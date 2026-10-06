"""Behavioural tests for cable over temperature protection."""

from __future__ import annotations

import unittest

from tunnelnet.cabin import Cabin, Registry as CabinRegistry
from tunnelnet.cable import Cable, CableRegistry, TemperaturePolicy
from tunnelnet.protection import (
    ProtectionConfig,
    ProtectionService,
    ResetMode,
    State,
)
from tunnelnet.vent import FanBank


class RecordingAuditor:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def emit(self, event: str, cable_id: str) -> None:
        self.events.append((event, cable_id))


def build_service(
    stable_samples: int = 3,
    policy: TemperaturePolicy | None = None,
    cables: CableRegistry | None = None,
    cabins: CabinRegistry | None = None,
    fans: FanBank | None = None,
) -> tuple[ProtectionService, CableRegistry, FanBank, CabinRegistry, RecordingAuditor]:
    cables = cables or CableRegistry(policy or TemperaturePolicy())
    cabins = cabins or CabinRegistry()
    fans = fans or FanBank()
    for cable in cables.list():
        cabins.upsert(Cabin(id=cable.cabin))
    auditor = RecordingAuditor()
    service = ProtectionService(
        cables,
        fans,
        cabins,
        auditor,
        ProtectionConfig(policy=policy or TemperaturePolicy(), stable_samples=stable_samples),
    )
    return service, cables, fans, cabins, auditor


class PolicyCase(unittest.TestCase):
    def test_bands_and_hysteresis(self) -> None:
        policy = TemperaturePolicy(80.0, 10.0)
        self.assertTrue(policy.is_over(80.0))
        self.assertFalse(policy.is_over(79.9))
        self.assertTrue(policy.is_restored(69.9))
        self.assertFalse(policy.is_restored(70.0))
        self.assertEqual(policy.band(90.0), "over")
        self.assertEqual(policy.band(75.0), "hysteresis")
        self.assertEqual(policy.band(60.0), "restored")

    def test_invalid_policy(self) -> None:
        with self.assertRaises(ValueError):
            TemperaturePolicy(80.0, -1.0)
        with self.assertRaises(ValueError):
            TemperaturePolicy(80.0, 0.0)
        with self.assertRaises(ValueError):
            ProtectionConfig(stable_samples=0)
        with self.assertRaises(ValueError):
            ProtectionConfig(reset_mode=ResetMode.AUTO)


class ProtectionCase(unittest.TestCase):
    def setUp(self) -> None:
        registry = CableRegistry()
        registry.add(Cable(id="cable-1", cabin="cabin-1", temp=40.0))
        self.service, self.cables, self.fans, self.cabins, self.audit = build_service(cables=registry)

    def test_trip_stops_fan_before_isolating_cabin(self) -> None:
        order: list[str] = []

        class OrderedFans(FanBank):
            def stop(self, cabin_id: str) -> bool:
                order.append("fan_stop")
                return super().stop(cabin_id)

        class OrderedCabins(CabinRegistry):
            def __init__(self, fan_bank: FanBank) -> None:
                super().__init__()
                self._fan_bank = fan_bank

            def isolate(self, cabin_id: str) -> bool:
                order.append("cabin_isolate")
                # The fan must already be stopped when isolation runs.
                assert not self._fan_bank.running(cabin_id)
                return super().isolate(cabin_id)

        registry = CableRegistry()
        registry.add(Cable(id="cable-1", cabin="cabin-1", temp=40.0))
        ordered_fans = OrderedFans()
        ordered_cabins = OrderedCabins(ordered_fans)
        ordered_cabins.upsert(Cabin(id="cabin-1"))
        service, *_ = build_service(cables=registry, cabins=ordered_cabins, fans=ordered_fans)

        outcome = service.evaluate("cable-1", 90.0)
        self.assertEqual(outcome.state, State.TRIPPED.value)
        self.assertEqual(outcome.action, "trip")
        self.assertEqual(order, ["fan_stop", "cabin_isolate"])
        self.assertFalse(ordered_fans.running("cabin-1"))
        self.assertTrue(ordered_cabins.isolate("cabin-1"))
        self.assertEqual(service.episodes("cable-1"), 1)

    def test_manual_reset_needs_stable_cooldown(self) -> None:
        self.service.evaluate("cable-1", 90.0)

        # Reset is denied while still hot, and the denial is audited.
        denied = self.service.reset("cable-1")
        self.assertEqual(denied.action, "denied")
        self.assertEqual(denied.state, State.TRIPPED.value)
        self.assertTrue(self.fans.stopped_cabins())

        # Below trip but inside the hysteresis band does not accumulate dwell.
        self.service.evaluate("cable-1", 74.0)
        self.assertEqual(self.service.state_of("cable-1"), State.TRIPPED.value)

        # Restore band needs stable_samples consecutive readings.
        for value in (65.0, 65.0):
            self.service.evaluate("cable-1", value)
            self.assertEqual(self.service.state_of("cable-1"), State.TRIPPED.value)
        ready = self.service.evaluate("cable-1", 65.0)
        self.assertEqual(ready.state, State.READY.value)
        self.assertEqual(ready.action, "ready")

        # Even when ready the actuators stay held until a human confirms.
        self.assertFalse(self.fans.running("cabin-1"))
        self.assertFalse(self.cabins.door_state("cabin-1"))

        outcome = self.service.reset("cable-1")
        self.assertEqual(outcome.action, "reset")
        self.assertEqual(outcome.state, State.NORMAL.value)
        self.assertTrue(self.fans.running("cabin-1"))
        self.assertTrue(self.cabins.door_state("cabin-1"))
        self.assertTrue(self.cabins.valve_state("cabin-1"))

    def test_rebound_inside_hysteresis_relatches(self) -> None:
        self.service.evaluate("cable-1", 90.0)
        for value in (65.0, 65.0, 65.0):
            self.service.evaluate("cable-1", value)
        self.assertEqual(self.service.state_of("cable-1"), State.READY.value)

        # 74 is below the 75 trip limit but above the 70 restore limit.
        outcome = self.service.evaluate("cable-1", 74.0)
        self.assertEqual(outcome.action, "relatch")
        self.assertEqual(outcome.state, State.TRIPPED.value)
        self.assertEqual(self.service.reset("cable-1").action, "denied")

    def test_repeated_over_temperature_relatches_and_resets_dwell(self) -> None:
        self.service.evaluate("cable-1", 90.0)
        for value in (65.0, 65.0, 65.0):
            self.service.evaluate("cable-1", value)
        self.assertEqual(self.service.state_of("cable-1"), State.READY.value)

        relatch = self.service.evaluate("cable-1", 90.0)
        self.assertEqual(relatch.action, "relatch")
        self.assertEqual(relatch.state, State.TRIPPED.value)
        self.assertEqual(self.service.dwell("cable-1"), 0)

        # A hot reading during tripped keeps the dwell at zero.
        self.service.evaluate("cable-1", 65.0)
        self.service.evaluate("cable-1", 65.0)
        self.service.evaluate("cable-1", 90.0)
        self.service.evaluate("cable-1", 65.0)
        self.assertEqual(self.service.state_of("cable-1"), State.TRIPPED.value)
        self.assertEqual(self.service.dwell("cable-1"), 1)
        self.assertEqual(self.service.episodes("cable-1"), 1)

    def test_steady_over_temperature_trips_once(self) -> None:
        first = self.service.evaluate("cable-1", 90.0)
        self.assertTrue(first.changed)
        for _ in range(5):
            outcome = self.service.evaluate("cable-1", 90.0)
            self.assertFalse(outcome.changed)
        self.assertEqual(self.service.episodes("cable-1"), 1)

    def test_repeated_reset_is_idempotent(self) -> None:
        self.service.evaluate("cable-1", 90.0)
        for value in (65.0, 65.0, 65.0):
            self.service.evaluate("cable-1", value)
        first = self.service.reset("cable-1")
        second = self.service.reset("cable-1")
        self.assertTrue(first.changed)
        self.assertEqual(second.action, "noop")
        self.assertFalse(second.changed)

    def test_partial_trip_is_retried_until_safe_position(self) -> None:
        registry = CableRegistry()
        registry.add(Cable(id="cable-1", cabin="cabin-1", temp=40.0))
        cabins = CabinRegistry()
        cabins.upsert(Cabin(id="cabin-1"))

        class FlakyIsolation(CabinRegistry):
            def __init__(self) -> None:
                super().__init__()
                self.attempts = 0

            def isolate(self, cabin_id: str) -> bool:
                self.attempts += 1
                if self.attempts == 1:
                    return False
                return super().isolate(cabin_id)

        flaky = FlakyIsolation()
        flaky.upsert(Cabin(id="cabin-1"))
        fan_bank = FanBank()
        service, _, returned_fans, _, auditor = build_service(
            cables=registry, cabins=flaky, fans=fan_bank
        )
        self.assertIs(returned_fans, fan_bank)

        outcome = service.evaluate("cable-1", 90.0)
        self.assertEqual(outcome.state, State.TRIPPING.value)
        self.assertEqual(outcome.fault, "trip_incomplete")
        # The fan stays stopped while the cabin isolation is retried.
        self.assertFalse(fan_bank.running("cabin-1"))
        self.assertEqual(auditor.events[-1][0], "otp_trip_failed")

        retry = service.evaluate("cable-1", 90.0)
        self.assertEqual(retry.state, State.TRIPPED.value)
        self.assertEqual(retry.fault, "")
        self.assertEqual(auditor.events[-1][0], "otp_trip")
        self.assertEqual(service.report().faults, 0)

    def test_failed_fan_stop_holds_in_tripping(self) -> None:
        registry = CableRegistry()
        registry.add(Cable(id="cable-1", cabin="cabin-1", temp=40.0))
        cabins = CabinRegistry()
        cabins.upsert(Cabin(id="cabin-1"))

        class StuckFan(FanBank):
            def stop(self, cabin_id: str) -> bool:
                # The stop command is ignored: never record the stopped state.
                return True

            def running(self, cabin_id: str) -> bool:
                return True

        cabins.release_door("cabin-1")
        cabins.open_valve("cabin-1")
        stuck = StuckFan()
        service, *_ = build_service(cables=registry, cabins=cabins, fans=stuck)
        cabins.release_door("cabin-1")
        cabins.open_valve("cabin-1")
        outcome = service.evaluate("cable-1", 90.0)
        self.assertEqual(outcome.state, State.TRIPPING.value)
        # Cabin must never isolate while the fan is still running.
        self.assertTrue(cabins.door_state("cabin-1"))

    def test_failed_recovery_returns_to_safe_position(self) -> None:
        registry = CableRegistry()
        registry.add(Cable(id="cable-1", cabin="cabin-1", temp=40.0))
        cabins = CabinRegistry()
        cabins.upsert(Cabin(id="cable-1"))

        class FailStartOnceFan(FanBank):
            def __init__(self) -> None:
                super().__init__()
                self._attempts = 0

            def start(self, cabin_id: str) -> bool:
                self._attempts += 1
                if self._attempts == 1:
                    return False
                return super().start(cabin_id)

            def running(self, cabin_id: str) -> bool:
                # Until a start call succeeds the fan stays stopped.
                return self._attempts >= 2 and super().running(cabin_id)

        fans = FailStartOnceFan()
        service, *_ = build_service(cables=registry, cabins=cabins, fans=fans)

        service.evaluate("cable-1", 90.0)
        for value in (65.0, 65.0, 65.0):
            service.evaluate("cable-1", value)
        outcome = service.reset("cable-1")
        self.assertEqual(outcome.action, "reset_failed")
        self.assertEqual(outcome.state, State.TRIPPED.value)
        self.assertEqual(outcome.fault, "reset_incomplete")
        # Safe position: cabin re-sealed, fan still stopped.
        self.assertFalse(cabins.door_state("cabin-1"))
        self.assertFalse(fans.running("cable-1"))
        # Needs a fresh stable cooldown before another reset is accepted.
        self.assertEqual(service.reset("cable-1").action, "denied")
        for value in (65.0, 65.0, 65.0):
            service.evaluate("cable-1", value)
        retried = service.reset("cable-1")
        self.assertEqual(retried.action, "reset")
        self.assertEqual(retried.state, State.NORMAL.value)

    def test_two_cables_sharing_a_cabin_are_reference_counted(self) -> None:
        registry = CableRegistry()
        registry.add(Cable(id="cable-a", cabin="cabin-1", temp=40.0))
        registry.add(Cable(id="cable-b", cabin="cabin-1", temp=40.0))
        service, _, fans, cabins, _ = build_service(cables=registry)

        service.evaluate("cable-a", 90.0)
        service.evaluate("cable-b", 90.0)
        for cable_id in ("cable-a", "cable-b"):
            for _ in range(3):
                service.evaluate(cable_id, 65.0)
        self.assertEqual(service.reset("cable-a").action, "reset")
        # Cable b is still protected: the cabin stays isolated and fan stopped.
        self.assertFalse(fans.running("cabin-1"))
        self.assertFalse(cabins.door_state("cabin-1"))
        self.assertEqual(service.reset("cable-b").action, "reset")
        self.assertTrue(fans.running("cabin-1"))
        self.assertTrue(cabins.door_state("cabin-1"))

    def test_report_and_unknown_cable(self) -> None:
        self.assertEqual(self.service.evaluate("ghost", 90.0).action, "ignored")
        self.assertEqual(self.service.reset("ghost").action, "ignored")
        self.assertIsNone(self.service.view("ghost"))
        report = self.service.report()
        self.assertEqual(report.active, 0)
        self.assertEqual(report.faults, 0)
        self.service.evaluate("cable-1", 90.0)
        report = self.service.report()
        self.assertEqual(report.active, 1)
        self.assertEqual(report.items[0].cabin_isolated, True)
        self.assertEqual(report.items[0].fan_running, False)
        self.assertIn("cables=1", self.service.describe())

    def test_audit_trail_covers_every_action(self) -> None:
        events = self.audit.events
        self.service.evaluate("cable-1", 90.0)
        self.assertEqual(events[0], ("otp_trip", "cable-1"))
        for value in (65.0, 65.0, 65.0):
            self.service.evaluate("cable-1", value)
        self.assertEqual(events[-1], ("otp_ready", "cable-1"))
        self.service.reset("cable-1")
        self.assertEqual(events[-1], ("otp_reset", "cable-1"))


class FanBankCase(unittest.TestCase):
    def test_idempotent_start_and_stop(self) -> None:
        bank = FanBank()
        self.assertTrue(bank.running("cabin-1"))
        self.assertFalse(bank.stop("cabin-1"))
        self.assertFalse(bank.stop("cabin-1"))
        self.assertEqual(bank.stopped_cabins(), ["cabin-1"])
        self.assertTrue(bank.start("cabin-1"))
        self.assertEqual(bank.state("cabin-1").status, "running")
        self.assertEqual(bank.state("cabin-1").as_dict()["status"], "running")
        self.assertIn("fans total=1", bank.describe())


class CabinIsolationCase(unittest.TestCase):
    def test_isolate_and_reinstate_are_verified(self) -> None:
        registry = CabinRegistry()
        registry.upsert(Cabin(id="cabin-1"))
        registry.release_door("cabin-1")
        registry.open_valve("cabin-1")
        self.assertTrue(registry.isolate("cabin-1"))
        self.assertFalse(registry.door_state("cabin-1"))
        self.assertFalse(registry.valve_state("cabin-1"))
        self.assertTrue(registry.reinstate("cabin-1"))
        self.assertTrue(registry.door_state("cabin-1"))
        self.assertTrue(registry.valve_state("cabin-1"))


if __name__ == "__main__":
    unittest.main()
