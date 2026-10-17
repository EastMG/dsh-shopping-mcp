"""Signing helpers.

All three platforms happen to use the same construction:

    sign = MD5( secret + concat(sorted(k + v)) + secret ).upper()

The differences are only which secret is used and which params participate:

  * Taobao (TOP)      : app_secret,     all params except `sign`
  * JD   (routerjson) : app_secret,     all params except `sign`
                        (business payload travels inside `360buy_param_json`)
  * Pinduoduo (ddk)   : client_secret,  all params except `sign`

Keeping one implementation here means a fix applies to all platforms at once.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

# Params that must never enter the signature material.
_EXCLUDED = {"sign"}


def _normalize(value: Any) -> str:
    """Render a param value the way the gateways expect it."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def signature_material(secret: str, params: Mapping[str, Any]) -> str:
    items = sorted(
        (str(k), _normalize(v))
        for k, v in params.items()
        if k not in _EXCLUDED and v is not None
    )
    return secret + "".join(f"{k}{v}" for k, v in items) + secret


def sign_md5(secret: str, params: Mapping[str, Any]) -> str:
    raw = signature_material(secret, params)
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()
