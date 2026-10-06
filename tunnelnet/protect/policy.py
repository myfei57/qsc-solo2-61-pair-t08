"""Over temperature protection policy shared by every cable judgement."""

from __future__ import annotations

from dataclasses import dataclass

from tunnelnet.cable.model import OVER_TEMPERATURE

RESET_TEMPERATURE = 70.0
HOLD_SAMPLES = 3


@dataclass(frozen=True)
class ProtectPolicy:
    """Trip and reset limits plus the stability hold before a reset is allowed.

    The trip limit is the same ``OVER_TEMPERATURE`` constant that classifies
    the cable status word, so the history, the console pages and the
    protection controller all judge with one shared limit.  The reset limit
    sits below the trip limit (hysteresis) so a reading hovering at the
    boundary cannot flap the protection.
    """

    trip: float = OVER_TEMPERATURE
    reset: float = RESET_TEMPERATURE
    hold_samples: int = HOLD_SAMPLES

    def __post_init__(self) -> None:
        if self.reset >= self.trip:
            raise ValueError("reset limit must be below the trip limit")
        if self.hold_samples < 1:
            raise ValueError("hold_samples must be at least 1")

    def as_dict(self) -> dict[str, object]:
        return {"trip": self.trip, "reset": self.reset, "hold_samples": self.hold_samples}
