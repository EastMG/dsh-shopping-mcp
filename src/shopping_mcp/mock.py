"""Deterministic mock adapter.

Why this exists: getting open-platform approval takes days, and none of the
tool wiring should be blocked on it. Every tool works against this adapter, so
the agent-side schema, the prompt, and your verification workflow are all
exercised before a single real credential exists.

Every payload produced here is tagged `"mock": true` and every URL lives under
`mock.local`, so a mock result can never be mistaken for a real price.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .models import Product, PromotionLink
from .adapters.base import Adapter

_TITLE_WORDS = [
    "无线蓝牙耳机", "机械键盘", "保温杯", "充电宝", "人体工学椅",
    "空气炸锅", "降噪耳机", "显示器支架", "台灯", "扫地机器人",
]
_SHOP_WORDS = ["官方旗舰店", "某某数码专营店", "优选工厂店", "百亿补贴店", "自营旗舰店"]


def _seed(*parts: str) -> int:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


class MockAdapter(Adapter):
    mode = "mock"

    def __init__(self, platform: str) -> None:
        if platform not in {"taobao", "jd", "pdd"}:
            raise ValueError(f"unknown platform {platform!r}")
        self.platform = platform

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
        count = min(max(page_size, 1), 20)
        products: list[Product] = []
        for index in range(count):
            seed = _seed(self.platform, keyword, str(page), str(index))
            products.append(self._fake_product(keyword, seed, index))
        if has_coupon:
            products = [p for p in products if p.coupon_amount]
        return products

    def detail(self, item_id: str) -> Product:
        seed = _seed(self.platform, "detail", item_id)
        product = self._fake_product("(detail)", seed, 0)
        product.item_id = str(item_id)
        return product

    def convert_link(self, item: str, *, sub_pid: str | None = None) -> PromotionLink:
        seed = _seed(self.platform, "link", item)
        suffix = f"&sub={sub_pid}" if sub_pid else ""
        return PromotionLink(
            platform=self.platform,
            item_id=str(item),
            promotion_url=(
                f"https://mock.local/{self.platform}/promotion/{item}"
                f"?token={seed:012x}{suffix}"
            ),
            short_url=f"https://mock.local/s/{seed:08x}",
            coupon_amount=round((seed % 5000) / 100, 2),
            raw={"mock": True, "note": "mock.local — 不是真实推广链接"},
        )

    # ---------------------------------------------------------------- fixtures
    def _fake_product(self, keyword: str, seed: int, index: int) -> Product:
        base = round(50 + (seed % 950) + index * 3.7, 2)
        coupon = round((seed % 80) + 5, 2) if seed % 3 else None
        final = round(base - coupon, 2) if coupon else base
        rate = round(1.5 + (seed % 180) / 10, 2)
        return Product(
            platform=self.platform,
            item_id=f"mock-{seed:012x}",
            title=f"[MOCK] {keyword} — {_TITLE_WORDS[(seed + index) % len(_TITLE_WORDS)]} {index + 1}号",
            price=final,
            original_price=base,
            coupon_amount=coupon,
            commission_rate=rate,
            commission_amount=round(final * rate / 100, 2),
            sales=seed % 50000,
            shop_name=_SHOP_WORDS[(seed + index) % len(_SHOP_WORDS)],
            item_url=f"https://mock.local/{self.platform}/item/mock-{seed:012x}",
            promotion_url=None,
            image_url=f"https://mock.local/{self.platform}/img/{seed:012x}.jpg",
            raw={"mock": True, "keyword": keyword, "note": "mock.local — 不是真实商品数据"},
        )
