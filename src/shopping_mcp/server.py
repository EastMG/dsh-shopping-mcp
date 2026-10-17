"""MCP server exposing one unified shopping surface over three platforms.

Run:  uv run shopping-mcp          (stdio transport, for DSH / Claude / Cherry)

Tool design
-----------
One set of tools, platform chosen by argument. Four tools total, deliberately:

    shopping_status   what is configured, what is still missing, how to apply
    search_products   keyword search (optionally coupon-only)
    get_product       detail by platform item id
    convert_link      item id / URL -> affiliate promotion link

Every tool returns JSON. A platform without complete credentials automatically
answers in mock mode instead of failing, so the surface is testable on day one.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import config
from .adapters import get_adapter
from .http import HttpError
from .models import PlatformError

mcp = FastMCP("shopping")

VALID_SORTS = ("default", "commission", "sales", "price_asc", "price_desc", "coupon_price")


# --------------------------------------------------------------------- helpers
def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _resolve(platform: str):
    """Return (adapter, mode, note) honouring the global mock switch."""
    platform = (platform or "").strip().lower()
    if platform not in config.PLATFORMS:
        raise ValueError(f"platform 必须是 {config.PLATFORMS} 之一，收到 {platform!r}")

    force_mock = os.environ.get("SHOPPING_MCP_MOCK", "").strip() in {"1", "true", "yes"}
    mode = config.resolve_mode(platform, force_mock=force_mock)
    adapter = get_adapter(platform, mock=(mode == "mock"))

    note = None
    if mode == "mock":
        missing = config.load_platforms()[platform].missing
        note = (
            "当前为 mock 模式，返回的全是假数据（域名 mock.local），不是真实价格。"
            + (f" 缺失凭证: {', '.join(missing)}。" if missing else " 已由 SHOPPING_MCP_MOCK 强制。")
            + " 调用 shopping_status 查看申请指引。"
        )
    return adapter, mode, note


def _fail(platform: str, exc: Exception) -> str:
    if isinstance(exc, (PlatformError, HttpError)):
        return _dump({"ok": False, "platform": platform, "error": str(exc)})
    return _dump({"ok": False, "platform": platform, "error": f"{type(exc).__name__}: {exc}"})


# ----------------------------------------------------------------------- tools
@mcp.tool()
def shopping_status() -> str:
    """查看三个平台（taobao / jd / pdd）的凭证配置状态、当前运行模式与申请指引。

    返回每个平台的 mode（live=真实数据 / mock=假数据）、缺失的环境变量名、
    以及该平台该去哪里申请、需要什么资质。开始任何调用前先看这个。
    """
    try:
        env_file = config.load_dotenv()
        report = config.readiness_report()
        return _dump({
            "ok": True,
            "env_file": str(env_file) if env_file else None,
            "force_mock": os.environ.get("SHOPPING_MCP_MOCK", "") or None,
            "platforms": report,
            "notes": [
                "mode=live 时调用走官方开放平台真实接口，会消耗接口配额。",
                "mode=mock 时返回 mock.local 下的假数据，仅用于验证链路与字段。",
                "凭证只从本地 .env 或进程环境读取，不写入 DSH 配置。",
            ],
        })
    except Exception as exc:  # noqa: BLE001 - surface everything to the agent
        return _fail("all", exc)


@mcp.tool()
def search_products(
    platform: str,
    keyword: str,
    page: int = 1,
    page_size: int = 10,
    has_coupon: bool = False,
    sort: str = "default",
) -> str:
    """按关键词搜索商品，返回统一字段（价格/券后价/券额/佣金率/销量/店铺/链接）。

    Args:
        platform: taobao / jd / pdd
        keyword: 搜索关键词，中文即可
        page: 页码，从 1 开始
        page_size: 每页条数，1-50
        has_coupon: 只要带优惠券的商品
        sort: default / commission（佣金高）/ sales（销量）/ price_asc / price_desc / coupon_price
    """
    try:
        adapter, mode, note = _resolve(platform)
        if sort not in VALID_SORTS:
            raise ValueError(f"sort 必须是 {VALID_SORTS} 之一")
        items = adapter.search(
            keyword,
            page=page,
            page_size=page_size,
            has_coupon=has_coupon,
            sort=sort,
        )
        return _dump({
            "ok": True,
            "platform": platform.strip().lower(),
            "mode": mode,
            "note": note,
            "keyword": keyword,
            "count": len(items),
            "items": [item.to_dict() for item in items],
        })
    except Exception as exc:  # noqa: BLE001
        return _fail(platform, exc)


@mcp.tool()
def get_product(platform: str, item_id: str) -> str:
    """按商品 id 查详情。

    淘宝传 num_iid，京东传 skuId，**拼多多传 goods_sign**（搜索结果 item.extra.goods_sign
    那一长串 token —— 拼多多已停用数字 goods_id）。

    Args:
        platform: taobao / jd / pdd
        item_id: 淘宝 num_iid / 京东 skuId / 拼多多 goods_sign
    """
    try:
        adapter, mode, note = _resolve(platform)
        product = adapter.detail(str(item_id).strip())
        return _dump({
            "ok": True,
            "platform": platform.strip().lower(),
            "mode": mode,
            "note": note,
            "item": product.to_dict(),
        })
    except Exception as exc:  # noqa: BLE001
        return _fail(platform, exc)


@mcp.tool()
def convert_link(platform: str, item: str, sub_pid: str | None = None) -> str:
    """把商品转成自己的推广/返利链接（含优惠券）。

    **拼多多要传 goods_sign**（搜索结果 item.extra.goods_sign），不是数字 goods_id；
    拼多多已把 goods_id_list 从转链接口下线（会报 40005）。

    Args:
        platform: taobao / jd / pdd
        item: 淘宝 num_iid、京东 skuId 或商品 URL、拼多多 goods_sign
        sub_pid: 可选，子推广位/自定义参数，用于区分流量来源
    """
    try:
        adapter, mode, note = _resolve(platform)
        link = adapter.convert_link(str(item).strip(), sub_pid=sub_pid)
        return _dump({
            "ok": True,
            "platform": platform.strip().lower(),
            "mode": mode,
            "note": note,
            "link": link.to_dict(),
        })
    except Exception as exc:  # noqa: BLE001
        return _fail(platform, exc)


def main() -> None:
    # MCP stdio is UTF-8 by contract. On Windows, a *piped* stdout defaults to
    # the ANSI code page (cp936 here), which would mangle Chinese product titles
    # or raise UnicodeEncodeError mid-response. Reconfigure before any I/O.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    env_file = config.load_dotenv()
    print(f"[shopping-mcp] started; env={env_file or 'none'}", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
