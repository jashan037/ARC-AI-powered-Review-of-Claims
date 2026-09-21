"""Deadlines, timeouts and retries for every Azure call (model, search, embeddings).

A chat turn runs inside `turn_scope()`. Every call goes through `call_with_retry()`, which
  * gives the call a timeout that never exceeds what is left of the turn deadline,
  * retries 429 and transient 5xx / connection errors with exponential backoff (jittered, capped, honours Retry-After),
  * raises `TurnAbort` when the deadline is gone or the retries are used up, so the caller can answer "please try again".

Model calls are not retried after a read timeout: the request may still be running on the service, and repeating a
`function_call_output` on the same conversation is what produced "No tool output found for function call" in an earlier evaluation.
Search and embeddings are reads, so a timeout there is retried.
Errors are classified by class name and status code, so this module needs neither the openai nor the azure SDK to import.
"""
from __future__ import annotations

import contextvars
import logging
import random
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from .config import settings

log = logging.getLogger("claims.resilience")

TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}
MIN_TIME_LEFT_S = 1.0          # do not start a call with less than this left in the deadline
_READ_TIMEOUT_NAMES = {"APITimeoutError", "ReadTimeout", "ServiceResponseTimeoutError", "TimeoutError", "WriteTimeout", "PoolTimeout"}
_CONNECT_NAMES = {"ConnectTimeout", "ServiceRequestTimeoutError", "ServiceRequestError", "APIConnectionError", "ConnectError", "ConnectionError", "ServiceResponseError"}


class TurnAbort(Exception):
    """The turn cannot continue. kind is "timeout" (deadline or read timeout) or "unavailable" (retries used up on 429/5xx/connection errors)."""

    def __init__(self, kind: str, where: str, cause: BaseException | None = None):
        super().__init__(f"{kind} in {where}")
        self.kind, self.where, self.cause = kind, where, cause


@dataclass
class TurnScope:
    seconds: float
    clock: callable = lambda: _now()   # noqa: E731 - looked up at call time so tests can replace _now
    started: float = field(init=False)
    retries: dict = field(default_factory=dict)   # label -> number of retries made this turn
    model_calls: int = 0
    tokens_in: int = 0                            # counts only: no question, no answer, no claim data is ever logged
    tokens_out: int = 0

    def __post_init__(self):
        self.started = self.clock()

    def remaining(self) -> float:
        return self.seconds - (self.clock() - self.started)


_scope: contextvars.ContextVar[TurnScope | None] = contextvars.ContextVar("turn_scope", default=None)
_sleep = time.sleep
_now = time.monotonic
_jitter = random.uniform


@contextmanager
def turn_scope(seconds: float | None = None, clock=None):
    s = TurnScope(settings.turn_deadline_s if seconds is None else seconds, clock or (lambda: _now()))
    token = _scope.set(s)
    try:
        yield s
    finally:
        _scope.reset(token)


def current_scope() -> TurnScope | None:
    return _scope.get()


def classify(exc: BaseException) -> str | None:
    """"timeout" (read timeout), "transient" (worth retrying) or None (a real error: bad request, auth, bug)."""
    for cls in type(exc).__mro__:
        name = cls.__name__
        if name in _READ_TIMEOUT_NAMES:
            return "timeout"
        if name in _CONNECT_NAMES:
            return "transient"
    status = getattr(exc, "status_code", None)
    return "transient" if status in TRANSIENT_STATUS else None


def _retry_after_s(exc: BaseException) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if not headers:
        return None
    try:
        if headers.get("retry-after-ms"):
            return float(headers["retry-after-ms"]) / 1000
        if headers.get("retry-after"):
            return float(headers["retry-after"])
    except (TypeError, ValueError):
        pass
    return None


def backoff_delay(attempt: int, exc: BaseException | None = None) -> float:
    """base * 2^attempt with jitter (half to full), never above the cap; a Retry-After hint can lengthen it, still under the cap."""
    delay = min(settings.backoff_cap_s, settings.backoff_base_s * (2 ** attempt))
    delay = _jitter(delay / 2, delay)
    hint = _retry_after_s(exc) if exc is not None else None
    return min(settings.backoff_cap_s, max(delay, hint)) if hint is not None else delay


def call_with_retry(fn, *, label: str, timeout: float, retry_timeouts: bool = True):
    """Run fn(timeout_seconds). See the module docstring for the rules."""
    scope = current_scope()
    attempts = settings.max_retries + 1
    for attempt in range(attempts):
        t = timeout
        if scope is not None:
            left = scope.remaining()
            if left < MIN_TIME_LEFT_S:
                raise TurnAbort("timeout", label)
            t = min(timeout, left)
        try:
            return fn(t)
        except TurnAbort:
            raise
        except Exception as e:  # noqa: BLE001 - classified below, real errors are re-raised untouched
            kind = classify(e)
            if kind is None:
                raise
            if kind == "timeout" and not retry_timeouts:
                raise TurnAbort("timeout", label, e) from e
            final_kind = "timeout" if kind == "timeout" else "unavailable"
            if attempt == attempts - 1:
                raise TurnAbort(final_kind, label, e) from e
            delay = backoff_delay(attempt, e)
            if scope is not None and delay >= scope.remaining() - MIN_TIME_LEFT_S:
                raise TurnAbort(final_kind, label, e) from e
            if scope is not None:
                scope.retries[label] = scope.retries.get(label, 0) + 1
            log.warning("retry", extra={"fields": dict(call=label, attempt=attempt + 1, reason=type(e).__name__,
                                                       status=getattr(e, "status_code", None), wait_s=round(delay, 2))})
            _sleep(delay)
