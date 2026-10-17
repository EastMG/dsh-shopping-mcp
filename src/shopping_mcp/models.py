"""Normalized data shapes shared by every adapter.

The whole point of this layer: the agent should never have to know that
Taobao returns `num_iid`/`zk_final_price` while Pinduoduo returns
`goods_id`/`min_group_price`. Each adapter maps into one shape.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class PlatformError(RuntimeError):
    """A platform call failed in a way worth showing the user."""

    def __init__(self, platform: str, message: str, *, code: str | None = None, detail: Any = None):
        self.platform = platform
        self.code = code
        self.detail = detail
        super().__init__(f"[{platform}] {message}")


@dataclass
class Product:
    platform: str
    item_id: str
    title: str
    price: float | None = None              # 券后价 / 到手价（若可得）
    original_price: float | None = None     # 原价 / 拼单价
    coupon_amount: float | None = None      # 优惠券面额
    commission_rate: float | None = None    # 佣金比例 %
    commission_amount: float | None = None  # 预估佣金
    sales: int | None = None
    shop_name: str | None = None
    item_url: str | None = None
    promotion_url: str | None = None        # 推广链接（转链后才有）
    image_url: str | None = None
    # Platform-specific extras that are useful downstream but do not have a
    # cross-platform equivalent. Pinduoduo puts `goods_sign` here, which is the
    # token its link-conversion API now requires instead of goods_id.
    extra: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_raw:
            data.pop("raw", None)
        return data


@dataclass
class PromotionLink:
    platform: str
    item_id: str | None
    promotion_url: str
    short_url: str | None = None
    coupon_amount: float | None = None
    coupon_click_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_raw:
            data.pop("raw", None)
        return data


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
