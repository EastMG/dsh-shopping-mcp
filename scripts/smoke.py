"""Smoke test for shopping-mcp.

Layers, because they fail differently:

  1. pure-logic checks (signing, unit normalization) — catch "silently wrong"
  2. a real MCP protocol round-trip over an **in-memory transport**

Why in-memory and not stdio-on-a-subprocess: under the DSH file sandbox,
creating a duplex pipe for a child process is denied (WinError 5). That is a
sandbox boundary, not a server bug — so the protocol layer is exercised
in-process here, and the real stdio entrypoint is validated by DSH itself when
the server is registered in the profile.

Run:  uv run python scripts/smoke.py
Exit: 0 on success, 1 on any failure.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys

os.environ.setdefault("SHOPPING_MCP_MOCK", "1")
# Hermetic: ignore any real .env and any ambient credentials, so this suite
# behaves identically on a bare checkout and on a machine that has already
# filled in its appkeys. Without this, smoke would start failing the moment the
# user configures a platform for real.
os.environ["SHOPPING_MCP_SKIP_DOTENV"] = "1"

from shopping_mcp import config as _config  # noqa: E402

for _keys in _config.REQUIRED_KEYS.values():
    for _key in _keys:
        os.environ.pop(_key, None)

from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"  PASS  {name}")
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  {detail}")


# --------------------------------------------------------------------- layer 1
def logic_checks() -> None:
    print("\n[1/2] logic")

    from shopping_mcp.signing import sign_md5, signature_material

    material = signature_material("secret", {"b": "2", "a": "1", "sign": "IGNORED"})
    check("signing: excludes sign, sorts by key", material == "secret" + "a1b2" + "secret", material)

    expected = hashlib.md5(b"secreta1b2secret").hexdigest().upper()
    check("signing: md5 uppercase hex", sign_md5("secret", {"a": "1", "b": "2"}) == expected)

    from shopping_mcp.adapters.pdd import _cents, _per_mille

    check("pdd: fen -> yuan", _cents(19900) == 199.0, str(_cents(19900)))
    check("pdd: per-mille -> percent", _per_mille(100) == 10.0, str(_per_mille(100)))

    from shopping_mcp.adapters.taobao import _normalize_rate as tb_rate

    check("taobao: fraction keeps precision", tb_rate(0.155) == 15.5, str(tb_rate(0.155)))
    check("taobao: percent", tb_rate(15.5) == 15.5, str(tb_rate(15.5)))
    check("taobao: basis points", tb_rate(1550) == 15.5, str(tb_rate(1550)))
    check("taobao: empty -> None", tb_rate("") is None, repr(tb_rate("")))

    from shopping_mcp.adapters.jd import _normalize_rate as jd_rate

    check("jd: fraction keeps precision", jd_rate(0.08) == 8.0, str(jd_rate(0.08)))

    from shopping_mcp.adapters.taobao import _parse_pid

    site, adzone = _parse_pid("mm_12345678_901234567_10987654321")
    check("taobao: pid parse", (site, adzone) == ("901234567", "10987654321"), f"{site}/{adzone}")

    from shopping_mcp import config

    report = config.readiness_report()
    check("config: covers 3 platforms", set(report) == {"taobao", "jd", "pdd"}, str(set(report)))
    check(
        "config: empty creds -> mock",
        all(entry["mode"] == "mock" for entry in report.values()),
        str({k: v["mode"] for k, v in report.items()}),
    )
    check(
        "config: every platform has an apply guide",
        all(entry["apply"] for entry in report.values()),
        "missing guide",
    )

    from shopping_mcp import server

    check("server: entrypoint importable", callable(server.main))
    check("server: 4 tools registered", _registered_tool_count(server.mcp) == 4, str(_registered_tool_count(server.mcp)))


def _registered_tool_count(fastmcp) -> int:
    manager = getattr(fastmcp, "_tool_manager", None)
    tools = getattr(manager, "_tools", None)
    return len(tools) if tools else -1


# --------------------------------------------------------------------- layer 2
async def protocol_checks() -> None:
    print("\n[2/2] MCP protocol (in-memory transport)")

    from shopping_mcp.server import mcp as server

    async with create_connected_server_and_client_session(server._mcp_server) as session:
        init = await session.initialize()
        check("initialize: server name", init.serverInfo.name == "shopping", str(init.serverInfo))

        listed = await session.list_tools()
        names = {tool.name for tool in listed.tools}
        check(
            "tools/list: exact 4 tools",
            names == {"shopping_status", "search_products", "get_product", "convert_link"},
            str(names),
        )
        for tool in listed.tools:
            if not tool.description:
                check(f"tools/list: {tool.name} has description", False, "empty")
        check("tools/list: all have descriptions", all(t.description for t in listed.tools))

        # --- search
        payload = await _call(session, "search_products", {"platform": "pdd", "keyword": "蓝牙耳机", "page_size": 3})
        check("search: ok", payload.get("ok") is True, str(payload.get("error", "")))
        check("search: mode=mock", payload.get("mode") == "mock", str(payload.get("mode")))
        check("search: returned rows", payload.get("count", 0) >= 1, str(payload.get("count")))
        check("search: mock note present", bool(payload.get("note")), "no note")

        items = payload.get("items") or []
        if items:
            first = items[0]
            required = {"platform", "item_id", "title", "price", "coupon_amount", "commission_rate"}
            check("search: normalized fields", required <= set(first), str(required - set(first)))
            check("search: mock urls obviously fake", "mock.local" in (first.get("item_url") or ""), str(first.get("item_url")))
            check("search: raw hidden by default", "raw" not in first)
            check("search: price = original - coupon", _price_consistent(first), json.dumps(first, ensure_ascii=False))
            check("search: commission arithmetic", _commission_consistent(first), json.dumps(first, ensure_ascii=False))

        # --- detail
        item_id = items[0]["item_id"] if items else "999"
        detail = await _call(session, "get_product", {"platform": "jd", "item_id": item_id})
        check("detail: ok", detail.get("ok") is True, str(detail.get("error", "")))
        check(
            "detail: echoes item_id",
            (detail.get("item") or {}).get("item_id") == item_id,
            str((detail.get("item") or {}).get("item_id")),
        )

        # --- convert_link
        link = await _call(session, "convert_link", {"platform": "taobao", "item": "654321"})
        check("convert_link: ok", link.get("ok") is True, str(link.get("error", "")))
        url = (link.get("link") or {}).get("promotion_url", "")
        check("convert_link: has url", bool(url), url)
        check("convert_link: mock domain", "mock.local" in url, url)

        # --- status
        status = await _call(session, "shopping_status", {})
        check("status: ok", status.get("ok") is True, str(status.get("error", "")))
        check(
            "status: 3 platforms reported",
            set(status.get("platforms") or {}) == {"taobao", "jd", "pdd"},
            str(set(status.get("platforms") or {})),
        )

        # --- bad input must be an error payload, not a crash
        bad = await _call(session, "search_products", {"platform": "amazon", "keyword": "x"})
        check("bad platform: ok=false, no crash", bad.get("ok") is False, str(bad))

        # --- coupon filter actually filters
        coupon_only = await _call(
            session, "search_products", {"platform": "pdd", "keyword": "耳机", "page_size": 20, "has_coupon": True}
        )
        coupon_items = coupon_only.get("items") or []
        check(
            "has_coupon: every row has a coupon",
            all(item.get("coupon_amount") for item in coupon_items),
            str([i.get("coupon_amount") for i in coupon_items]),
        )


async def _call(session, name: str, args: dict) -> dict:
    result = await session.call_tool(name, args)
    return json.loads(result.content[0].text)


def _price_consistent(item: dict) -> bool:
    price, original, coupon = item.get("price"), item.get("original_price"), item.get("coupon_amount")
    if price is None or original is None:
        return True
    if coupon:
        return abs((original - coupon) - price) < 0.02
    return abs(original - price) < 0.02


def _commission_consistent(item: dict) -> bool:
    price, rate, amount = item.get("price"), item.get("commission_rate"), item.get("commission_amount")
    if None in (price, rate, amount):
        return True
    return abs(round(price * rate / 100, 2) - amount) < 0.02


def main() -> int:
    print("shopping-mcp smoke test")
    logic_checks()
    asyncio.run(protocol_checks())

    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} checks passed")
    if FAILURES:
        print("\nFAILURES:")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
