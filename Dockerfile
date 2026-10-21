# Multi-stage build for the FieldDesk API.
# NOTE: not built locally (the dev machine has no Docker; see docs/DECISIONS.md D10).
# Build:  docker build -t fielddesk .
# Run:    docker run --env-file .env -p 8000:8000 fielddesk

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first, so source edits don't invalidate this layer.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime
RUN useradd --create-home --uid 10001 fielddesk
WORKDIR /app
COPY --from=builder --chown=fielddesk:fielddesk /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    FIELDDESK_DB_PATH=/app/var/fielddesk.db \
    FIELDDESK_LEDGER_PATH=/app/var/ledger.jsonl
RUN mkdir -p /app/var && chown fielddesk:fielddesk /app/var
USER fielddesk
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "fielddesk.api:app", "--host", "0.0.0.0", "--port", "8000"]
