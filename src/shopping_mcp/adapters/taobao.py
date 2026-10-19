"""淘宝联盟 / 淘宝客 (TOP gateway) adapter.

Gateway : https://eco.taobao.com/router/rest
Auth    : app_key + app_secret + session (淘宝客授权 token)
Signing : MD5(secret + sorted(k+v) + secret), uppercase

Interfaces used
---------------
  taobao.tbk.dg.material.optional.upgrade   关键词物料搜索（权限包 27939「商品物料获取」）
  taobao.tbk.item.info.get                  商品详情（权限包 16189「物料信息查询」）
  taobao.tbk.tpwd.create                    淘口令生成（权限包 11655「淘口令生成」）

Why `.upgrade` and not the classic method
-----------------------------------------
`taobao.tbk.dg.material.optional` still answers `isv.permission-api-package-limit`
even with pack 27939 granted. The upgraded variant is the one that pack actually
opens, and it returns a different (nested) shape plus a ready-made promotion
`click_url`, so search and link-generation happen in a single call.

`taobao.tbk.privilege.get` is NOT granted (no 转链 pack), so convert_link falls
back to 淘口令 (`tpwd.create`), which needs any URL rather than an item id.

Notes
-----
* `adzone_id` / `site_id` are parsed out of the PID, whose canonical shape is
  `mm_{memberId}_{siteId}_{adzoneId}`.
* The upgrade payload reports `publish_info.income_rate` as a **percentage**
  already (0.9 means 0.9%), verified against `commission_amount / price`.
  `publish_info.income_info.commission_rate` is that same value x100 and is only
  used as a fallback.
* `item_id` in this payload is an opaque token, not the numeric num_iid.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .. import config
from ..http import request_json
from ..models import PlatformError, Product, PromotionLink, to_float, to_int
from ..signing import sign_md5
from .base import Adapter

GATEWAY = "https://eco.taobao.com/router/rest"

SEARCH_METHOD = "taobao.tbk.dg.material.optional.upgrade"
INFO_METHOD = "taobao.tbk.item.info.get"
TPWD_METHOD = "taobao.tbk.tpwd.create"

_SALES_RE = re.compile(r"([\d.]+)\s*(万|亿)?")
_SHORT_URL_RE = re.compile(r"(https?://[^\s%？，,]+)")


def _parse_pid(pid: str) -> tuple[str, str]:
    """Return (site_id, adzone_id) from a Taobao PID."""
    parts = pid.split("_")
    if len(parts) >= 4 and parts[0] == "mm":
        return parts[2], parts[3]
    raise PlatformError(
        "taobao",
        f"TAOBAO_PID looks wrong: {pid!r}; expected mm_<memberId>_<siteId>_<adzoneId>",
    )


def _normalize_rate(value: Any) -> float | None:
    """Normalize a commission rate that may be a fraction / percent / bps."""
    if value is None or value == "":
        return None
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    if rate <= 0:
        return None
    if rate <= 1:                # fraction, e.g. 0.155
        return round(rate * 100, 2)
    if rate <= 100:              # already percent, e.g. 15.5
        return round(rate, 2)
    return round(rate / 100, 2)  # basis points, e.g. 1550


def _parse_sales_tip(text: Any) -> int | None:
    """`tk_total_sales` arrives as a display string like "1万+" / "1000"."""
    if text is None or text == "":
        return None
    m = _SALES_RE.search(str(text))
    if not m:
        return None
    try:
        value = float(m.group(1))
    except (TypeError, ValueError):
        return None
    unit = m.group(2)
    if unit == "万":
        value *= 10_000
    elif unit == "亿":
        value *= 100_000_000
    return int(value)


def _absolute(url: Any) -> str | None:
    """Taobao returns scheme-relative URLs (`//s.click.taobao.com/...`)."""
    if not url:
        return None
    s = str(url).strip()
    if s.startswith("//"):
        return "https:" + s
    return s


class TaobaoAdapter(Adapter):
    platform = "taobao"
    mode = "live"

    def __init__(self) -> None:
        self.cfg = config.load_platforms()["taobao"]
        if not self.cfg.ready:
            raise PlatformError(
                "taobao",
                "credentials incomplete: " + ", ".join(self.cfg.missing),
            )
        self.app_key = self.cfg.require("TAOBAO_APP_KEY")
        self.session = self.cfg.require("TAOBAO_SESSION")
        self.site_id, self.adzone_id = _parse_pid(self.cfg.require("TAOBAO_PID"))
        # item_id -> 已带 PID 追踪的推广链接，由 search() 填充。
        # 淘口令接口只接受规范商品链接，不接受 uland 落地页，所以这里复用搜索时
        # 已经生成的 click_url，避免再解析一次。
        self._link_cache: dict[str, str] = {}

    # ------------------------------------------------------------------ core
    def _call(self, method: str, business: dict[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "method": method,
            "app_key": self.app_key,
            "session": self.session,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "format": "json",
            "v": "2.0",
            "sign_method": "md5",
        }
        params.update(business)
        params["sign"] = sign_md5(self.cfg.secret, params)
        payload = request_json("POST", GATEWAY, data=params)

        if "error_response" in payload:
            err = payload["error_response"]
            raise PlatformError(
                "taobao",
                f"{err.get('sub_msg') or err.get('msg') or 'unknown error'} "
                f"(method={method})",
                code=str(err.get("sub_code") or err.get("code") or ""),
                detail=err,
            )

        # taobao.tbk.dg.material.optional.upgrade -> tbk_dg_material_optional_upgrade_response
        response_key = method.replace("taobao.", "").replace(".", "_") + "_response"
        wrapper = payload.get(response_key)
        if wrapper is None:
            raise PlatformError("taobao", f"unexpected response shape (method={method})", detail=payload)
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
        business: dict[str, Any] = {
            "adzone_id": self.adzone_id,
            "q": keyword,
            "page_no": page,
            "page_size": min(max(page_size, 1), 100),
        }
        if has_coupon:
            business["has_coupon"] = "true"
        if sort and sort != "default":
            business["sort"] = sort

        wrapper = self._call(SEARCH_METHOD, business)
        result_list = (wrapper.get("result_list") or {}).get("map_data") or []
        products = [self._product_from_search(item) for item in result_list]
        for product in products:
            link = product.item_url or product.promotion_url
            if product.item_id and link:
                self._link_cache[product.item_id] = link
        return products

    def detail(self, item_id: str) -> Product:
        wrapper = self._call(INFO_METHOD, {"num_iids": item_id, "platform": 2})
        results = (wrapper.get("results") or {}).get("n_tbk_item") or []
        if not results:
            raise PlatformError("taobao", f"item not found: {item_id}")
        return self._product_from_info(results[0])

    def convert_link(self, item: str, *, sub_pid: str | None = None) -> PromotionLink:
        """Turn an item id or any Taobao URL into a 淘口令 + short link.

        `taobao.tbk.privilege.get` is not granted to this app, so this uses the
        淘口令接口 instead: it accepts a URL and returns a tracked short link
        plus a share password.
        """
        url = self._link_cache.get(str(item))
        if not url:
            url = item if str(item).startswith("http") else self._url_for(item)
        if sub_pid:
            url = f"{url}&relationId={sub_pid}" if "?" in url else f"{url}?relationId={sub_pid}"

        wrapper = self._call(TPWD_METHOD, {"url": url})
        data = wrapper.get("data") or {}
        model = str(data.get("model") or "")
        short = _SHORT_URL_RE.search(model)
        promotion_url = short.group(1) if short else None
        if not promotion_url:
            raise PlatformError(
                "taobao",
                f"no promotion url returned for {item}",
                detail=wrapper,
            )
        return PromotionLink(
            platform="taobao",
            item_id=str(item),
            promotion_url=promotion_url,
            coupon_click_url=None,
            coupon_amount=None,
            raw={"model": model, "password": data.get("password_simple"), "source_url": url},
        )

    # ---------------------------------------------------------------- internal
    def _url_for(self, item_id: str) -> str:
        """Resolve an opaque item id to a shareable product URL."""
        try:
            wrapper = self._call(INFO_METHOD, {"num_iids": item_id, "platform": 2})
            rows = (wrapper.get("results") or {}).get("n_tbk_item") or []
            if rows:
                url = rows[0].get("item_url")
                if url:
                    return str(url)
        except PlatformError:
            pass
        return f"https://item.taobao.com/item.htm?id={item_id}"

    # ---------------------------------------------------------------- mapping
    def _product_from_search(self, item: dict[str, Any]) -> Product:
        """Map the nested `.upgrade` search shape."""
        basic = item.get("item_basic_info") or {}
        promo = item.get("price_promotion_info") or {}
        publish = item.get("publish_info") or {}
        income = publish.get("income_info") or {}

        final_price = to_float(promo.get("final_promotion_price"))
        zk_price = to_float(promo.get("zk_final_price"))
        reserve = to_float(promo.get("reserve_price"))

        coupon = None
        if final_price is not None and zk_price is not None and zk_price > final_price:
            coupon = round(zk_price - final_price, 2)

        price = final_price if final_price is not None else zk_price

        # income_rate is already a percentage (0.9 => 0.9%)
        rate = to_float(publish.get("income_rate"))
        if rate is None:
            raw = to_float(income.get("commission_rate"))
            rate = round(raw / 100, 2) if raw is not None else None

        amount = to_float(income.get("commission_amount"))
        if amount is None and price is not None and rate is not None:
            amount = round(price * rate / 100, 2)

        return Product(
            platform="taobao",
            item_id=str(item.get("item_id") or ""),
            title=str(basic.get("title") or ""),
            price=price,
            original_price=reserve or zk_price,
            coupon_amount=coupon,
            commission_rate=rate,
            commission_amount=amount,
            sales=_parse_sales_tip(basic.get("tk_total_sales")),
            shop_name=basic.get("shop_title") or None,
            item_url=_absolute(publish.get("click_url")),
            promotion_url=_absolute(publish.get("coupon_share_url") or publish.get("click_url")),
            image_url=basic.get("pict_url"),
            raw=item,
        )

    def _product_from_info(self, item: dict[str, Any]) -> Product:
        """Map the flat `item.info.get` detail shape."""
        zk = to_float(item.get("zk_final_price"))
        return Product(
            platform="taobao",
            item_id=str(item.get("num_iid") or item.get("input_num_iid") or ""),
            title=str(item.get("title") or ""),
            price=zk,
            original_price=to_float(item.get("reserve_price")) or zk,
            coupon_amount=None,
            commission_rate=None,
            commission_amount=None,
            sales=None,
            shop_name=item.get("nick") or None,
            item_url=_absolute(item.get("item_url")),
            promotion_url=None,
            image_url=item.get("pict_url"),
            raw=item,
        )
