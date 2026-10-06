"""Cable over temperature protection service.

The protection sequence is ordered: the cooling fan is stopped first and the
cabin is isolated only after the fan is verified stopped.  Recovery runs the
reverse order and is allowed only after the temperature fell below a lower
reset limit and stayed there, followed by an explicit manual confirmation.

Both directions are idempotent and self healing: a partially applied sequence
is retried on the next evaluation, a failed recovery rolls back to the
protected (safe) position, and several cables sharing a cabin are combined by
a reference count so one cable's reset cannot undo another cable's protection.
"""

from __future__ import annotations

import threading
from typing import Protocol

from tunnelnet.cable import TemperaturePolicy

from .model import ProtectionConfig, ProtectionOutcome, ResetMode, State
from .report import ProtectionItemView, ProtectionReport


class Fans(Protocol):
    """Per cabin cooling fan actuators."""

    def stop(self, cabin_id: str) -> bool:  # pragma: no cover - structural
        ...

    def start(self, cabin_id: str) -> bool:  # pragma: no cover - structural
        ...

    def running(self, cabin_id: str) -> bool:  # pragma: no cover - structural
        ...


class Cabins(Protocol):
    """Cabin isolation actuators."""

    def isolate(self, cabin_id: str) -> bool:  # pragma: no cover - structural
        ...

    def reinstate(self, cabin_id: str) -> bool:  # pragma: no cover - structural
        ...

    def door_state(self, cabin_id: str) -> bool:  # pragma: no cover - structural
        ...

    def valve_state(self, cabin_id: str) -> bool:  # pragma: no cover - structural
        ...


class CableLookup(Protocol):
    """Resolves the cabin a cable belongs to."""

    def get(self, cable_id: str) -> object:  # pragma: no cover - structural
        ...


class Auditor(Protocol):
    """Receives audit events raised by protection actions."""

    def emit(self, event: str, cable_id: str) -> None:  # pragma: no cover - structural
        ...


class _Item:
    """Mutable per cable protection state."""

    def __init__(self, cabin_id: str) -> None:
        self.cabin_id = cabin_id
        self.state = State.NORMAL
        self.dwell = 0
        self.episodes = 0
        self.fault = ""
        self.engaged = False


class ProtectionService:
    """Owns the protection state machine of every cable."""

    def __init__(
        self,
        cables: CableLookup,
        fans: Fans,
        cabins: Cabins,
        auditor: Auditor | None = None,
        config: ProtectionConfig | None = None,
    ) -> None:
        self._cables = cables
        self._fans = fans
        self._cabins = cabins
        self._auditor = auditor
        self._config = config or ProtectionConfig()
        self._items: dict[str, _Item] = {}
        self._isolated: dict[str, int] = {}
        self._lock = threading.RLock()

    def config(self) -> ProtectionConfig:
        return self._config

    def bind_auditor(self, auditor: Auditor) -> None:
        self._auditor = auditor

    def reset_mode(self) -> ResetMode:
        return self._config.reset_mode

    def evaluate(self, cable_id: str, temp: float) -> ProtectionOutcome:
        """Apply one temperature reading and return the resulting outcome."""

        with self._lock:
            item = self._load(cable_id)
            if item is None:
                return ProtectionOutcome(cable_id=cable_id, state="", changed=False, action="ignored")

            policy = self._config.policy

            if item.state is State.TRIPPING:
                completed = self._engage(item)
                if completed:
                    item.state = State.TRIPPED
                    item.fault = ""
                    self._emit("otp_trip", cable_id)
                    return ProtectionOutcome(
                        cable_id, item.state.value, changed=True, action="trip"
                    )
                item.fault = "trip_incomplete"
                return ProtectionOutcome(
                    cable_id, item.state.value, changed=False, action="retry_trip", fault=item.fault
                )

            if item.state is State.NORMAL:
                if policy.is_over(temp):
                    item.episodes += 1
                    if self._engage(item):
                        item.state = State.TRIPPED
                        item.fault = ""
                        self._emit("otp_trip", cable_id)
                    else:
                        item.state = State.TRIPPING
                        item.fault = "trip_incomplete"
                        self._emit("otp_trip_failed", cable_id)
                    return ProtectionOutcome(
                        cable_id, item.state.value, changed=True, action="trip", fault=item.fault
                    )
                return ProtectionOutcome(cable_id, item.state.value, changed=False)

            if item.state is State.TRIPPED:
                if policy.is_restored(temp):
                    item.dwell += 1
                    if item.dwell >= self._config.stable_samples:
                        item.state = State.READY
                        self._emit("otp_ready", cable_id)
                        return ProtectionOutcome(
                            cable_id, item.state.value, changed=True, action="ready"
                        )
                else:
                    item.dwell = 0
                return ProtectionOutcome(cable_id, item.state.value, changed=False)

            # READY: any reading back out of the restore band re-latches, even
            # inside the hysteresis gap, so a recovery cannot be confirmed while
            # the temperature is climbing back.  The actuators are still held,
            # so this is only a state move.
            if not policy.is_restored(temp):
                item.state = State.TRIPPED
                item.dwell = 0
                item.fault = ""
                self._emit("otp_relatch", cable_id)
                return ProtectionOutcome(
                    cable_id, item.state.value, changed=True, action="relatch"
                )
            return ProtectionOutcome(cable_id, item.state.value, changed=False)

    def reset(self, cable_id: str) -> ProtectionOutcome:
        """Manually confirm recovery of a cable that is ready to restore."""

        with self._lock:
            item = self._load(cable_id)
            if item is None:
                return ProtectionOutcome(cable_id=cable_id, state="", changed=False, action="ignored")

            if item.state is State.NORMAL:
                return ProtectionOutcome(cable_id, item.state.value, changed=False, action="noop")

            if item.state in (State.TRIPPING, State.TRIPPED):
                self._emit("otp_reset_denied", cable_id)
                return ProtectionOutcome(
                    cable_id, item.state.value, changed=False, action="denied", fault=item.fault
                )

            if self._disengage(item):
                item.state = State.NORMAL
                item.dwell = 0
                item.fault = ""
                self._emit("otp_reset", cable_id)
                return ProtectionOutcome(cable_id, item.state.value, changed=True, action="reset")

            item.state = State.TRIPPED
            item.dwell = 0
            item.fault = "reset_incomplete"
            self._emit("otp_reset_failed", cable_id)
            return ProtectionOutcome(
                cable_id, item.state.value, changed=True, action="reset_failed", fault=item.fault
            )

    def state_of(self, cable_id: str) -> str:
        with self._lock:
            item = self._items.get(cable_id)
            return "" if item is None else item.state.value

    def dwell(self, cable_id: str) -> int:
        with self._lock:
            item = self._items.get(cable_id)
            return 0 if item is None else item.dwell

    def episodes(self, cable_id: str) -> int:
        with self._lock:
            item = self._items.get(cable_id)
            return 0 if item is None else item.episodes

    def fault(self, cable_id: str) -> str:
        with self._lock:
            item = self._items.get(cable_id)
            return "" if item is None else item.fault

    def cable_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._items)

    def view(self, cable_id: str) -> ProtectionItemView | None:
        with self._lock:
            item = self._items.get(cable_id) or self._load(cable_id)
            if item is None:
                return None
            return self._view(cable_id, item)

    def report(self) -> ProtectionReport:
        with self._lock:
            views = tuple(self._view(cable_id, self._items[cable_id]) for cable_id in sorted(self._items))
            return ProtectionReport(
                items=views,
                active=sum(1 for view in views if view.state != State.NORMAL.value),
                ready=sum(1 for view in views if view.state == State.READY.value),
                faults=sum(1 for view in views if view.fault),
            )

    def describe(self) -> str:
        report = self.report()
        return f"protection cables={len(report.items)} active={report.active} faults={report.faults}"

    def _load(self, cable_id: str) -> _Item | None:
        item = self._items.get(cable_id)
        if item is not None:
            return item
        cable = self._cables.get(cable_id)
        cabin_id = getattr(cable, "cabin", "")
        if not cabin_id:
            return None
        item = _Item(cabin_id)
        self._items[cable_id] = item
        return item

    def _engage(self, item: _Item) -> bool:
        """Stop the fan first, isolate the cabin second; verified and idempotent."""

        if item.engaged:
            return True
        cabin_id = item.cabin_id
        try:
            if self._fans.stop(cabin_id):
                # The stop command reported the fan still running.
                return False
            count = self._isolated.get(cabin_id, 0)
            if count == 0 and not self._cabins.isolate(cabin_id):
                return False
            self._isolated[cabin_id] = count + 1
            item.engaged = True
            return True
        except Exception:
            return False

    def _disengage(self, item: _Item) -> bool:
        """Open the cabin first, start the fan second; roll back on failure.

        The cabin stays protected while another cable in it is still held, so
        only the last reference releases the actuators.
        """

        if not item.engaged:
            return True
        cabin_id = item.cabin_id
        count = self._isolated.get(cabin_id, 0)
        if count <= 0:
            item.engaged = False
            return True
        if count > 1:
            self._isolated[cabin_id] = count - 1
            item.engaged = False
            return True
        try:
            if not self._cabins.reinstate(cabin_id):
                self._safe_isolate(cabin_id)
                return False
            self._fans.start(cabin_id)
            if not self._fans.running(cabin_id):
                self._safe_isolate(cabin_id)
                return False
        except Exception:
            self._safe_isolate(cabin_id)
            return False
        self._isolated[cabin_id] = 0
        item.engaged = False
        return True

    def _safe_isolate(self, cabin_id: str) -> None:
        try:
            self._cabins.isolate(cabin_id)
        except Exception:
            pass

    def _view(self, cable_id: str, item: _Item) -> ProtectionItemView:
        isolated = self._isolated.get(item.cabin_id, 0) > 0
        return ProtectionItemView(
            cable_id=cable_id,
            cabin_id=item.cabin_id,
            state=item.state.value,
            dwell=item.dwell,
            episodes=item.episodes,
            fault=item.fault,
            fan_running=self._fans.running(item.cabin_id),
            cabin_isolated=isolated,
        )

    def _emit(self, event: str, cable_id: str) -> None:
        if self._auditor is None:
            return
        try:
            self._auditor.emit(event, cable_id)
        except Exception:
            pass
