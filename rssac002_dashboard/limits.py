"""Protection for a public deployment: a request budget per client.

Each client gets a bucket of tokens that refills steadily. An ordinary request costs one
token and an expensive one costs more. A client that empties its bucket is told when to
come back. Browsing the dashboard never comes close; a script hammering it does.
"""

from __future__ import annotations

import threading
import time

MAX_CLIENTS = 20000


class RateLimiter:
    def __init__(self, per_minute: int, clock=time.monotonic):
        self.capacity = float(per_minute)
        self.per_second = per_minute / 60
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def wait_for(self, client: str, cost: float = 1) -> float:
        """Spend `cost` tokens for this client. Returns 0 when allowed, else the seconds until it would be."""
        if self.capacity <= 0:
            return 0
        cost = min(cost, self.capacity)
        now = self._clock()
        with self._lock:
            tokens, seen = self._buckets.get(client, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - seen) * self.per_second)
            if tokens < cost:
                self._buckets[client] = (tokens, now)
                return (cost - tokens) / self.per_second
            self._buckets[client] = (tokens - cost, now)
            if len(self._buckets) > MAX_CLIENTS:  # forget the clients not seen for the longest
                for stale in sorted(self._buckets, key=lambda key: self._buckets[key][1])[: MAX_CLIENTS // 2]:
                    del self._buckets[stale]
            return 0
