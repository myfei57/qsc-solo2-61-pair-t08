"""Over temperature and load statistics for the cable registry."""

from __future__ import annotations

from .model import TemperaturePolicy
from .registry import CableRegistry


def danger_count(
    registry: CableRegistry, policy: TemperaturePolicy | None = None
) -> int:
    rule = registry.policy() if policy is None else policy
    return sum(1 for temp in registry.temperatures() if rule.is_over(temp))


def healthy_count(
    registry: CableRegistry, policy: TemperaturePolicy | None = None
) -> int:
    rule = registry.policy() if policy is None else policy
    return sum(1 for temp in registry.temperatures() if not rule.is_over(temp))


def load_factor(registry: CableRegistry) -> float:
    temperatures = registry.temperatures()
    if not temperatures:
        return 0.0
    return sum(temperatures) / len(temperatures)
