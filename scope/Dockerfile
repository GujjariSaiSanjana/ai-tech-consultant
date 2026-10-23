# Scope of Work service: web UI on port 8100. Build from the scope/ directory:
#   docker build -t scope . && docker run -p 8100:8100 -e ANTHROPIC_API_KEY -v scope-data:/var/lib/scope scope
FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY data ./data
COPY evals ./evals
COPY deploy ./deploy
RUN uv sync --frozen --no-dev
ENV SCOPE_HOST=0.0.0.0 \
    SCOPE_PORT=8100 \
    SCOPE_DATABASE_URL=sqlite+aiosqlite:////var/lib/scope/scope.db \
    SCOPE_EXPORT_DIR=/var/lib/scope/exports
RUN useradd --create-home app && mkdir -p /var/lib/scope && chown app /var/lib/scope
USER app
VOLUME ["/var/lib/scope"]
EXPOSE 8100
CMD ["/app/.venv/bin/python", "-m", "scope.web"]
