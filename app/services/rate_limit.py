"""In-memory per-IP rate limits for generation endpoints (Gemini / PDF pack)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse

BURST_WINDOW_SECONDS = 10.0
BURST_MAX = 1
HOUR_WINDOW_SECONDS = 3600.0
HOUR_MAX = 10
RATE_LIMIT_ERROR = "rate_limit_exceeded"
RATE_LIMIT_MESSAGE = "Çox tez-tez sorğu gönderirsiniz. Zəhmət olmasa gözləyin."
GENERATION_PATHS = frozenset({"/api/process", "/api/demo", "/api/pack"})


class IpRateLimiter:
    """Sliding-window limiter: 1 request / 10s and 10 requests / hour per IP."""

    def __init__(self, burst_max: int = BURST_MAX, hour_max: int = HOUR_MAX) -> None:
        self.burst_max = burst_max
        self.hour_max = hour_max
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, ip: str) -> bool:
        now = time.monotonic()
        key = (ip or "unknown").strip() or "unknown"
        with self._lock:
            hits = self._hits[key]
            hour_cut = now - HOUR_WINDOW_SECONDS
            while hits and hits[0] < hour_cut:
                hits.popleft()
            if len(hits) >= self.hour_max:
                return False
            burst_cut = now - BURST_WINDOW_SECONDS
            recent = 0
            for stamp in reversed(hits):
                if stamp < burst_cut:
                    break
                recent += 1
                if recent >= self.burst_max:
                    return False
            hits.append(now)
            return True


def client_ip(request: Request) -> str:
    cf = (request.headers.get("cf-connecting-ip") or "").strip()
    if cf:
        return cf.split(",")[0].strip()[:64]
    forwarded = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip[:64]
    if request.client and request.client.host:
        return request.client.host[:64]
    return "unknown"


def rate_limit_response() -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"error": RATE_LIMIT_ERROR, "message": RATE_LIMIT_MESSAGE},
        headers={"Retry-After": "10"},
    )


class GenerationRateLimitMiddleware(BaseHTTPMiddleware):
    """Reject generation POSTs that exceed the per-IP budget before the handler runs."""

    def __init__(self, app, limiter: IpRateLimiter | None = None) -> None:
        super().__init__(app)
        self.limiter = limiter or IpRateLimiter()

    async def dispatch(self, request: Request, call_next) -> StarletteResponse:
        path = request.url.path.rstrip("/") or "/"
        if request.method == "POST" and path in GENERATION_PATHS:
            if not self.limiter.allow(client_ip(request)):
                return rate_limit_response()
        return await call_next(request)
