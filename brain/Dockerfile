# Knowledge Brain API image. Build from this directory:  docker build -t brain .
# The same image runs the other entry points, e.g.  docker run brain python -m brain.scheduler
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed \
    BRAIN_HOST=0.0.0.0 \
    BRAIN_PORT=8000 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
RUN useradd --create-home --uid 10001 brain

# Dependencies first so code changes do not invalidate the layer.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY migrations ./migrations
COPY evals ./evals
COPY deploy ./deploy
RUN uv sync --frozen --no-dev && mkdir -p /app/.cache && chown -R brain /app/.cache

USER brain
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["python", "-m", "brain.api"]
