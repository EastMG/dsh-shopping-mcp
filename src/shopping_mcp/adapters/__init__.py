"""Platform adapter registry."""

from __future__ import annotations

from .base import Adapter
from .jd import JdAdapter
from .pdd import PddAdapter
from .taobao import TaobaoAdapter

ADAPTERS: dict[str, type[Adapter]] = {
    "taobao": TaobaoAdapter,
    "jd": JdAdapter,
    "pdd": PddAdapter,
}


def get_adapter(platform: str, mock: bool = False):
    """Build an adapter for `platform`, or its mock counterpart if mock=True."""
    if mock:
        from ..mock import MockAdapter

        return MockAdapter(platform)
    try:
        factory = ADAPTERS[platform]
    except KeyError:
        raise ValueError(f"unknown platform {platform!r}; expected one of {sorted(ADAPTERS)}") from None
    return factory()
