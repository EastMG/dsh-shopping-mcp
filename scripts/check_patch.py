"""Structural check for the standalone DSH patch overlay.

Fails loudly rather than letting a malformed overlay reach the boot path.

Usage: uv run --with pyyaml python scripts/check_patch.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

PATCH = Path(__file__).resolve().parents[1] / "dsh" / "mcp-shopping.patch.yml"


def main() -> int:
    if not PATCH.is_file():
        print(f"FAIL  missing {PATCH}")
        return 1

    data = yaml.safe_load(PATCH.read_text(encoding="utf-8"))
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  {detail}"))
        if not ok:
            failures.append(name)

    check("root is a list", isinstance(data, list), type(data).__name__)
    if not isinstance(data, list):
        return 1

    check("exactly one overlay entry", len(data) == 1, str(len(data)))
    entry = data[0] if data else {}
    check("entry has insert", isinstance(entry, dict) and "insert" in entry, str(entry))
    inserted = (entry or {}).get("insert") or []
    check("insert has one item", len(inserted) == 1, str(len(inserted)))
    item = inserted[0] if inserted else {}

    check("id is mcp-shopping", item.get("id") == "mcp-shopping", str(item.get("id")))
    check(
        "name is dsh-mcp-client",
        item.get("name") == "@deepseek-ai/dsh-mcp-client",
        str(item.get("name")),
    )

    config = item.get("config") or {}
    check("serverName is shopping", config.get("serverName") == "shopping", str(config.get("serverName")))
    check("transport is stdio", config.get("transport") == "stdio", str(config.get("transport")))

    command = config.get("command") or ""
    check("command path exists", Path(command).is_file(), command)
    check("args launch the module", config.get("args") == ["-m", "shopping_mcp.server"], str(config.get("args")))

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        return 1
    print("PATCH OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
