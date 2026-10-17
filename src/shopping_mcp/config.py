"""Credential loading and readiness reporting.

Design notes
------------
* Secrets live in a local `.env` file next to the project (or in the process
  environment). They are NEVER placed in the DSH profile / MCP config, so they
  do not get duplicated into config dumps or shared patch files.
* A platform is "ready" only when every required key is present. Missing keys
  do not crash the server: the platform degrades to mock mode, so the whole
  tool surface stays explorable before any approval comes through.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PLATFORMS = ("taobao", "jd", "pdd")

# Required env keys per platform, plus the key that carries the signing secret.
REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "taobao": ("TAOBAO_APP_KEY", "TAOBAO_APP_SECRET", "TAOBAO_SESSION", "TAOBAO_PID"),
    "jd": ("JD_APP_KEY", "JD_APP_SECRET", "JD_SITE_ID", "JD_POSITION_ID"),
    "pdd": ("PDD_CLIENT_ID", "PDD_CLIENT_SECRET", "PDD_PID"),
}

SECRET_KEY = {
    "taobao": "TAOBAO_APP_SECRET",
    "jd": "JD_APP_SECRET",
    "pdd": "PDD_CLIENT_SECRET",
}

APPLY_GUIDE = {
    "taobao": (
        "淘宝联盟（阿里妈妈）→ 淘宝开放平台创建应用 → 申请淘宝客 API 权限。"
        "需要实名认证 + 推广媒体备案，审核通常 1-3 个工作日。"
        "TAOBAO_SESSION 通过 oauth.taobao.com 授权后取得。"
    ),
    "jd": (
        "京东联盟 union.jd.com → 注册推广者 → 在京东宙斯/开放平台创建应用，"
        "申请 jd.union.open.* 接口权限。JD_SITE_ID 与 JD_POSITION_ID 取自联盟后台的网站与推广位。"
    ),
    "pdd": (
        "拼多多开放平台 open.pinduoduo.com → 创建应用 → 申请多多进宝 pdd.ddk.* 权限。"
        "三家之中个人注册门槛最低，建议从这里先跑通。PDD_PID 取自多多进宝后台的推广位。"
    ),
}


def _project_root() -> Path:
    # src/shopping_mcp/config.py -> src/shopping_mcp -> src -> project root
    return Path(__file__).resolve().parents[2]


def load_dotenv(path: Path | None = None, override: bool = False) -> Path | None:
    """Minimal .env loader (no external dependency).

    Format: KEY=VALUE per line, `#` comments, optional surrounding quotes.
    Values already present in the real environment win unless override=True.

    Set SHOPPING_MCP_SKIP_DOTENV=1 to ignore any .env file entirely — the test
    suite uses this so its result does not depend on the machine it runs on.
    """
    if os.environ.get("SHOPPING_MCP_SKIP_DOTENV", "").strip().lower() in {"1", "true", "yes"}:
        return None

    candidates = [path] if path else [
        Path.cwd() / ".env",
        _project_root() / ".env",
    ]
    for candidate in candidates:
        if candidate is None or not candidate.is_file():
            continue
        for raw_line in candidate.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            if override or key not in os.environ:
                os.environ[key] = value
        return candidate
    return None


@dataclass
class PlatformConfig:
    name: str
    values: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.missing

    @property
    def secret(self) -> str:
        return self.values.get(SECRET_KEY[self.name], "")

    def require(self, *keys: str) -> str:
        for key in keys:
            if key in self.values and self.values[key]:
                return self.values[key]
        raise KeyError(
            f"{self.name}: missing required credential(s) {keys}; "
            f"see shopping_status for the application guide"
        )


def load_platforms() -> dict[str, PlatformConfig]:
    out: dict[str, PlatformConfig] = {}
    for platform in PLATFORMS:
        values: dict[str, str] = {}
        missing: list[str] = []
        for key in REQUIRED_KEYS[platform]:
            value = os.environ.get(key, "").strip()
            if value:
                values[key] = value
            else:
                missing.append(key)
        out[platform] = PlatformConfig(name=platform, values=values, missing=missing)
    return out


def readiness_report() -> dict[str, dict[str, object]]:
    report: dict[str, dict[str, object]] = {}
    for platform, cfg in load_platforms().items():
        report[platform] = {
            "mode": "live" if cfg.ready else "mock",
            "ready": cfg.ready,
            "missing_env": cfg.missing,
            "apply": APPLY_GUIDE[platform],
        }
    return report


def resolve_mode(platform: str, force_mock: bool = False) -> str:
    if force_mock:
        return "mock"
    cfg = load_platforms().get(platform)
    if cfg is None:
        raise ValueError(f"unknown platform: {platform!r}; expected one of {PLATFORMS}")
    return "live" if cfg.ready else "mock"
