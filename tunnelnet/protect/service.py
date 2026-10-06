"""Latched over temperature protection controller.

A cable that crosses the trip limit is protected once, in a fixed order: the
cabin fan is stopped first, then the cabin is isolated (door sealed, inlet
valve closed).  The incident latches and is never undone automatically: only
after the temperature stays below the reset limit for a stable run of samples
does the incident become resettable, and a manual reset then restores the
actuators to their pre-trip states.

Both directions are idempotent.  Evaluating an already latched cable does not
re-actuate; it only retries safe-state steps that never confirmed, so a
partially failed action is driven back to the safe position.  Resetting a
cable with no incident is a no-op.
"""

from __future__ import annotations

import threading
import uuid
from typing import Protocol

from tunnelnet.audit import Record
from tunnelnet.cable import CableState
from tunnelnet.drain import utc_now

from .model import DEGRADED, NORMAL, RESETTABLE, TRIPPED, Incident
from .policy import ProtectPolicy
from .report import ProtectReport, ProtectState


class CableStates(Protocol):
    """Reads the current temperature and status word of each cable."""

    def get(self, cable_id: str) -> CableState | None:  # pragma: no cover - structural
        ...

    def list(self) -> list[CableState]:  # pragma: no cover - structural
        ...


class FanUnits(Protocol):
    """Stops and starts the fan that belongs to one cabin."""

    def stop(self, cabin: str) -> bool:  # pragma: no cover - structural
        ...

    def start(self, cabin: str) -> bool:  # pragma: no cover - structural
        ...

    def running(self, cabin: str) -> bool:  # pragma: no cover - structural
        ...


class Isolation(Protocol):
    """Seals and releases a cabin door and its inlet valve."""

    def seal_door(self, cabin_id: str) -> None:  # pragma: no cover - structural
        ...

    def release_door(self, cabin_id: str) -> None:  # pragma: no cover - structural
        ...

    def close_valve(self, cabin_id: str) -> None:  # pragma: no cover - structural
        ...

    def open_valve(self, cabin_id: str) -> None:  # pragma: no cover - structural
        ...

    def door_state(self, cabin_id: str) -> bool:  # pragma: no cover - structural
        ...

    def valve_state(self, cabin_id: str) -> bool:  # pragma: no cover - structural
        ...


class AuditSink(Protocol):
    """Receives one audit record per protection transition."""

    def append(self, record: Record) -> Record:  # pragma: no cover - structural
        ...


class ProtectService:
    """Evaluates cable temperatures and latches over temperature incidents."""

    def __init__(
        self,
        cables: CableStates,
        fans: FanUnits,
        cabins: Isolation,
        audit: AuditSink,
        policy: ProtectPolicy | None = None,
        clock: object = utc_now,
    ) -> None:
        self._cables = cables
        self._fans = fans
        self._cabins = cabins
        self._audit = audit
        self._policy = policy if policy is not None else ProtectPolicy()
        self._clock = clock
        self._incidents: dict[str, Incident] = {}
        self._lock = threading.RLock()

    def policy(self) -> ProtectPolicy:
        return self._policy

    def evaluate(self, cable_id: str) -> ProtectState | None:
        """Judge the cable's current temperature and act on a transition."""

        with self._lock:
            cable = self._cables.get(cable_id)
            if cable is None:
                return None
            incident = self._incidents.get(cable_id)
            if incident is None:
                if cable.temp >= self._policy.trip:
                    incident = self._trip(cable)
                return self._projection(cable, incident)
            if cable.temp >= self._policy.trip:
                incident.hold = 0
                if incident.state == RESETTABLE:
                    incident.state = TRIPPED
                if incident.state == DEGRADED:
                    self._secure(incident)
                    if incident.state == TRIPPED:
                        self._record("cable_protect_secured")
            elif cable.temp < self._policy.reset:
                incident.hold += 1
                if incident.state != RESETTABLE and incident.hold >= self._policy.hold_samples:
                    incident.state = RESETTABLE
                    self._record("cable_resettable")
            else:
                incident.hold = 0
            return self._projection(cable, incident)

    def reset(self, cable_id: str) -> ProtectState:
        """Manually reset a latched incident once it became resettable."""

        with self._lock:
            cable = self._cables.get(cable_id)
            if cable is None:
                raise ValueError(f"cable {cable_id} not found")
            incident = self._incidents.get(cable_id)
            if incident is None:
                return self._projection(cable, None)
            if incident.state != RESETTABLE:
                raise ValueError(
                    f"cable {cable_id} is not resettable "
                    f"(state={incident.state} hold={incident.hold}/{self._policy.hold_samples})"
                )
            cabin = incident.cabin
            if cabin:
                if incident.fan_was_running:
                    self._fans.start(cabin)
                try:
                    if incident.door_was_open:
                        self._cabins.release_door(cabin)
                    if incident.valve_was_open:
                        self._cabins.open_valve(cabin)
                except ValueError:
                    pass
            del self._incidents[cable_id]
            self._record("cable_reset")
            return self._projection(cable, None)

    def state_for(self, cable_id: str) -> ProtectState | None:
        with self._lock:
            cable = self._cables.get(cable_id)
            if cable is None:
                return None
            return self._projection(cable, self._incidents.get(cable_id))

    def states(self) -> list[ProtectState]:
        with self._lock:
            return [
                self._projection(cable, self._incidents.get(cable.id))
                for cable in self._cables.list()
            ]

    def latched_count(self) -> int:
        with self._lock:
            return len(self._incidents)

    def degraded_count(self) -> int:
        with self._lock:
            return sum(1 for incident in self._incidents.values() if incident.state == DEGRADED)

    def report(self) -> ProtectReport:
        return ProtectReport(
            states=self.states(),
            latched=self.latched_count(),
            degraded=self.degraded_count(),
            policy=self._policy,
        )

    def describe(self) -> str:
        return f"protect latched={self.latched_count()} degraded={self.degraded_count()}"

    def _trip(self, cable: CableState) -> Incident:
        cabin = cable.cabin
        door_open, valve_open = False, False
        if cabin:
            try:
                door_open = self._cabins.door_state(cabin)
                valve_open = self._cabins.valve_state(cabin)
            except ValueError:
                door_open, valve_open = False, False
        incident = Incident(
            cable_id=cable.id,
            cabin=cabin,
            state=DEGRADED,
            tripped_at=self._clock(),
            fan_was_running=self._fans.running(cabin) if cabin else False,
            door_was_open=door_open,
            valve_was_open=valve_open,
        )
        self._secure(incident)
        self._incidents[cable.id] = incident
        self._record("cable_protect" if incident.state == TRIPPED else "cable_protect_degraded")
        return incident

    def _secure(self, incident: Incident) -> None:
        """Drive every unconfirmed step to its safe position, fan first."""

        cabin = incident.cabin
        if not cabin:
            return
        if not incident.fan_secured:
            self._fans.stop(cabin)
            incident.fan_secured = not self._fans.running(cabin)
        if not incident.isolation_secured:
            try:
                self._cabins.seal_door(cabin)
                self._cabins.close_valve(cabin)
                incident.isolation_secured = (
                    not self._cabins.door_state(cabin) and not self._cabins.valve_state(cabin)
                )
            except ValueError:
                incident.isolation_secured = False
        if incident.fan_secured and incident.isolation_secured:
            incident.state = TRIPPED

    def _projection(self, cable: CableState, incident: Incident | None) -> ProtectState:
        cabin = cable.cabin
        fan_running = self._fans.running(cabin) if cabin else False
        isolated = False
        if cabin:
            try:
                isolated = not self._cabins.door_state(cabin) and not self._cabins.valve_state(cabin)
            except ValueError:
                isolated = False
        return ProtectState(
            cable_id=cable.id,
            cabin=cabin,
            state=NORMAL if incident is None else incident.state,
            temp=cable.temp,
            status=cable.status,
            latched=incident is not None,
            hold=0 if incident is None else incident.hold,
            hold_required=self._policy.hold_samples,
            fan_running=fan_running,
            isolated=isolated,
            fan_secured=True if incident is None else incident.fan_secured,
            isolation_secured=True if incident is None else incident.isolation_secured,
            tripped_at="" if incident is None else incident.tripped_at,
        )

    def _record(self, event: str) -> None:
        clock = self._clock
        stamp = clock() if callable(clock) else utc_now()
        self._audit.append(Record(id=uuid.uuid4().hex, event=event, at=stamp))
