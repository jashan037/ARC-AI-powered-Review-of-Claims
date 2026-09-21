"""One JSON log line per chat turn, and for every retry. Written to stderr.

What is logged: ids, the agent and retriever in use, tool names with success and milliseconds, answer type, status, latency,
retry counts and an error class name. What is never logged: the user's question, claim data, tool arguments, tool results,
answers or secrets. `message_chars` is only a length.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid

from .config import settings

log = logging.getLogger("claims")


_SECRET_SHAPES = [re.compile(p, re.I) for p in (
    r"(?:api[-_ ]?key|subscription[-_ ]?key|secret|password|token)['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9+/_\-.]{8,}", r"Bearer\s+[A-Za-z0-9+/_\-.=]{8,}", r"Authorization['\"]?\s*[:=]\s*\S+",
    r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-.]{8,}", r"\b[A-Za-z0-9+/_\-]{32,}={0,2}(?=\b)")]


def redact(text) -> str:
    """Text with anything key-shaped, any bearer token or auth header and the configured keys replaced by [redacted]. Applied to every log line."""
    s = str(text)
    for v in (settings.search_key, settings.openai_key):
        if v and len(v) >= 8:
            s = s.replace(v, "[redacted]")
    for rx in _SECRET_SHAPES:
        s = rx.sub("[redacted]", s)
    return s


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + "Z", "level": record.levelname,
               "logger": record.name, "event": record.getMessage()}
        out.update(getattr(record, "fields", {}))
        return redact(json.dumps(out, ensure_ascii=False, default=str))


def configure_logging() -> None:
    """Idempotent. Call once at start-up (the API does)."""
    root = logging.getLogger("claims")
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    if not any(getattr(h, "_claims_json", False) for h in root.handlers):
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(JsonFormatter())
        h._claims_json = True
        root.addHandler(h)
        root.propagate = False


def log_turn(*, agent: str, session: dict, message: str, status: str, answer_type: str, latency_ms: int, trace: list, scope=None, error: str | None = None) -> None:
    fields = dict(turn_id=uuid.uuid4().hex[:8], session=session.get("id"), agent=agent, retriever=settings.retriever,
                  has_claim=bool(session.get("claim")), message_chars=len(message), status=status, answer_type=answer_type, latency_ms=latency_ms,
                  tools=[dict(tool=t["tool"], ok=t["ok"], ms=t.get("ms")) for t in trace],
                  final_rejections=sum(1 for t in trace if t["tool"] == "final_answer" and not t["ok"]),
                  model_calls=getattr(scope, "model_calls", None), retries=getattr(scope, "retries", None) or {}, error=error)
    log.log(logging.INFO if status == "ok" else logging.WARNING, "turn", extra={"fields": fields})
