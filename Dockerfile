FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock* ./

RUN uv venv && \
    UV_PROJECT_ENVIRONMENT=/env uv sync --no-cache

COPY . .

CMD ["gunicorn", "-w", "3", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "main:app"]