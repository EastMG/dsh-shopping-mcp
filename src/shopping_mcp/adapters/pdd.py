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
            "page_size": min(max(page_size, 1), 100),
            "pid": self.pid,
            "sort_type": sort_map.get(sort, 0),
        }
        if has_coupon:
            business["with_coupon"] = "true"

        wrapper = self._call("pdd.ddk.goods.search", business)
        return [self._product(item) for item in (wrapper.get("goods_list") or [])]

    def detail(self, item_id: str) -> Product:
        wrapper = self._call("pdd.ddk.goods.detail", {"goods_id_list": [int(item_id)]})
        details = wrapper.get("goods_details") or []
        if not details:
            raise PlatformError("pdd", f"item not found: {item_id}")
        return self._product(details[0])

    def convert_link(self, item: str, *, sub_pid: str | None = None) -> PromotionLink:
        business: dict[str, Any] = {
            "p_id": self.pid,
            "goods_id_list": [int(item)],
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
            sales=to_int(item.get("sales_tip")) or to_int(item.get("sold_quantity")),
            shop_name=item.get("mall_name"),
            item_url=f"https://mobile.yangkeduo.com/goods.html?goods_id={item.get('goods_id')}",
            promotion_url=item.get("goods_image_url"),
            image_url=item.get("goods_image_url") or item.get("goods_thumbnail_url"),
            raw=item,
        )
