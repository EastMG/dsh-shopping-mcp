"""淘宝联盟 / 淘宝客 (TOP gateway) adapter.

Gateway : https://eco.taobao.com/router/rest
Auth    : app_key + app_secret + session (淘宝客授权 token)
Signing : MD5(secret + sorted(k+v) + secret), uppercase

Interfaces used
---------------
  taobao.tbk.dg.material.optional   关键词物料搜索
  taobao.tbk.privilege.get          高效转链（取推广链接 + 券）
  taobao.tbk.item.info.get          商品详情

Notes
-----
* `adzone_id` / `site_id` are parsed out of the PID, whose canonical shape is
  `mm_{memberId}_{siteId}_{adzoneId}`.
* `commission_rate` has shipped in more than one unit over the years. The
  normalizer below handles fraction / percent / basis-point forms and keeps the
  raw payload so nothing is lost.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .. import config
from ..http import request_json
from ..models import PlatformError, Product, PromotionLink, to_float, to_int
from ..signing import sign_md5
from .base import Adapter

GATEWAY = "https://eco.taobao.com/router/rest"


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
    """Normalize commission_rate across fraction / percent / basis-point forms.

    Deliberately does NOT go through `to_float`: that helper rounds to 2 decimals
    for money, which would silently turn a fraction like 0.155 into 0.15 and
    report 15.0% instead of 15.5%.
    """
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

        # key convention: taobao.tbk.dg.material.optional -> tbk_dg_material_optional_response
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

        wrapper = self._call("taobao.tbk.dg.material.optional", business)
        result_list = (wrapper.get("result_list") or {}).get("map_data") or []
        return [self._product(item) for item in result_list]

    def detail(self, item_id: str) -> Product:
        wrapper = self._call(
            "taobao.tbk.item.info.get",
            {"num_iids": item_id, "platform": 2},
        )
        results = (wrapper.get("results") or {}).get("n_tbk_item") or []
        if not results:
            raise PlatformError("taobao", f"item not found: {item_id}")
        return self._product(results[0])

    def convert_link(self, item: str, *, sub_pid: str | None = None) -> PromotionLink:
        business: dict[str, Any] = {
            "adzone_id": self.adzone_id,
            "site_id": self.site_id,
            "platform": 2,
            "item_id": item,
        }
        if sub_pid:
            business["relation_id"] = sub_pid
        wrapper = self._call("taobao.tbk.privilege.get", business)
        result = wrapper.get("result") or {}
        url = result.get("coupon_click_url") or result.get("click_url")
        if not url:
            raise PlatformError(
                "taobao",
                f"no promotion url returned for {item}",
                detail=result,
            )
        return PromotionLink(
            platform="taobao",
            item_id=item,
            promotion_url=url,
            coupon_click_url=result.get("coupon_click_url"),
            coupon_amount=to_float(result.get("coupon_amount")),
            raw=result,
        )

    # ---------------------------------------------------------------- mapping
    def _product(self, item: dict[str, Any]) -> Product:
        zk = to_float(item.get("zk_final_price"))
        coupon = to_float(item.get("coupon_amount"))
        price = round(zk - coupon, 2) if (zk is not None and coupon) else zk
        rate = _normalize_rate(item.get("commission_rate"))
        return Product(
            platform="taobao",
            item_id=str(item.get("num_iid") or item.get("item_id") or ""),
            title=str(item.get("title") or ""),
            price=price,
            original_price=to_float(item.get("reserve_price")) or zk,
            coupon_amount=coupon,
            commission_rate=rate,
            commission_amount=(
                round(price * rate / 100, 2) if (price is not None and rate is not None) else None
            ),
            sales=to_int(item.get("volume")),
            shop_name=item.get("shop_title") or item.get("nick"),
            item_url=item.get("item_url") or item.get("url"),
            promotion_url=item.get("coupon_share_url") or item.get("click_url"),
            image_url=item.get("pict_url"),
            raw=item,
        )
