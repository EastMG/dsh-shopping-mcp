"""淘宝 access_token 续期。

淘宝的 access_token 只有 **1 天**有效期，refresh_token 有 **30 天**。
如果不续，第二天所有淘宝调用都会返回「session 失效」，看起来像权限问题。

用法：
    uv run python scripts\\taobao_refresh_token.py           # 续期并写回 .env
    uv run python scripts\\taobao_refresh_token.py --check   # 只看当前 token 还有多久

续期后会把新的 TAOBAO_SESSION / TAOBAO_REFRESH_TOKEN 原地写回 `.env`，
其它行（包括注释）原样保留。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

TOKEN_URL = "https://oauth.taobao.com/token"


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        out[k.strip()] = v.strip()
    return out


def write_env_key(path: Path, key: str, value: str) -> bool:
    """Replace `<key>=...` in place, preserving everything else."""
    lines = path.read_text(encoding="utf-8").splitlines()
    replaced = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return replaced


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只检查，不续期")
    args = parser.parse_args()

    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        print(f"找不到 {env_path}")
        return 2

    env = read_env(env_path)
    app_key = env.get("TAOBAO_APP_KEY", "")
    app_secret = env.get("TAOBAO_APP_SECRET", "")
    refresh = env.get("TAOBAO_REFRESH_TOKEN", "")
    session = env.get("TAOBAO_SESSION", "")

    if not (app_key and app_secret):
        print("缺少 TAOBAO_APP_KEY / TAOBAO_APP_SECRET")
        return 2

    print(f"env            : {env_path}")
    print(f"app_key        : {app_key}")
    print(f"当前 session   : {session[:12]}...（长度 {len(session)}）")
    print(f"refresh_token  : {'有' if refresh else '无'}")

    if args.check:
        return 0

    if not refresh:
        print("\n没有 TAOBAO_REFRESH_TOKEN，无法自动续期。需要重新走一次 OAuth 授权。")
        return 1

    print("\n正在用 refresh_token 换新 token...")
    try:
        resp = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": app_key,
                "client_secret": app_secret,
                "refresh_token": refresh,
            },
            timeout=30,
        )
    except httpx.HTTPError as exc:
        print(f"网络错误: {exc}")
        return 1

    print(f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        print(resp.text[:500])
        return 1

    if "error" in data or "access_token" not in data:
        print("续期失败：")
        print(json.dumps(data, ensure_ascii=False, indent=2)[:800])
        return 1

    new_access = data["access_token"]
    new_refresh = data.get("refresh_token", refresh)

    write_env_key(env_path, "TAOBAO_SESSION", new_access)
    write_env_key(env_path, "TAOBAO_REFRESH_TOKEN", new_refresh)

    print("\n续期成功，已写回 .env")
    print(f"  新 session      : {new_access[:12]}...")
    print(f"  有效期          : {data.get('expires_in')} 秒")
    print(f"  refresh 有效期  : {data.get('re_expires_in')} 秒")
    print(f"  淘宝昵称        : {data.get('taobao_user_nick')}")
    if data.get("expire_time"):
        exp = datetime.fromtimestamp(int(data["expire_time"]) / 1000)
        print(f"  过期时间        : {exp:%Y-%m-%d %H:%M:%S}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
