# Search Audit report server for Railway: serves audits/<slug>/results.json (+ report.pdf).
# The engine (run/judge/assemble) runs on a laptop; only its outputs ship in the image.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    AUDITS_DIR=/app/audits

WORKDIR /app

# Dependencies first, so code changes reuse this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY searchaudit searchaudit
RUN uv sync --frozen --no-dev

# Only what the report reads: results.json, report.pdf, prompts.json (see .dockerignore).
COPY audits audits

RUN useradd --system --uid 999 --no-create-home app
USER app

EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn searchaudit.web.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
