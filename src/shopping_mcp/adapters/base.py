"""Adapter interface every platform implements."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Product, PromotionLink


class Adapter(ABC):
    """Uniform surface over one platform's affiliate API."""

    platform: str = ""
    mode: str = "live"

    @abstractmethod
    def search(
        self,
        keyword: str,
        *,
        page: int = 1,
        page_size: int = 10,
        has_coupon: bool = False,
        sort: str = "default",
    ) -> list[Product]:
        """Keyword search returning normalized products."""

    @abstractmethod
    def detail(self, item_id: str) -> Product:
        """Product detail by platform item id."""

    @abstractmethod
    def convert_link(self, item: str, *, sub_pid: str | None = None) -> PromotionLink:
        """Turn an item id / landing URL into an affiliate promotion link."""
