"""Cable records tracked by the registry."""

from __future__ import annotations

from dataclasses import dataclass

OVER_TEMPERATURE = 75.0
DEFAULT_RESTORE_DELTA = 5.0


@dataclass
class Cable:
    """One monitored cable section."""

    id: str
    cabin: str = ""
    temp: float = 0.0
    status: str = "normal"


@dataclass(frozen=True)
class TemperaturePolicy:
    """The single over temperature rule shared by history, console and protection.

    ``trip_limit`` trips the protection (inclusive).  Reset is gated by the
    lower ``restore_limit`` so a temperature hovering near the trip limit cannot
    chatter protection on and off.
    """

    trip_limit: float = OVER_TEMPERATURE
    restore_delta: float = DEFAULT_RESTORE_DELTA

    def __post_init__(self) -> None:
        if self.restore_delta < 0:
            raise ValueError("restore delta must be non negative")
        if self.restore_limit >= self.trip_limit:
            raise ValueError("restore limit must be below the trip limit")

    @property
    def restore_limit(self) -> float:
        return self.trip_limit - self.restore_delta

    def is_over(self, temp: float) -> bool:
        """Trip band: at or above the trip limit."""

        return temp >= self.trip_limit

    def is_restored(self, temp: float) -> bool:
        """Reset band: strictly below the lower restore limit."""

        return temp < self.restore_limit

    def band(self, temp: float) -> str:
        """``over`` at the trip limit, ``restored`` below the reset limit, else ``hysteresis``."""

        if self.is_over(temp):
            return "over"
        if self.is_restored(temp):
            return "restored"
        return "hysteresis"


DEFAULT_POLICY = TemperaturePolicy()


def status_for(temp: float, policy: TemperaturePolicy = DEFAULT_POLICY) -> str:
    """Classify a temperature against the shared over temperature policy."""

    return "over" if policy.is_over(temp) else "normal"
