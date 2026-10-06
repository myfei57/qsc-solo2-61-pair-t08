"""State model for cable over temperature protection (OTP)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from tunnelnet.cable import TemperaturePolicy


class State(str, Enum):
    """Protection state of one cable."""

    NORMAL = "normal"
    TRIPPING = "tripping"
    TRIPPED = "tripped"
    READY = "ready"

    @property
    def protected(self) -> bool:
        """True while the cabin must remain held in the protected position."""

        return self is not State.NORMAL


class ResetMode(str, Enum):
    """How a cable leaves the protected position.

    Only :attr:`MANUAL` is wired: auto recovery after the temperature falls
    would re-enable the fan and open the cabin with no one watching, and
    repeated over temperature would cycle actuators on their own.  The enum is
    kept so the decision is explicit in code and cannot silently drift.
    """

    MANUAL = "manual"
    AUTO = "auto"


@dataclass(frozen=True)
class ProtectionConfig:
    """Tuning of the protection state machine."""

    policy: TemperaturePolicy = TemperaturePolicy()
    stable_samples: int = 3
    reset_mode: ResetMode = ResetMode.MANUAL

    def __post_init__(self) -> None:
        if self.stable_samples < 1:
            raise ValueError("stable samples must be positive")
        if self.reset_mode is not ResetMode.MANUAL:
            raise ValueError("over temperature recovery requires manual confirmation")


@dataclass(frozen=True)
class ProtectionOutcome:
    """Result of one temperature evaluation or one reset request."""

    cable_id: str
    state: str
    changed: bool
    action: str = ""
    fault: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "cable_id": self.cable_id,
            "state": self.state,
            "changed": self.changed,
            "action": self.action,
            "fault": self.fault,
        }
