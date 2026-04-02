"""In-process sliding-window rate limiter for upload endpoints.

Keyed by user_id.  No external dependency (no Redis) — works for a
single-process deployment.  For multi-process / multi-replica deployments
replace the in-memory store with a shared cache (Redis + INCR/EXPIRE).
"""

import asyncio
import time
from collections import deque

from fastapi import Depends, HTTPException, status

from ..config import settings
from ..dependencies import get_current_user
from ..models.user import User


class _SlidingWindowLimiter:
    """Thread-safe sliding-window counter keyed by an integer user id."""

    def __init__(self) -> None:
        self._buckets: dict[int, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def check(
        self,
        user_id: int,
        max_calls: int,
        window_seconds: int,
    ) -> tuple[bool, int]:
        """Return *(allowed, retry_after_seconds)*.

        *retry_after_seconds* is 0 when *allowed* is True.
        """
        async with self._lock:
            now = time.monotonic()
            cutoff = now - window_seconds
            bucket = self._buckets.setdefault(user_id, deque())

            # Evict timestamps outside the window
            while bucket and bucket[0] < cutoff:
                bucket.popleft()

            if len(bucket) >= max_calls:
                # Time until the oldest entry leaves the window
                retry_after = int(bucket[0] - cutoff) + 1
                return False, retry_after

            bucket.append(now)
            return True, 0


# Singleton — shared across all requests within the process lifetime
_upload_limiter = _SlidingWindowLimiter()


async def require_upload_rate_ok(
    current_user: User = Depends(get_current_user),
) -> None:
    """FastAPI dependency: raise HTTP 429 when the caller exceeds the
    configured upload rate limit."""
    allowed, retry_after = await _upload_limiter.check(
        current_user.id,
        settings.upload_rate_limit_per_minute,
        60,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"アップロードリクエストが多すぎます。"
                f"{retry_after} 秒後に再試行してください。"
            ),
            headers={"Retry-After": str(retry_after)},
        )
