"""Structured logging with workflow correlation. Never log document text or secrets."""

from __future__ import annotations

import contextvars
import json
import logging
import sys

workflow_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar("workflow_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "workflow_id": workflow_ctx.get(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if any(getattr(h, "_chainaudit", False) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler._chainaudit = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    for n in ("httpx", "httpx2", "uvicorn.access"):
        logging.getLogger(n).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"chainaudit.{name}")
