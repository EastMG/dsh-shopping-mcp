# syntax=docker/dockerfile:1
# stdio MCP server：容器里只跑入口进程，靠 stdin/stdout 与 DSH 通信。
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# 依赖层：只依赖 pyproject/uv.lock，改代码不会让这一层失效
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 应用层
COPY README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev

# 非 root 运行
RUN useradd --create-home --uid 10001 app \
 && chown -R app:app /app
USER app

# 凭证从 --env-file 注入，不打进镜像
ENTRYPOINT ["/app/.venv/bin/shopping-mcp"]
