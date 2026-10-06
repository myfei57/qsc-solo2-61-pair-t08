"""Per cabin cooling fans used by the cable over temperature protection."""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class FanState:
    """Whether the cooling fan of one cabin is running."""

    cabin_id: str
    running: bool

    @property
    def status(self) -> str:
        return "running" if self.running else "stopped"

    def as_dict(self) -> dict[str, object]:
        return {"cabin_id": self.cabin_id, "running": self.running, "status": self.status}


class FanBank:
    """One cooling fan per cabin.

    Fans run by default.  ``stop`` and ``start`` are idempotent and report the
    resulting running state so callers can verify the actuator instead of
    trusting the command.
    """

    def __init__(self) -> None:
        self._running: dict[str, bool] = {}
        self._lock = threading.RLock()

    def stop(self, cabin_id: str) -> bool:
        with self._lock:
            self._running[cabin_id] = False
            return False

    def start(self, cabin_id: str) -> bool:
        with self._lock:
            self._running[cabin_id] = True
            return True

    def running(self, cabin_id: str) -> bool:
        with self._lock:
            return self._running.get(cabin_id, True)

    def state(self, cabin_id: str) -> FanState:
        return FanState(cabin_id=cabin_id, running=self.running(cabin_id))

    def cabins(self) -> list[str]:
        with self._lock:
            return sorted(self._running)

    def states(self) -> list[FanState]:
        return [self.state(cabin_id) for cabin_id in self.cabins()]

    def stopped_cabins(self) -> list[str]:
        return [cabin_id for cabin_id in self.cabins() if not self.running(cabin_id)]

    def describe(self) -> str:
        with self._lock:
            stopped = sum(1 for running in self._running.values() if not running)
            return f"fans total={len(self._running)} stopped={stopped}"
