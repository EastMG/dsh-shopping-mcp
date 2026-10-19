"""拼多多多多进宝 (DDK) adapter.

Gateway : https://gw-api.pinduoduo.com/api/router
Auth    : client_id + client_secret + pid
Signing : MD5(secret + sorted(k+v) + secret), uppercase

Interfaces used
---------------
  pdd.ddk.goods.search                    商品搜索
  pdd.ddk.goods.detail                    商品详情
  pdd.ddk.goods.promotion.url.generate    推广链接生成

Unit traps (these are the usual source of "price looks 100x too big"):
  * all money fields are in **分** (cents)
  * `promotion_rate` / `predict_promotion_rate` are **千分比** (per mille, 100 == 10%)
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from .. import config
from ..http import request_json
from ..models import PlatformError, Product, PromotionLink, to_float, to_int
from ..signing import sign_md5
from .base import Adapter

GATEWAY = "https://gw-api.pinduoduo.com/api/router"


def _cents(value: Any) -> float | None:
    amount = to_float(value)
    return round(amount / 100, 2) if amount is not None else None


def _per_mille(value: Any) -> float | None:
    """千分比 -> percent."""
    rate = to_float(value)
    return round(rate / 10, 2) if rate is not None else None


def _parse_sales_tip(value: Any) -> int | None:
    """PDD gives sales as a human string: '7万+' -> 70000, '1.2万+' -> 12000.

    Verified against live data: the field is `sales_tip`, a String, not a count.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    multiplier = 10000 if "万" in text else 1
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    return int(float(match.group(0)) * multiplier)


def _looks_like_goods_sign(value: str) -> bool:
    """goods_sign is a long opaque token; goods_id is pure digits."""
    return bool(value) and not value.isdigit() and len(value) >= 20


def _find_url(node: Any) -> str | None:
    """Dig the first http(s) url out of an arbitrarily shaped response."""
    if isinstance(node, str):
        return node if node.startswith("http") else None
    if isinstance(node, dict):
        for key in ("url", "mobile_url", "short_url", "mobile_short_url"):
            value = node.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        for value in node.values():
            found = _find_url(value)
            if found:
                return found
    if isinstance(node, list):
        for value in node:
            found = _find_url(value)
            if found:
                return found
    return None


# ---------------------------------------------------------------- throttling
#
# 拼多多的规则：**调取商品接口多、实际出单少 → 限用**（40009
# 「当前duoId因大量调取商品接口但出单较少被限制使用」）。
#
# 这个限制是账号级的，一旦触发所有搜索都会失败，而且继续调用只会加重。
# 所以这里做四道闸：
#   1. 搜索结果缓存（同一个关键词几分钟内不重复请求）
#   2. 最小请求间隔（默认 3 秒一次）
#   3. 每日调用上限（默认 150 次，跨进程持久化到 .pdd_quota.json）
#   4. 撞到 40009 后主动冷却（默认 30 分钟），期间直接失败不再打网络
#
# 四个参数都能用环境变量覆盖：
#   PDD_MIN_INTERVAL / PDD_CACHE_TTL / PDD_DAILY_BUDGET / PDD_COOLDOWN

_ROOT = Path(__file__).resolve().parents[3]
_STATE_FILE = _ROOT / ".pdd_quota.json"


def _env_num(name: str, default: float) -> float:
    try:
        return float(__import__("os").environ.get(name, default))
    except (TypeError, ValueError):
        return default


_MIN_INTERVAL = _env_num("PDD_MIN_INTERVAL", 3.0)
_CACHE_TTL = _env_num("PDD_CACHE_TTL", 900.0)
_DAILY_BUDGET = int(_env_num("PDD_DAILY_BUDGET", 150))
_COOLDOWN = _env_num("PDD_COOLDOWN", 1800.0)

_last_call_mono = 0.0
_search_cache: dict[tuple[Any, ...], tuple[float, list[Product]]] = {}


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 文件不存在/不可读都按空状态处理
        return {}


def _save_state(state: dict[str, Any]) -> None:
    try:
        _STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except Exception:  # noqa: BLE001 - 沙箱只读时退化为纯内存计数
        pass


def _throttle() -> None:
    """请求前置检查：冷却 / 日配额 / 最小间隔。"""
    global _last_call_mono
    import time as _t

    now = _t.time()
    state = _load_state()
    today = _t.strftime("%Y-%m-%d", _t.localtime(now))
    if state.get("day") != today:
        state = {"day": today, "count": 0, "cooldown_until": state.get("cooldown_until", 0)}

    cooldown_until = float(state.get("cooldown_until") or 0)
    if cooldown_until > now:
        mins = int((cooldown_until - now) / 60) + 1
        raise PlatformError(
            "pdd",
            f"拼多多调用主动冷却中（上次撞到 40009 限流），约需 {mins} 分钟恢复。"
            "这段时间请不要重复调用，越调限制越久。",
            code="client.cooldown",
        )

    if int(state.get("count") or 0) >= _DAILY_BUDGET:
        raise PlatformError(
            "pdd",
            f"今日拼多多调用已达自设上限 {_DAILY_BUDGET} 次，避免触发 40009 限流。"
            "明天自动重置；需要更多可调大 PDD_DAILY_BUDGET。",
            code="client.budget",
        )

    wait = _MIN_INTERVAL - (_t.monotonic() - _last_call_mono)
    if wait > 0:
        _t.sleep(wait)

    state["day"] = today
    state["count"] = int(state.get("count") or 0) + 1
    _save_state(state)
    _last_call_mono = _t.monotonic()


def _enter_cooldown() -> None:
    """撞到 40009 后写冷却时间，后续调用直接失败，不再打网络。"""
    import time as _t

    state = _load_state()
    today = _t.strftime("%Y-%m-%d", _t.localtime())
    state.setdefault("day", today)
    state["cooldown_until"] = _t.time() + _COOLDOWN
    _save_state(state)
    print(
        f"[pdd] 触发限流 40009，已进入 {int(_COOLDOWN / 60)} 分钟冷却期",
        file=sys.stderr,
    )


class PddAdapter(Adapter):
    platform = "pdd"
    mode = "live"

    def __init__(self) -> None:
        self.cfg = config.load_platforms()["pdd"]
        if not self.cfg.ready:
            raise PlatformError("pdd", "credentials incomplete: " + ", ".join(self.cfg.missing))
        self.client_id = self.cfg.require("PDD_CLIENT_ID")
        self.pid = self.cfg.require("PDD_PID")

    # ------------------------------------------------------------------ core
    def _call(self, method: str, business: dict[str, Any]) -> dict[str, Any]:
        _throttle()  # 冷却 / 日配额 / 最小间隔
        params: dict[str, Any] = {
            "type": method,
            "client_id": self.client_id,
            "timestamp": str(int(time.time())),
            "data_type": "JSON",
        }
        params.update({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
                       for k, v in business.items()})
        params["sign"] = sign_md5(self.cfg.secret, params)
        payload = request_json("POST", GATEWAY, data=params)

        if "error_response" in payload:
            err = payload["error_response"]
            sub_code = str(err.get("sub_code") or "")
            # 40009 = 调取多、出单少 被限用。一旦出现就进冷却，避免继续加重。
            if sub_code == "40009" or "出单较少" in str(err.get("sub_msg") or ""):
                _enter_cooldown()
            raise PlatformError(
                "pdd",
                f"{err.get('error_msg') or err.get('sub_msg') or 'unknown error'} (method={method})",
                code=str(err.get("error_code") or err.get("sub_code") or ""),
                detail=err,
            )
        response_key = method.replace("pdd.", "").replace(".", "_") + "_response"
        wrapper = payload.get(response_key)
        if wrapper is None:
            # PDD does not always mirror the method name in the response key:
            # pdd.ddk.member.authority.query -> authority_query_response.
            # Fall back to the single *_response key when it is unambiguous.
            candidates = [k for k in payload if k.endswith("_response")]
            if len(candidates) == 1:
                wrapper = payload[candidates[0]]
        if wrapper is None:
            raise PlatformError("pdd", f"unexpected response shape (method={method})", detail=payload)
        return wrapper

    # ------------------------------------------------------------------ tools
    def search(
        self,
        keyword: str,
        *,
        page: int = 1,
        page_size: int = 10,
        has_coupon: bool = False,
        sort: str = "default",
    ) -> list[Product]:
        sort_map = {
            "default": 0,
            "commission": 1,
            "sales": 2,
            "price_asc": 3,
            "price_desc": 4,
            "coupon_price": 9,
        }
        business: dict[str, Any] = {
            "keyword": keyword,
            "page": page,
            # PDD rejects page_size outside 10-100 with code 50001 /
            # sub_code 3000000 ("pageSize的取值范围是10-100"). Clamp, don't pass through.
            "page_size": min(max(page_size, 10), 100),
            "pid": self.pid,
            "sort_type": sort_map.get(sort, 0),
        }
        if has_coupon:
            business["with_coupon"] = "true"

        # 结果缓存：比价脚本会反复问同样的关键词，缓存能砍掉绝大部分调用
        cache_key = (keyword, page, business["page_size"], has_coupon, sort)
        hit = _search_cache.get(cache_key)
        if hit is not None and (time.time() - hit[0]) < _CACHE_TTL:
            print(f"[pdd] 命中缓存 {keyword!r} p{page}", file=sys.stderr)
            return hit[1]

        wrapper = self._call("pdd.ddk.goods.search", business)
        rows = wrapper.get("goods_list") or []
        if not rows:
            # Live behaviour on a fresh app: the same query can return 0 rows and
            # then 3 rows a second later (throttling). Retry once so the caller
            # does not have to know that and report "no products" wrongly.
            time.sleep(0.8)
            wrapper = self._call("pdd.ddk.goods.search", business)
            rows = wrapper.get("goods_list") or []
        products = [self._product(item) for item in rows]
        if products:  # 只缓存成功结果，空结果可能是瞬时抖动
            _search_cache[cache_key] = (time.time(), products)
        return products

    def detail(self, item: str) -> Product:
        """按 goods_sign 查详情。

        实测（2026-10）：pdd.ddk.goods.detail 只认**单数** `goods_sign`；
        传 `goods_id_list` 报 40004（已下线），传 `goods_sign_list` 报 10002
        （goodsId与goodsSign为空），传数字 goods_id 同样报 10002。
        没有 goods_id -> goods_sign 的接口，所以数字 id 只能明确拒绝。
        """
        value = str(item).strip()
        if not _looks_like_goods_sign(value):
            raise PlatformError(
                "pdd",
                f"拼多多已停用 goods_id 入参（{value}）。请改用搜索结果里的 "
                "extra.goods_sign（一长串字母数字 token）来查详情或转链。",
            )
        wrapper = self._call("pdd.ddk.goods.detail", {"goods_sign": value})
        details = wrapper.get("goods_details") or []
        if not details:
            raise PlatformError("pdd", f"item not found: {value}", detail=wrapper)
        return self._product(details[0])

    def convert_link(self, item: str, *, sub_pid: str | None = None) -> PromotionLink:
        """商品 -> 推广链接。

        实测（2026-10）：`goods_id_list` 已下线，传了会返回
        sub_code 40005「goods_id_list已下线，请使用goods_sign_list代替」。
        所以这里接受两种输入：
          * goods_sign（搜索/详情返回的那串不透明 token）—— 直接用
          * 纯数字 goods_id —— 先经 pdd.ddk.goods.detail 换成 goods_sign
        """
        value = str(item).strip()
        if not _looks_like_goods_sign(value):
            raise PlatformError(
                "pdd",
                f"拼多多已停用 goods_id 入参（{value}）。请改用搜索结果里的 "
                "extra.goods_sign（一长串字母数字 token）来转链。",
            )
        sign = value

        business: dict[str, Any] = {
            "p_id": self.pid,
            "goods_sign_list": [sign],
            "generate_short_url": True,
            "generate_authority_url": True,
        }
        if sub_pid:
            business["custom_parameters"] = sub_pid
        wrapper = self._call("pdd.ddk.goods.promotion.url.generate", business)
        links = wrapper.get("goods_promotion_url_list") or []
        if not links:
            raise PlatformError("pdd", f"no promotion url returned for {item}", detail=wrapper)
        link = links[0]
        url = link.get("mobile_url") or link.get("url")
        if not url:
            raise PlatformError("pdd", "promotion url list had no usable url", detail=link)
        return PromotionLink(
            platform="pdd",
            item_id=str(item),
            promotion_url=url,
            short_url=link.get("mobile_short_url") or link.get("short_url"),
            raw=link,
        )

    # -------------------------------------------------- 授权备案 (authority)
    # PDD gates 商品搜索/转链 behind a per-推手 "授权备案": the account that
    # earns commission must open an authorization link once and confirm.
    # Without it every call returns sub_code 60001
    # ("未传入已经授权备案过的相关参数"). Docs: qa-system?questionId=218
    def authority_status(self) -> dict[str, Any]:
        """pdd.ddk.member.authority.query — bind == 1 means 备案 done."""
        last: PlatformError | None = None
        for key in ("pid", "p_id"):
            try:
                return self._call("pdd.ddk.member.authority.query", {key: self.pid})
            except PlatformError as exc:
                last = exc
                if "参数" not in str(exc):
                    raise
        raise PlatformError("pdd", "member.authority.query rejected both pid and p_id", detail=str(last))

    def authority_url(self, custom_parameters: str | None = None) -> str:
        """pdd.ddk.rp.prom.url.generate with channel_type=10 -> 授权备案链接.

        This endpoint wants `p_id_list` (an array), not the `p_id` scalar that
        most other ddk endpoints take.
        """
        business: dict[str, Any] = {"p_id_list": [self.pid], "channel_type": 10}
        if custom_parameters:
            business["custom_parameters"] = custom_parameters
        wrapper = self._call("pdd.ddk.rp.prom.url.generate", business)
        url = _find_url(wrapper)
        if not url:
            raise PlatformError("pdd", "no authority url in response", detail=wrapper)
        return url

    # ---------------------------------------------------------------- mapping
    def _product(self, item: dict[str, Any]) -> Product:
        group_price = _cents(item.get("min_group_price"))
        coupon = _cents(item.get("coupon_discount"))
        normal_price = _cents(item.get("min_normal_price"))
        final_price = (
            round(group_price - coupon, 2)
            if (group_price is not None and coupon)
            else group_price
        )
        rate = _per_mille(item.get("promotion_rate"))
        if rate is None:
            rate = _per_mille(item.get("predict_promotion_rate"))

        return Product(
            platform="pdd",
            item_id=str(item.get("goods_id") or ""),
            title=str(item.get("goods_name") or ""),
            price=final_price,
            original_price=normal_price or group_price,
            coupon_amount=coupon,
            commission_rate=rate,
            commission_amount=(
                round(final_price * rate / 100, 2)
                if (final_price is not None and rate is not None)
                else None
            ),
            sales=_parse_sales_tip(item.get("sales_tip")) or to_int(item.get("sold_quantity")),
            # 实测：goods.search 返回的 mall_name 常为空字符串，店铺名拿不到就是拿不到，
            # 不要用 brand_name 冒充店铺名。品牌名在 raw.brand_name 里。
            shop_name=item.get("mall_name") or None,
            item_url=f"https://mobile.yangkeduo.com/goods.html?goods_id={item.get('goods_id')}",
            # 推广链接要转链后才有，不能拿图片地址顶替（这里曾经是个 bug）
            promotion_url=None,
            image_url=item.get("goods_image_url") or item.get("goods_thumbnail_url"),
            # goods_sign is what convert_link needs now (goods_id_list is retired)
            extra={"goods_sign": item.get("goods_sign")},
            raw=item,
        )
