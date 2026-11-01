"""Per-source circuit breaker: after `threshold` consecutive failures a source is skipped for an increasing cooldown."""
from __future__ import annotations

import threading
import time

COOLDOWNS = (300, 900, 3600)          # 5 min, 15 min, 1 h


class Breaker:
    def __init__(self, threshold: int = 3, clock=time.time):
        self.threshold, self.clock, self.state, self.lock = threshold, clock, {}, threading.Lock()

    def allow(self, key) -> bool:
        with self.lock:
            s = self.state.get(key)
            return not s or s["open_until"] <= self.clock()          # closed, or cooldown over (half-open probe)

    def success(self, key):
        with self.lock:
            self.state[key] = {"fails": 0, "trips": 0, "open_until": 0.0}

    def failure(self, key):
        with self.lock:
            s = self.state.setdefault(key, {"fails": 0, "trips": 0, "open_until": 0.0})
            s["fails"] += 1
            if s["fails"] >= self.threshold:
                s["open_until"] = self.clock() + COOLDOWNS[min(s["trips"], len(COOLDOWNS) - 1)]
                s["trips"] += 1; s["fails"] = 0

    def retry_in(self, key) -> int:
        with self.lock:
            s = self.state.get(key)
            return max(0, int(s["open_until"] - self.clock())) if s else 0
