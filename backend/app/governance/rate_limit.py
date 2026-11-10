"""
Sliding-window rate limit for cross-validation: at most N checks per window for
each ORDERED agent pair (A asking B is counted separately from B asking A).

State is in memory, per process. That is the right size for the single Render
instance this backend runs on; with several instances each would allow N, so the
real ceiling would be N times the instance count. Documented, not hidden.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

MAX_CHECKS = 5
WINDOW_SECONDS = 60.0


class PairRateLimiter:
    def __init__(self, max_calls: int = MAX_CHECKS, window: float = WINDOW_SECONDS, clock=time.monotonic):
        self.max_calls = max_calls
        self.window = window
        self._clock = clock
        self._calls: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def acquire(self, agent_a: str, agent_b: str) -> float:
        """
        Take one slot. Returns 0.0 if allowed, otherwise the seconds to wait
        (nothing is recorded for a refused call).
        """
        now = self._clock()
        with self._lock:
            calls = self._calls[(agent_a, agent_b)]
            while calls and now - calls[0] >= self.window:
                calls.popleft()
            if len(calls) >= self.max_calls:
                return max(0.001, self.window - (now - calls[0]))
            calls.append(now)
            return 0.0

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()


limiter = PairRateLimiter()
