"""Local observability, rate controls and retry policy without cloud dependencies."""

from __future__ import annotations

import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

LOGGER = logging.getLogger("forecast_towell.api")
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False


class TelemetryProvider(ABC):
    @abstractmethod
    def emit(self, *, environment: str, component: str, event: str, status: str,
             request_id: str | None = None, user_id: str | None = None,
             run_id: str | None = None, duration: float | None = None,
             error_code: str | None = None, **fields: Any) -> None: ...

    @abstractmethod
    def snapshot(self) -> dict[str, Any]: ...


class LocalTelemetryProvider(TelemetryProvider):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: Counter[str] = Counter()
        self._durations: dict[str, list[float]] = defaultdict(list)

    def emit(self, *, environment: str, component: str, event: str, status: str,
             request_id: str | None = None, user_id: str | None = None,
             run_id: str | None = None, duration: float | None = None,
             error_code: str | None = None, **fields: Any) -> None:
        # Whitelist metadata: caller cannot send message text, tokens or file contents.
        safe = {key: value for key, value in fields.items()
                if key in {"method", "route", "intent", "tool", "model_version", "provider",
                           "prompt_version", "tool_calls", "token_usage", "cost",
                           "storage_provider", "transaction_id"}}
        payload = {"timestamp": datetime.now(timezone.utc).isoformat(), "level": "INFO" if status == "ok" else "ERROR",
                   "environment": environment, "request_id": request_id, "user_id": user_id,
                   "run_id": run_id, "component": component, "event": event,
                   "duration": duration, "status": status, "error_code": error_code, **safe}
        LOGGER.info(json.dumps(payload, ensure_ascii=False))
        with self._lock:
            self._counts[event] += 1
            if status != "ok":
                self._counts["errors"] += 1
            if duration is not None:
                values = self._durations[event]
                values.append(duration)
                if len(values) > 1000:
                    del values[:len(values) - 1000]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"counts": dict(self._counts),
                    "average_duration_seconds": {key: round(sum(values) / len(values), 4)
                                                 for key, values in self._durations.items() if values}}


class SlidingWindowRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int, *, window_seconds: int = 60) -> bool:
        now = time.monotonic()
        with self._lock:
            events = self._events[key]
            while events and now - events[0] >= window_seconds:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True


T = TypeVar("T")


def retry(operation: Callable[[], T], *, max_attempts: int = 3,
          initial_backoff: float = 0.2,
          retryable: tuple[type[Exception], ...] = (TimeoutError, ConnectionError)) -> T:
    """Explicit opt-in for future external operations; no blanket retries of commands."""
    if max_attempts < 1 or initial_backoff < 0:
        raise ValueError("invalid_retry_policy")
    for attempt in range(max_attempts):
        try:
            return operation()
        except retryable:
            if attempt + 1 == max_attempts:
                raise
            time.sleep(initial_backoff * 2 ** attempt)
    raise AssertionError("unreachable")
