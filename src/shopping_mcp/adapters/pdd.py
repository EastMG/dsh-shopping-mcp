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
import time
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

        wrapper = self._call("pdd.ddk.goods.search", business)
        return [self._product(item) for item in (wrapper.get("goods_list") or [])]

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
