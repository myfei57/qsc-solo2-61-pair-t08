"""Latched over temperature protection for cable sections."""

from .model import DEGRADED, NORMAL, RESETTABLE, TRIPPED, Incident
from .policy import HOLD_SAMPLES, RESET_TEMPERATURE, ProtectPolicy
from .report import ProtectReport, ProtectState
from .service import ProtectService

__all__ = [
    "DEGRADED",
    "HOLD_SAMPLES",
    "NORMAL",
    "RESETTABLE",
    "RESET_TEMPERATURE",
    "TRIPPED",
    "Incident",
    "ProtectPolicy",
    "ProtectReport",
    "ProtectService",
    "ProtectState",
]
