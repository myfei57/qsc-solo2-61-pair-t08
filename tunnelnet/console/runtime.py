"""Wiring of every control component behind the console."""

from __future__ import annotations

from tunnelnet.access import AccessService
from tunnelnet.audit import AuditRegistry, MarkSet
from tunnelnet.cabin import PartitionTable, PowerBook, Registry as CabinRegistry, SuppressionLog
from tunnelnet.cable import CableRegistry, MovingAverage, OVER_TEMPERATURE, TemperaturePolicy
from tunnelnet.drain import DrainService, PumpUnit
from tunnelnet.env import Environment
from tunnelnet.fire import FireService
from tunnelnet.ns import Registry as NamespaceRegistry
from tunnelnet.patrol import PatrolService
from tunnelnet.protection import ProtectionConfig, ProtectionService
from tunnelnet.quota import QuotaService
from tunnelnet.store import Store
from tunnelnet.vent import FanBank, VentService

DEFAULT_ZONES = ("zone-1", "zone-2", "zone-3")
DEFAULT_THRESHOLD = OVER_TEMPERATURE
DEFAULT_STABLE_SAMPLES = 3


class Runtime:
    """Owns one instance of each control component for a single store."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.namespaces = NamespaceRegistry()
        self.cabins = CabinRegistry()
        self.partitions = PartitionTable()
        self.filter = MovingAverage(3)
        cable_policy = TemperaturePolicy(DEFAULT_THRESHOLD)
        self.cables = CableRegistry(cable_policy)
        self.env = Environment(self.filter, self.partitions, cable_policy.trip_limit)
        self.access = AccessService(store, self.cabins)
        self.pump = PumpUnit()
        self.drain = DrainService(self.cabins, self.pump, store)
        self.ventilation = VentService()
        self.fans = FanBank()
        self.suppression = SuppressionLog()
        self.dedupe = MarkSet()
        self.fire = FireService(store, self.ventilation, self.suppression, self.dedupe, DEFAULT_ZONES)
        self.patrol = PatrolService(self.cabins)
        self.power_book = PowerBook()
        self.quota = QuotaService(self.cabins, self.power_book)
        self.audit = AuditRegistry()
        self.protection = ProtectionService(
            self.cables,
            self.fans,
            self.cabins,
            config=ProtectionConfig(policy=cable_policy, stable_samples=DEFAULT_STABLE_SAMPLES),
        )

    def bind_protection_audit(self) -> None:
        """Attach the audit sink once the runtime is fully constructed."""

        from .audit_log import AuditSink

        self.protection.bind_auditor(AuditSink(self))
