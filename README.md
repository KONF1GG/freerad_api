# Radius Core

Async FastAPI-based RADIUS microservice

## Quick start

- Install deps (uv):

```
uv sync
```

- Run dev server:

```
uv run python main.py
```

Expose env vars REDIS_URL, AMQP_URL accordingly.

## Centralized logging (Grafana + Loki + Promtail)

- Stack is provided in docker-compose. Start it:

```
docker compose up -d --build
```

- App emits structured JSON logs to stdout and mirrors them to /var/log/stdout/app.log in the container. Promtail tails that file and pushes to Loki.

- Access Grafana at http://localhost:3000 (admin/admin). Loki datasource is pre-provisioned. Explore logs by selecting datasource "Loki" and query:

```
{job="radius-core-stdout"}
```

- Environment variables:
	- SERVICE_NAME: radius-core (default)
	- ENVIRONMENT: dev/prod
	- LOG_LEVEL: info/debug/warning/error
