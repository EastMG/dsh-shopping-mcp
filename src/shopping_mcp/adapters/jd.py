"""京东联盟 (JD union open platform) adapter.

Gateway : https://api.jd.com/routerjson
Auth    : app_key + app_secret (+ siteId / positionId for link conversion)
Signing : MD5(secret + sorted(k+v) + secret), uppercase
Business payload travels as a JSON string inside `360buy_param_json`.

Interfaces used
---------------
  jd.union.open.goods.query              商品查询
  jd.union.open.promotion.common.get     通用转链
  jd.union.open.goods.bigfield.query     商品详情（大字段）

Note: the gateway returns the business result as a JSON *string* inside
`<method>_response.result`, so it needs a second parse.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .. import config
from ..http import request_json
from ..models import PlatformError, Product, PromotionLink, to_float, to_int
from ..signing import sign_md5
from .base import Adapter

GATEWAY = "https://api.jd.com/routerjson"
ITEM_URL = "https://item.jd.com/{sku}.html"


def _normalize_rate(value: Any) -> float | None:
    """See taobao._normalize_rate: bypass to_float to keep fraction precision."""
    if value is None or value == "":
        return None
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    if rate <= 0:
        return None
    if rate <= 1:
        return round(rate * 100, 2)
    if rate <= 100:
        return round(rate, 2)
    return round(rate / 100, 2)


class JdAdapter(Adapter):
    platform = "jd"
    mode = "live"

    def __init__(self) -> None:
        self.cfg = config.load_platforms()["jd"]
        if not self.cfg.ready:
            raise PlatformError("jd", "credentials incomplete: " + ", ".join(self.cfg.missing))
        self.app_key = self.cfg.require("JD_APP_KEY")
        self.site_id = self.cfg.require("JD_SITE_ID")
        self.position_id = self.cfg.require("JD_POSITION_ID")

    # ------------------------------------------------------------------ core
    def _call(self, method: str, business: dict[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "method": method,
            "app_key": self.app_key,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "format": "json",
            "v": "1.0",
            "sign_method": "md5",
            "360buy_param_json": json.dumps(business, separators=(",", ":"), ensure_ascii=False),
        }
        params["sign"] = sign_md5(self.cfg.secret, params)
        payload = request_json("POST", GATEWAY, data=params)

        response_key = method.replace(".", "_") + "_response"
        wrapper = payload.get(response_key)
        if wrapper is None:
            # JD reports errors under a nested `error_response` key.
            err = payload.get("error_response") or {}
            raise PlatformError(
                "jd",
                f"{err.get('zh_desc') or err.get('en_desc') or 'unexpected response'} (method={method})",
                code=str(err.get("code") or ""),
                detail=payload,
            )
        if wrapper.get("code") not in (None, "", "0", 0):
            raise PlatformError(
                "jd",
                f"{wrapper.get('message') or wrapper.get('zh_desc') or 'call failed'} (method={method})",
                code=str(wrapper.get("code")),
                detail=wrapper,
            )
        raw_result = wrapper.get("result")
        if raw_result in (None, "", "null"):
            return {}
        if isinstance(raw_result, str):
            try:
                return json.loads(raw_result)
            except ValueError as exc:
                raise PlatformError("jd", f"result is not JSON (method={method})", detail=raw_result) from exc
        return raw_result if isinstance(raw_result, dict) else {"data": raw_result}

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
        goods_req: dict[str, Any] = {
            "keyword": keyword,
            "pageIndex": page,
            "pageSize": min(max(page_size, 1), 50),
        }
        if has_coupon:
            goods_req["isCoupon"] = 1
        if sort and sort != "default":
            goods_req["sortName"] = sort

        result = self._call("jd.union.open.goods.query", {"goodsReqDTO": goods_req})
        items = result.get("data") or []
        return [self._product(item) for item in items]

    def detail(self, item_id: str) -> Product:
        result = self._call(
            "jd.union.open.goods.bigfield.query",
            {"goodsReq": {"skuIds": [int(item_id)] if str(item_id).isdigit() else [item_id]}},
        )
        items = result.get("data") or []
        if not items:
            raise PlatformError("jd", f"item not found: {item_id}")
        return self._product(items[0])

    def convert_link(self, item: str, *, sub_pid: str | None = None) -> PromotionLink:
        material = item if str(item).startswith("http") else ITEM_URL.format(sku=item)
        req: dict[str, Any] = {
            "materialId": material,
            "siteId": self.site_id,
            "positionId": self.position_id,
        }
        if sub_pid:
            req["subUnionId"] = sub_pid
        result = self._call("jd.union.open.promotion.common.get", {"promotionCodeReq": req})
        url = result.get("clickURL") or result.get("shortURL")
        if not url:
            raise PlatformError("jd", f"no promotion url returned for {item}", detail=result)
        return PromotionLink(
            platform="jd",
            item_id=str(item),
            promotion_url=url,
            short_url=result.get("shortURL"),
            raw=result,
        )

    # ---------------------------------------------------------------- mapping
    def _product(self, item: dict[str, Any]) -> Product:
        price_info = item.get("priceInfo") or {}
        coupon_info = item.get("couponInfo") or {}
        coupon_list = coupon_info.get("couponList") or []
        coupon = None
        if coupon_list:
            coupon = to_float(coupon_list[0].get("discount")) or to_float(coupon_list[0].get("quota"))
        commission_info = item.get("commissionInfo") or {}
        shop_info = item.get("shopInfo") or {}
        image_info = item.get("imageInfo") or {}
        image_list = image_info.get("imageList") or []

        final_price = to_float(price_info.get("lowestCouponPrice")) or to_float(price_info.get("lowestPrice"))
        rate = _normalize_rate(commission_info.get("commissionRate"))
        commission = to_float(commission_info.get("commission"))

        return Product(
            platform="jd",
            item_id=str(item.get("skuId") or ""),
            title=str(item.get("skuName") or ""),
            price=final_price,
            original_price=to_float(price_info.get("price")),
            coupon_amount=coupon,
            commission_rate=rate,
            commission_amount=commission,
            sales=to_int(item.get("inOrderCount30Days")) or to_int(item.get("comments")),
            shop_name=shop_info.get("shopName"),
            item_url=item.get("materialUrl") or ITEM_URL.format(sku=item.get("skuId")),
            promotion_url=item.get("materialUrl"),
            image_url=(image_list[0].get("url") if image_list else None),
            raw=item,
        )
