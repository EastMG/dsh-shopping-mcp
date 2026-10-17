"""扣动拼多多「授权备案」这一步。

背景：拼多多的商品搜索/转链接口要求**推手本人**做过一次授权备案，否则每次
调用都返回 sub_code 60001「未传入已经授权备案过的相关参数」。这不是凭证错，
是账号维度的授权没做。

本脚本做三件事，按顺序：
  1. pdd.ddk.member.authority.query 查当前是否已备案（bind == 1）
  2. 未备案则 pdd.ddk.rp.prom.url.generate(channel_type=10) 生成备案链接
  3. 把链接打印出来，你去浏览器打开并点「确认授权」

用法：
    uv run python scripts\\pdd_authorize.py            # 查状态，没备案就出链接
    uv run python scripts\\pdd_authorize.py --open     # 额外尝试调用系统浏览器
    uv run python scripts\\pdd_authorize.py --status   # 只查状态

注意：备案必须由**将来拿佣金的那个拼多多账号**点确认。一个拼多多账号最多
可以备案给 5 个业务平台推手身份；一个推手只需备案一次。
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser

from shopping_mcp import config
from shopping_mcp.adapters.pdd import PddAdapter
from shopping_mcp.models import PlatformError


def show_status(adapter: PddAdapter) -> bool | None:
    """Return True if 备案 done, False if not, None if unknown."""
    try:
        payload = adapter.authority_status()
    except PlatformError as exc:
        print(f"  查询备案状态失败: {exc}")
        detail = getattr(exc, "detail", None)
        if detail:
            print(json.dumps(detail, ensure_ascii=False, indent=2))
        return None

    bind = payload.get("bind")
    print(f"  authority.query 原始返回: {json.dumps(payload, ensure_ascii=False)[:400]}")
    if bind in (1, "1", True):
        print("  状态: 已备案 (bind=1)")
        return True
    print("  状态: 未备案 (bind != 1)")
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true", help="只查询，不生成链接")
    parser.add_argument("--open", action="store_true", help="生成链接后尝试打开系统浏览器")
    parser.add_argument("--uid", help="可选的 custom_parameters uid；不传则只用 pid 标识推手")
    args = parser.parse_args()

    env_file = config.load_dotenv()
    print(f"env file: {env_file or '未找到 .env'}")

    try:
        adapter = PddAdapter()
    except PlatformError as exc:
        print(f"\n无法初始化拼多多适配器: {exc}")
        print("请先在 .env 里填齐 PDD_CLIENT_ID / PDD_CLIENT_SECRET / PDD_PID")
        return 2

    print(f"pid: {adapter.pid}\n")

    print("[1/2] 查询授权备案状态")
    bound = show_status(adapter)
    if bound is True:
        print("\n已备案，可以直接跑 probe_creds.py 了。")
        return 0
    if args.status:
        return 0 if bound else 1

    print("\n[2/2] 生成授权备案链接")
    custom = json.dumps({"uid": args.uid}, ensure_ascii=False) if args.uid else None
    try:
        url = adapter.authority_url(custom_parameters=custom)
    except PlatformError as exc:
        print(f"  生成失败: {exc}")
        detail = getattr(exc, "detail", None)
        if detail:
            print(json.dumps(detail, ensure_ascii=False, indent=2))
        return 1

    print("\n" + "=" * 70)
    print("  授权备案链接（要用将来拿佣金的拼多多账号打开并点「确认授权」）:")
    print("=" * 70)
    print(url)
    print("=" * 70)

    if args.open:
        try:
            webbrowser.open(url)
            print("已尝试唤起系统浏览器。")
        except Exception as exc:  # noqa: BLE001
            print(f"唤起浏览器失败（手动复制上面的链接即可）: {exc}")

    print("\n打开链接 → 点「确认授权」→ 回来重跑本脚本，应显示 bind=1。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
