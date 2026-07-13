"""
Simple in-memory rate limiter for DC Atlas.

No Redis or external dependencies. Uses process-local dictionary.
Resets on bot restart — acceptable for v1.
"""

import time
from collections import defaultdict
from typing import Optional


class RateLimitRule:
    """Defines a rate limit: max_count actions per window_seconds."""

    def __init__(self, max_count: int, window_seconds: int):
        self.max_count = max_count
        self.window_seconds = window_seconds


class RateLimitService:
    """In-memory rate limiter. Resets on restart."""

    def __init__(self):
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str, rule: RateLimitRule) -> tuple[bool, int]:
        """
        Check if an action is allowed.

        Args:
            key: Unique identifier (e.g. "user:<dc_id>:add_group").
            rule: Rate limit rule to apply.

        Returns:
            (allowed: bool, remaining: int)
        """
        now = time.time()
        window_start = now - rule.window_seconds

        # Clean old entries
        timestamps = self._buckets[key]
        self._buckets[key] = [t for t in timestamps if t > window_start]

        current_count = len(self._buckets[key])
        remaining = max(0, rule.max_count - current_count)

        if current_count >= rule.max_count:
            return False, remaining

        self._buckets[key].append(now)
        return True, remaining - 1

    def reset(self, key: Optional[str] = None) -> None:
        """Reset rate limit for a key or all keys."""
        if key:
            self._buckets.pop(key, None)
        else:
            self._buckets.clear()


# Default rules from TZ section 14.2
DEFAULT_RULES = {
    "add_group": RateLimitRule(5, 3600),
    "add_channel": RateLimitRule(5, 3600),
    "add_tg": RateLimitRule(10, 3600),
    "report": RateLimitRule(20, 3600),
    "search": RateLimitRule(60, 3600),
}
