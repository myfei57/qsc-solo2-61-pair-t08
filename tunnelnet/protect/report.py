"""Read only projections of the protection controller."""

from __future__ import annotations

from dataclasses import dataclass

from .policy import ProtectPolicy


@dataclass(frozen=True)
class ProtectState:
    """How one cable sits inside the protection state machine."""

    cable_id: str
    cabin: str
    state: str
    temp: float
    status: str
    latched: bool
    hold: int
    hold_required: int
    fan_running: bool
    isolated: bool
    fan_secured: bool
    isolation_secured: bool
    tripped_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "cable_id": self.cable_id,
            "cabin": self.cabin,
            "state": self.state,
            "temp": self.temp,
            "status": self.status,
            "latched": self.latched,
            "hold": self.hold,
            "hold_required": self.hold_required,
            "fan_running": self.fan_running,
            "isolated": self.isolated,
            "fan_secured": self.fan_secured,
            "isolation_secured": self.isolation_secured,
            "tripped_at": self.tripped_at,
        }


@dataclass(frozen=True)
class ProtectReport:
    """Every protection state together with the policy that judged it."""

    states: list[ProtectState]
    latched: int
    degraded: int
    policy: ProtectPolicy

    def as_dict(self) -> dict[str, object]:
        return {
            "items": [state.as_dict() for state in self.states],
            "latched": self.latched,
            "degraded": self.degraded,
            "policy": self.policy.as_dict(),
        }
