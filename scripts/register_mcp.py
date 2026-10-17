"""Register shopping-mcp into a DSH profile's patch file.

Idempotent: re-running is a no-op once the entry exists. Always writes a
timestamped backup before touching anything, and refuses to append to a file
that does not already look like a patch list.

Usage:
    uv run --with pyyaml python scripts/register_mcp.py            # register
    uv run --with pyyaml python scripts/register_mcp.py --check     # validate only
    uv run --with pyyaml python scripts/register_mcp.py --remove    # roll back
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROFILE = Path(__file__).resolve().parents[4] / ".dsh" / "profiles" / "desktop" / "cordis.patch.yml"
# Fallback: resolve from DSH_HOME when the repo-relative guess is wrong.
import os

if not PROFILE.is_file() and os.environ.get("DSH_HOME"):
    PROFILE = Path(os.environ["DSH_HOME"]) / "profiles" / "desktop" / "cordis.patch.yml"

PROJECT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT / ".venv" / "Scripts" / "python.exe"

MARKER = "id: mcp-shopping"

BLOCK = f"""
# 电商结构化能力 MCP（shopping-mcp）：淘宝 / 京东 / 拼多多 的商品搜索、详情、转链。
# 凭证放在 {PROJECT.name}/.env —— 不写在这里，避免进入配置 dump 或共享 patch。
# 未配置凭证的平台会自动降级为 mock 模式，所以首次启动即可验证整条工具链。
- insert:
    - id: mcp-shopping
      name: "@deepseek-ai/dsh-mcp-client"
      config:
        serverName: "shopping"
        transport: "stdio"
        command: "{PYTHON.as_posix()}"
        args:
          - "-m"
          - "shopping_mcp.server"
"""


def validate(path: Path) -> None:
    import yaml

    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SystemExit(f"INVALID YAML in {path}:\n{exc}") from exc
    if not isinstance(data, list):
        raise SystemExit(f"{path} root is {type(data).__name__}, expected a list")
    print(f"OK  {path} parses as a YAML list with {len(data)} top-level entries")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="validate the file, change nothing")
    parser.add_argument("--remove", action="store_true", help="remove the shopping entry")
    parser.add_argument("--force", action="store_true", help="append even if marker already present")
    args = parser.parse_args()

    if not PROFILE.is_file():
        raise SystemExit(f"profile patch not found: {PROFILE}")
    if not PYTHON.is_file():
        raise SystemExit(f"venv interpreter not found: {PYTHON}  (run `uv sync` first)")

    if args.check:
        validate(PROFILE)
        present = MARKER in PROFILE.read_text(encoding="utf-8")
        print(f"shopping entry present: {present}")
        return 0

    text = PROFILE.read_text(encoding="utf-8")

    if args.remove:
        if MARKER not in text:
            print("nothing to remove")
            return 0
        start = text.rindex("- insert:", 0, text.index(MARKER))
        end = text.index("shopping_mcp.server", text.index(MARKER))
        end = text.index("\n", end) + 1
        backup = _backup()
        PROFILE.write_text(text[:start] + text[end:], encoding="utf-8")
        print(f"removed entry; backup at {backup}")
        validate(PROFILE)
        return 0

    if MARKER in text and not args.force:
        print("already registered (idempotent no-op)")
        validate(PROFILE)
        return 0

    backup = _backup()
    if not text.endswith("\n"):
        text += "\n"
    PROFILE.write_text(text + BLOCK, encoding="utf-8")
    print(f"appended shopping-mcp entry; backup at {backup}")
    validate(PROFILE)
    print("\nRestart DSH for the new MCP server to be picked up.")
    return 0


def _backup() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = PROFILE.with_name(f"{PROFILE.name}.bak-{stamp}-preShopping")
    shutil.copy2(PROFILE, backup)
    return backup


if __name__ == "__main__":
    sys.exit(main())
