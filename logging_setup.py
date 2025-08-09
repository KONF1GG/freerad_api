import logging
import json
import os
import socket
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, Optional


# Context carried across async tasks
_log_ctx: ContextVar[Dict[str, Any]] = ContextVar("log_ctx", default={})


def get_log_context() -> Dict[str, Any]:
    return dict(_log_ctx.get() or {})


def set_log_context(ctx: Dict[str, Any]) -> None:
    _log_ctx.set(dict(ctx))


def update_log_context(extra: Dict[str, Any]) -> None:
    ctx = get_log_context()
    ctx.update({k: v for k, v in extra.items() if v is not None})
    _log_ctx.set(ctx)


def clear_log_context() -> None:
    _log_ctx.set({})


class ContextFilter(logging.Filter):
    """Inject contextvars into each record."""

    def __init__(self, service: str, environment: str):
        super().__init__()
        self.service = service
        self.environment = environment
        self.host = socket.gethostname()

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        ctx = get_log_context()

        # Standard fields
        setattr(record, "service", self.service)
        setattr(record, "env", self.environment)
        setattr(record, "host", self.host)

        # Context fields (best-effort)
        for key in (
            "request_id",
            "trace_id",
            "span_id",
            "correlation_id",
            "method",
            "path",
            "client_ip",
            "user_id",
        ):
            if key in ctx:
                setattr(record, key, ctx[key])
        return True


class JsonFormatter(logging.Formatter):
    """Minimal, fast JSON formatter with predictable keys per line."""

    converter = time.gmtime

    def __init__(self):
        super().__init__()

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        # Base payload
        payload: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }

        # Add context if present
        for field in (
            "service",
            "env",
            "host",
            "request_id",
            "trace_id",
            "span_id",
            "correlation_id",
            "method",
            "path",
            "client_ip",
            "user_id",
        ):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value

        # Errors
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        # Render
        return json.dumps(payload, ensure_ascii=False)


def _coerce_level(level: str) -> int:
    return {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }.get(level.lower(), logging.INFO)


def configure_logging(
    *,
    service: Optional[str] = None,
    environment: Optional[str] = None,
    level: Optional[str] = None,
) -> None:
    """Configure root logging to emit structured JSON to stdout.

    Env overrides (optional):
      LOG_LEVEL, SERVICE_NAME, ENVIRONMENT
    """

    service_name = service or os.getenv("SERVICE_NAME", "radius-core")
    env = environment or os.getenv("ENVIRONMENT", os.getenv("ENV", "dev"))
    lvl = _coerce_level(level or os.getenv("LOG_LEVEL", "info"))

    root = logging.getLogger()
    root.setLevel(lvl)

    # Clear existing handlers (e.g., basicConfig) to avoid double logs
    for h in list(root.handlers):
        root.removeHandler(h)

    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setFormatter(JsonFormatter())
    stream_handler.addFilter(ContextFilter(service_name, env))
    root.addHandler(stream_handler)

    # Optional: mirror to file inside container so promtail can tail it
    log_dir = os.getenv("LOG_MIRROR_DIR", "/var/log/stdout")
    try:
        os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.FileHandler(os.path.join(log_dir, "app.log"))
        file_handler.setFormatter(JsonFormatter())
        file_handler.addFilter(ContextFilter(service_name, env))
        root.addHandler(file_handler)
    except Exception:
        # Ignore file mirror errors; stdout remains primary sink
        pass

    # Tame noisy libraries if needed
    logging.getLogger("aio_pika").setLevel(logging.INFO)
    logging.getLogger("aiormq").setLevel(logging.INFO)
    logging.getLogger("uvicorn").setLevel(lvl)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(lvl)


def start_request_context(
    *,
    request_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    method: Optional[str] = None,
    path: Optional[str] = None,
    client_ip: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Initialize per-request context and return it (useful for middlewares)."""
    ctx = {
        "request_id": request_id or str(uuid.uuid4()),
        "trace_id": trace_id or str(uuid.uuid4()),
        "span_id": uuid.uuid4().hex[:16],
        "method": method,
        "path": path,
        "client_ip": client_ip,
        "user_id": user_id,
    }
    update_log_context(ctx)
    return get_log_context()
