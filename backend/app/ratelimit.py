"""Abuse control that stores nothing about the person filing.

Three buckets, chosen so that the limit lands on the resource being abused
rather than on an identity:

  office  - HMAC(department|pin). Slows a campaign against one office; a
            citizen reporting three different offices in a day is untouched.
  actor   - employee code, on the sign-in route. Staff are identified anyway.
  client  - HMAC(salt, address) for read routes only, where the salt is random
            per process and never persisted. The address is used and dropped
            inside one function; it is never logged, stored or returned.

Redis when REDIS_URL is set, in-process otherwise. The in-process limiter is
per-worker, which is fine for a single node and honest about its limits.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from dataclasses import dataclass
from typing import Protocol

from .config import get_settings
from .observability import RATE_LIMITED, log

logger = logging.getLogger("civiclens.ratelimit")


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    retry_after: int = 0


class Backend(Protocol):
    def hit(self, key: str, limit: int, window: int) -> Verdict: ...


class _MemoryBackend:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window: int) -> Verdict:
        now = time.time()
        with self._lock:
            stamps = [t for t in self._hits.get(key, []) if now - t < window]
            if len(stamps) >= limit:
                return Verdict(False, int(window - (now - stamps[0])) + 1)
            stamps.append(now)
            self._hits[key] = stamps
            if len(self._hits) > 100_000:  # crude ceiling; Redis is the real answer
                self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < window}
        return Verdict(True)


class _RedisBackend:
    def __init__(self, url: str) -> None:
        import redis  # imported lazily so the dependency stays optional

        self._r = redis.Redis.from_url(url, socket_timeout=0.25, socket_connect_timeout=0.25)

    def hit(self, key: str, limit: int, window: int) -> Verdict:
        try:
            pipe = self._r.pipeline()
            pipe.incr(key)
            pipe.expire(key, window, nx=True)
            count, _ = pipe.execute()
            # The synchronous client returns concrete values; the stubs allow
            # awaitables because the same class backs the async client.
            used = int(count)  # type: ignore[arg-type]
            if used > limit:
                return Verdict(False, int(self._r.ttl(key) or window))  # type: ignore[arg-type]
            return Verdict(True)
        except Exception as exc:
            # Fail open: a Redis outage must not stop somebody reporting a
            # bribe. The event is logged so the gap is visible.
            log(logger, logging.WARNING, "rate limiter unavailable, failing open",
                error_type=type(exc).__name__)
            return Verdict(True)


_backend: Backend | None = None


def _get_backend() -> Backend:
    global _backend
    if _backend is None:
        url = get_settings().redis_url
        if url:
            try:
                _backend = _RedisBackend(url)
                log(logger, logging.INFO, "rate limiter using redis")
            except Exception as exc:
                log(logger, logging.WARNING, "redis unavailable, using in-process limiter",
                    error_type=type(exc).__name__)
                _backend = _MemoryBackend()
        else:
            _backend = _MemoryBackend()
    return _backend


def reset_for_tests() -> None:
    global _backend
    _backend = None


def _digest(*parts: str) -> str:
    s = get_settings()
    msg = "|".join(parts).encode()
    return hmac.new(s.client_bucket_salt.encode(), msg, hashlib.sha256).hexdigest()[:32]


def office_bucket(department: str, pin: str) -> str:
    return "office:" + hashlib.sha256(f"{department}|{pin}".encode()).hexdigest()[:32]


def actor_bucket(employee_code: str) -> str:
    return "actor:" + hashlib.sha256(employee_code.encode()).hexdigest()[:32]


def client_bucket(address: str | None) -> str:
    """Ephemeral, salted, memory-only. Never call this on the intake path."""
    return "client:" + _digest(address or "unknown")


def check(bucket: str, limit: int, window_seconds: int, route: str = "-") -> Verdict:
    verdict = _get_backend().hit(bucket, limit, window_seconds)
    if not verdict.allowed:
        RATE_LIMITED.labels(route).inc()
    return verdict
