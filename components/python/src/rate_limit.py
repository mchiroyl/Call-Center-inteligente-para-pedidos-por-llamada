"""Small in-memory rate limiter for demo endpoints."""

import time
from collections import defaultdict, deque

from fastapi import HTTPException, status

_buckets: dict[str, deque[float]] = defaultdict(deque)


def enforce_rate_limit(key: str, *, limit: int, window_seconds: int) -> None:
    now = time.monotonic()
    bucket = _buckets[key]
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiadas solicitudes. Intente de nuevo en unos segundos.",
        )
    bucket.append(now)

