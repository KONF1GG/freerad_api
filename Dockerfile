FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock* ./

RUN uv sync --frozen --no-cache

COPY . .

RUN mkdir -p /app/prometheus_multiproc_dir && chmod 755 /app/prometheus_multiproc_dir

# Устанавливаем переменную окружения для multiprocess метрик
ENV PROMETHEUS_MULTIPROC_DIR=/app/prometheus_multiproc_dir

WORKDIR /app/src

CMD ["uv", "run", "gunicorn", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "main:app"]