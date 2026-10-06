"""One ventilation fan per cabin, each running unless explicitly stopped."""

from __future__ import annotations

import threading


class FanBank:
    """Tracks the fans the over temperature protection stops per cabin."""

    def __init__(self) -> None:
        self._stopped: set[str] = set()
        self._lock = threading.RLock()

    def stop(self, cabin: str) -> bool:
        """Stop one cabin fan, returning whether it is confirmed stopped."""

        with self._lock:
            self._stopped.add(cabin)
            return cabin in self._stopped

    def start(self, cabin: str) -> bool:
        """Start one cabin fan, returning whether it is confirmed running."""

        with self._lock:
            self._stopped.discard(cabin)
            return cabin not in self._stopped

    def running(self, cabin: str) -> bool:
        with self._lock:
            return cabin not in self._stopped

    def stopped(self) -> list[str]:
        with self._lock:
            return sorted(self._stopped)

    def describe(self) -> str:
        return f"fans stopped={len(self.stopped())}"
