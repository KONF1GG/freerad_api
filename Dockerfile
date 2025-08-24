FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock* ./

RUN uv sync --frozen --no-cache

COPY . .

# Создаем директорию для multiprocess метрик
RUN mkdir -p /tmp/prometheus_multiproc_dir

CMD ["uv", "run", "gunicorn", "-w", "3", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "main:app"]