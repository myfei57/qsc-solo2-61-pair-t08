"""Read only projections of the over temperature protection service."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProtectionItemView:
    """Protection state of one cable."""

    cable_id: str
    cabin_id: str
    state: str
    dwell: int
    episodes: int
    fault: str
    fan_running: bool
    cabin_isolated: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "cable_id": self.cable_id,
            "cabin_id": self.cabin_id,
            "state": self.state,
            "dwell": self.dwell,
            "episodes": self.episodes,
            "fault": self.fault,
            "fan_running": self.fan_running,
            "cabin_isolated": self.cabin_isolated,
        }


@dataclass(frozen=True)
class ProtectionReport:
    """All cable protection rows together with counters."""

    items: tuple[ProtectionItemView, ...]
    active: int
    ready: int
    faults: int

    def as_dict(self) -> dict[str, object]:
        return {
            "items": [item.as_dict() for item in self.items],
            "active": self.active,
            "ready": self.ready,
            "faults": self.faults,
        }
