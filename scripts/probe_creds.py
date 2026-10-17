"""Validate credentials and verify field mapping against the real API.

For every platform whose `.env` credentials are complete, this issues exactly
ONE cheap real request (a 10-row keyword search - PDD rejects fewer than 10) and prints:

  * whether the call succeeded
  * a plain-language diagnosis when it failed (sign / permission / pid / ...)
  * the field names the platform actually returned
  * our normalized output, so a mapping error is visible side by side

It is deliberately read-only and tiny: no writes, no order access, no quota burn.

Usage:
    uv run python scripts\\probe_creds.py                  # every ready platform
    uv run python scripts\\probe_creds.py --platform pdd   # just one
    uv run python scripts\\probe_creds.py --keyword 机械键盘

Exit: 0 all ready platforms passed, 1 at least one failed, 2 nothing configured.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback

from shopping_mcp import config
from shopping_mcp.adapters import ADAPTERS
from shopping_mcp.http import HttpError
from shopping_mcp.models import PlatformError

EXPECTED_FIELDS = [
    "platform",
    "item_id",
    "title",
    "price",
    "original_price",
    "coupon_amount",
    "commission_rate",
    "commission_amount",
    "sales",
    "shop_name",
    "item_url",
    "image_url",
]

KNOWN_INTERFACE = {
    "pdd": "pdd.ddk.goods.search",
    "jd": "jd.union.open.goods.query",
    "taobao": "taobao.tbk.dg.material.optional",
}


def diagnose(exc: Exception) -> str:
    """Turn a platform error into something actionable.

    Matching runs against the exception text AND the structured payload, because
    the gateways put the human-readable reason in different places: Pinduoduo
    sends `error_msg`/`sub_msg`, Taobao sends `sub_msg`, JD sends `zh_desc`.
    Biting on the message text alone missed Pinduoduo's `client下线` case.
    """
    text = f"{exc}".lower()
    code = str(getattr(exc, "code", "") or "")
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        payload = " ".join(f"{k}={v}" for k, v in detail.items()).lower()
    else:
        payload = f"{detail}".lower()
    blob = f"{text} {payload}"

    if "sign" in blob or "签名" in blob:
        return "签名错误 → client_secret / app_secret 填错，检查首尾空格与引号"
    if "参数校验" in blob or "参数不合法" in blob or "param" in blob:
        return (
            "参数不合法 → 不是凭证问题，是本项目传的参数超出平台取值范围；"
            "看 sub_msg 给出的合法区间，然后修对应 adapter"
        )
    if any(k in blob for k in ("client下线", "clientid", "client_id", "app_key", "app已下线")):
        return (
            "应用凭证无效或应用未上线 → client_id / app_key 复制错了，"
            "或应用还卡在审核/未发布（拼多多需完成安全扫描并发布）"
        )
    if any(k in blob for k in ("pid", "p_id", "adzone")):
        return "PID / 推广位无效 → 回联盟后台重新取推广位 ID"
    if any(k in blob for k in ("权限", "permission", "不支持", "not support", "scope", "无权限")):
        return (
            "权限不足 → 应用还没获得该接口权限。按平台分别处理："
            "拼多多要完成多多进宝绑定并发布；淘宝要申请对应权限包"
            "（商品搜索需 27939「商品物料获取」）；京东要申请接口权限"
        )
    if any(k in blob for k in ("session", "token", "授权", "oauth", "过期")):
        return "授权 session / token 无效或过期 → 需要重新走一次 OAuth 授权"
    if isinstance(exc, HttpError):
        return "网络 / HTTP 层失败 → 检查出网、代理、平台是否在维护"
    return f"未识别的错误（code={code or '无'}）→ 看下面的原始信息，并把 code 一起反馈"


def probe(platform: str, keyword: str) -> bool:
    """Run one search. Return True on success."""
    interface = KNOWN_INTERFACE.get(platform, "?")
    print(f"\n{'=' * 66}")
    print(f"  {platform}   interface: {interface}")
    print(f"{'=' * 66}")

    try:
        adapter = ADAPTERS[platform]()
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  无法构造 adapter: {exc}")
        return False

    try:
        items = adapter.search(keyword, page_size=10)
    except (PlatformError, HttpError, KeyError) as exc:
        code = getattr(exc, "code", "")
        label = f"  (code={code})" if code else ""
        print(f"  FAIL  {type(exc).__name__}: {exc}{label}")
        print(f"  诊断: {diagnose(exc)}")
        detail = getattr(exc, "detail", None)
        if detail:
            print("  原始返回:")
            print(json.dumps(detail, ensure_ascii=False, indent=2)[:2000])
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return False

    if not items:
        print("  WARN  接口调用成功，但返回 0 条结果。可能是关键词太窄，或 PID 没有可用商品。")
        print("        这已说明签名与权限都正常。")
        return True

    first = items[0]
    normalized = first.to_dict()
    raw = first.raw or {}

    print(f"  OK    拿到 {len(items)} 条")

    print("\n  归一化结果（我们输出给 agent 的形态）:")
    print(json.dumps(normalized, ensure_ascii=False, indent=2))

    print("\n  平台实际返回的顶层字段:")
    print("   " + ", ".join(sorted(raw.keys())))

    missing = [f for f in EXPECTED_FIELDS if normalized.get(f) in (None, "", [])]
    if missing:
        print(f"\n  WARN  这些归一化字段为空，需人工核对映射: {', '.join(missing)}")
    else:
        print("\n  归一化字段齐全。")

    out = config._project_root() / f"_probe_raw_{platform}.json"
    out.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  完整原始返回已写入: {out.name}（已在 .gitignore 中，不会提交）")
    print("  把这个文件和上面的输出贴给 agent，即可据此校准字段映射。")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=sorted(ADAPTERS), help="只验证某一个平台")
    parser.add_argument("--keyword", default="蓝牙耳机", help="探测用关键词")
    args = parser.parse_args()

    env_file = config.load_dotenv()
    print(f"env file: {env_file or '未找到（复制 .env.example 为 .env 再填值）'}")

    report = config.readiness_report()
    if args.platform:
        targets = [args.platform]
    else:
        targets = [p for p, info in report.items() if info["ready"]]

    if not targets:
        print("\n没有任何平台的凭证是完整的。当前状态：")
        for platform, info in report.items():
            print(f"  {platform:<7} mode={info['mode']:<5} 缺少: {', '.join(info['missing_env'])}")
        print("\n先看 docs/apply-pdd.md（门槛最低），填完 .env 再跑本脚本。")
        return 2

    if args.platform and not report[args.platform]["ready"]:
        missing = ", ".join(report[args.platform]["missing_env"])
        print(f"\n{args.platform} 凭证不完整，缺少: {missing}")
        return 2

    print(f"\n将验证: {', '.join(targets)}")
    results = {p: probe(p, args.keyword) for p in targets}

    print(f"\n{'=' * 66}")
    for platform, ok in results.items():
        print(f"  {platform:<7} {'PASS' if ok else 'FAIL'}")
    print(f"{'=' * 66}")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
