"""Process-lifetime serialized compatible-prefix external-statistics publisher.

Why this module exists
----------------------
``publication_adapter`` (retained, unwired) tried to prove *import-lineage
completion* in-process: it waited for Recorder queue settlement and treated a
bounded read-back as an ownership release. That contract is not supported by HA
2026.9.4 -- ``async_block_till_done`` returns while an import task is dequeued
but uncommitted, and a cancelled/timed-out read never proves the executor thread
stopped. The lifecycle counterexamples are recorded in ``PRACTICAL-PATH.md``.

This module implements the reduced-scope alternative that those counterexamples
actually justify: **append-compatible publication with an immutable submitted
prefix**. It needs no drain guarantee, no receipt and no executor framework.

Contract
--------
One owner per private external statistic id, for the whole process lifetime:

* A publisher claims its physical ``statistic_id`` in a process-global registry at
  construction -- not the logical ``stream_id``, which is only an alias for the
  same Recorder series. The claim is *retained*: an entry unload stops new
  admissions but does not release the claim, so the same process can never
  construct a second writer for that statistic id under any stream id. Only a
  genuinely new process (a fresh module instance / fresh registry) may replace
  it; binding a *restarted* process to previously published Recorder history
  needs durable cross-process provenance and is a separate integration gate, not
  claimed here.
* Before the first submission in a process the publisher runs a fail-closed
  identity pre-flight: **any** already-occupied statistic id is refused
  (``blocked_ownership``) rather than upserted over unknown history. Ledger
  presence is not ownership evidence -- an offline-created, never-published
  ledger satisfies it exactly as an established owner would, so an unexplained
  overlap, a foreign pre-origin row or a foreign suffix row would otherwise be
  adopted or overwritten. No baseline is fabricated. The only positive owner in
  this scope is the process that confirmed the identity empty *before* its own
  first enqueue: it keeps ordinary replay/append rights for the prefix it froze.
* Every payload is a **full-origin absolute replay** of one approved ledger
  snapshot: hour start -> encoded ``state``/``sum``. Overlap with the submitted
  prefix must match *exactly*; a changed hour, a removed hour, a prefixed earlier
  origin / interior insert (backfill) or any metadata drift is rejected
  **before enqueue**, and stays rejected after success, after a verification
  timeout and after unload. New payloads may only extend the contiguous complete
  suffix.
* Every encoded state/sum/meta key is marked submitted **before** enqueue. A
  failed, timed-out or superseded enqueue therefore can never be replayed later
  with a different value: the prefix is frozen from the moment we hand it to the
  queue.
* A bounded read-back confirms **visibility only** -- never that a queue is
  drained, never that all retries are done. On timeout/error the publisher stops
  admissions for the process (degraded), leaves the ledger generation pending,
  and reports the last verified coverage. There is no retry storm and no queue
  polling loop beyond the bounded visibility deadline.

Deliberately *not* here: no automatic in-process historical correction, no hot
reload of a corrected prefix, no drain framework, no live ESB wiring, no
Recorder SQL/private queue markers. Correction/backfill reconciliation happens
at a separately authorized full HA-process boundary, after the previous process
has exited.

Dependencies
------------
Only the approved frozen ledger (``.ledger``) plus *public* HA Recorder APIs.
This module deliberately does not import ``publication_adapter`` or
``statistics`` (both retained/unapproved prototype paths), so an approved new
artifact never depends on unapproved code.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Protocol, Sequence

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_metadata,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_conversion import EnergyConverter

from .ledger import (
    DEFAULT_UNIT,
    NORMALIZATION_CONTRACT_ID,
    IntervalLedger,
    IntervalReading,
    PublicationSnapshot,
    UnknownStreamError,
)
from . import energy_runtime as energy_runtime

# --------------------------------------------------------------------------- ids

EXTERNAL_SOURCE = "esb_smart_meter"
EXTERNAL_UNIT_OF_MEASUREMENT = "kWh"
EXTERNAL_UNIT_CLASS = EnergyConverter.UNIT_CLASS
EXTERNAL_STATISTIC_ID_SUFFIX = "_grid_import"
EXTERNAL_STATISTIC_NAME = "ESB grid import (verified interval history)"

# --------------------------------------------------------------------- outcomes

STATUS_READY = "ready"
STATUS_PUBLISHED = "published"
STATUS_NOTHING_NEW = "nothing_new"
STATUS_BLOCKED_INCOMPLETE = "blocked_incomplete"
STATUS_BLOCKED_OWNERSHIP = "blocked_ownership"
STATUS_CORRECTION_PENDING = "correction_pending"
STATUS_ENQUEUE_FAILED = "enqueue_failed"
STATUS_VERIFY_FAILED = "verify_failed"
STATUS_DEGRADED = "degraded"
STATUS_FENCED = "fenced"

#: Reason codes for a payload that is not append-compatible with the prefix.
CORRECTION_CHANGED_HOUR = "changed_submitted_hour"
CORRECTION_REMOVED_HOUR = "removed_submitted_hour"
CORRECTION_BACKFILL = "prepend_or_interior_hour"
CORRECTION_METADATA_DRIFT = "metadata_drift"

#: Reason codes for a snapshot that may not be published at all.
BLOCK_EMPTY_SNAPSHOT = "snapshot_empty"
BLOCK_INCOMPLETE_HOUR = "snapshot_incomplete_hour"
BLOCK_HOUR_GAP = "snapshot_hour_gap"
#: Any occupied hour refuses the identity at first admission; ledger presence is
#: not ownership evidence, so the reason is not conditioned on a missing stream.
BLOCK_OCCUPIED_STATISTIC_ID = "occupied_statistic_id"

#: Reason codes for a refused durable establishment/resume (collaborator present).
BLOCK_OWNER_RECORD_CORRUPT = "owner_record_corrupt"
BLOCK_INVENTORY_UNREADABLE = "inventory_unreadable"
BLOCK_OWNER_BINDING_MISMATCH = "owner_binding_mismatch"
BLOCK_LEDGER_WITHOUT_OWNER = "ledger_without_owner_record"
BLOCK_LEDGER_PROVENANCE = "ledger_provenance_mismatch"
BLOCK_RECORDER_PROVENANCE = "recorder_provenance_mismatch"
BLOCK_MISSING_VERIFIED_HISTORY = "missing_verified_history"
BLOCK_OWNER_RECORD_WRITE_FAILED = "owner_record_write_failed"
CORRECTION_ORIGIN_MISMATCH = "origin_mismatch"

DEGRADED_DURABLE_WRITE_FAILED = "durable_write_failed"
DEGRADED_ACKNOWLEDGE_FAILED = "acknowledge_failed"

DEFAULT_STARTUP_TIMEOUT = 10.0

DEGRADED_VERIFICATION_FAILED = "verification_failed"

# ------------------------------------------------------------------- constants

DEFAULT_VERIFY_TIMEOUT = 5.0
DEFAULT_SETTLE_TIMEOUT = 30.0
VERIFY_POLL_INTERVAL = 0.05

#: Earliest instant a Recorder statistic row can represent. The inventory read is
#: open-ended from here so "occupied" is decided over the whole identity.
INVENTORY_START = datetime(1970, 1, 1, tzinfo=timezone.utc)

_HOUR = timedelta(hours=1)
_MICRO_KWH = Decimal("0.000001")
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_]*[a-z0-9])?$")


class CompatiblePublisherError(Exception):
    """A compatible-publisher contract violation."""


class DuplicateOwnerError(CompatiblePublisherError):
    """A second writer was constructed for a stream already owned this process."""


class SnapshotNotPublishable(CompatiblePublisherError):
    """A ledger snapshot is not a contiguous run of complete UTC hours."""

    def __init__(self, reason: str, *, hour: datetime | None = None) -> None:
        super().__init__(f"snapshot not publishable: {reason}")
        self.reason = reason
        self.hour = hour


# --------------------------------------------------------------------- encoding


def statistic_id_for_slug(slug: str) -> str:
    """Build the external statistic id for an opaque per-stream slug."""
    return f"{EXTERNAL_SOURCE}:{slug}{EXTERNAL_STATISTIC_ID_SUFFIX}"


def to_recorder_float(value: Any) -> float:
    """Encode a ledger fixed-point value as a float without losing micro-kWh."""
    if isinstance(value, bool):
        raise CompatiblePublisherError("statistic value must be numeric, not bool")
    if isinstance(value, Decimal):
        decimal_value = value
    elif isinstance(value, int):
        decimal_value = Decimal(value)
    elif isinstance(value, float):
        decimal_value = Decimal(str(value))
    else:
        raise CompatiblePublisherError(f"statistic value must be numeric, got {type(value)!r}")
    if not decimal_value.is_finite():
        raise CompatiblePublisherError("statistic value must be finite")
    encoded = float(decimal_value)
    if not math.isfinite(encoded):
        raise CompatiblePublisherError("statistic value does not encode to a finite float")
    if Decimal(str(encoded)).quantize(_MICRO_KWH, rounding=ROUND_HALF_UP) != decimal_value.quantize(
        _MICRO_KWH, rounding=ROUND_HALF_UP
    ):
        raise CompatiblePublisherError("statistic value loses micro-kWh precision as a float")
    return encoded


@dataclass(frozen=True)
class EncodedMetadata:
    """The immutable external-statistics identity a stream is bound to."""

    statistic_id: str
    source: str
    unit: str
    unit_class: str
    mean_type: Any
    has_sum: bool


@dataclass(frozen=True)
class EncodedPayload:
    """One full-origin absolute payload: ascending contiguous complete UTC hours.

    ``rows`` is ``(hour_start, state, sum)``; ``state`` and ``sum`` both carry the
    ledger snapshot's cumulative known-only state, so the payload is idempotent
    and needs no delta arithmetic.
    """

    metadata: EncodedMetadata
    rows: tuple[tuple[datetime, float, float], ...]

    def validate(self) -> "EncodedPayload":
        """Fail closed on a hand-built payload that is not contiguous UTC hours."""
        previous: datetime | None = None
        for hour, state, total in self.rows:
            if not isinstance(hour, datetime) or hour.tzinfo is None or hour.utcoffset() != timedelta(0):
                raise CompatiblePublisherError("payload hours must be offset-aware UTC")
            if hour.minute or hour.second or hour.microsecond:
                raise CompatiblePublisherError("payload hours must be exact UTC hour boundaries")
            if previous is not None and hour != previous + _HOUR:
                raise CompatiblePublisherError("payload hours must be strictly ascending and contiguous")
            if not (math.isfinite(state) and math.isfinite(total)):
                raise CompatiblePublisherError("payload values must be finite")
            previous = hour
        return self

    def value_map(self) -> dict[datetime, tuple[float, float]]:
        return {hour: (state, total) for hour, state, total in self.rows}


def encode_snapshot(identity: "StreamIdentity", snapshot: PublicationSnapshot) -> EncodedPayload:
    """Encode an approved ledger snapshot as an append-compatible absolute payload.

    Only a contiguous run of **complete** UTC hours is publishable. A trailing
    partial hour, an interior gap or an empty snapshot is refused before anything
    is marked submitted, so a gap can never be silently zero-filled and an
    extension can never jump an unpublished watermark.
    """
    if not snapshot.rows:
        raise SnapshotNotPublishable(BLOCK_EMPTY_SNAPSHOT)
    rows: list[tuple[datetime, float, float]] = []
    expected = snapshot.rows[0].hour_start
    for row in snapshot.rows:
        if not row.complete:
            raise SnapshotNotPublishable(BLOCK_INCOMPLETE_HOUR, hour=row.hour_start)
        if row.hour_start != expected:
            raise SnapshotNotPublishable(BLOCK_HOUR_GAP, hour=row.hour_start)
        value = to_recorder_float(row.state_kwh)
        rows.append((row.hour_start, value, value))
        expected = expected + _HOUR
    return EncodedPayload(metadata=identity.metadata(), rows=tuple(rows)).validate()


# --------------------------------------------------------------------- identity


@dataclass(frozen=True)
class StreamIdentity:
    """Immutable binding of an opaque stream to one external statistic id."""

    slug: str
    stream_id: str
    statistic_id: str
    contract_id: str = NORMALIZATION_CONTRACT_ID

    def validate(self) -> "StreamIdentity":
        if not isinstance(self.slug, str) or not _SLUG_RE.match(self.slug):
            raise CompatiblePublisherError(f"invalid stream slug {self.slug!r}")
        if not isinstance(self.stream_id, str) or not self.stream_id:
            raise CompatiblePublisherError("stream_id must be a non-empty string")
        if self.statistic_id != statistic_id_for_slug(self.slug):
            raise CompatiblePublisherError(
                f"statistic_id {self.statistic_id!r} does not match slug {self.slug!r}"
            )
        if not isinstance(self.contract_id, str) or not self.contract_id:
            raise CompatiblePublisherError("contract_id must be a non-empty string")
        return self

    def metadata(self) -> EncodedMetadata:
        return EncodedMetadata(
            statistic_id=self.statistic_id,
            source=EXTERNAL_SOURCE,
            unit=EXTERNAL_UNIT_OF_MEASUREMENT,
            unit_class=EXTERNAL_UNIT_CLASS,
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
        )


class ProcessOwnerRegistry:
    """Process-lifetime single-writer claim per physical statistic id.

    A claim is never released in-process: ``CompatiblePublisher.async_unload``
    only closes admissions. A replacement writer requires a new process, which
    re-imports this module and therefore starts from a fresh registry. (Tests
    inject a fresh registry to model a new process; that is an explicit
    simulation, not a real process boundary.)
    """

    def __init__(self) -> None:
        self._owners: dict[str, object] = {}

    def claim(self, statistic_id: str, owner: object) -> None:
        if statistic_id in self._owners:
            raise DuplicateOwnerError(
                f"statistic id {statistic_id!r} already has a publisher in this process"
            )
        self._owners[statistic_id] = owner

    def __contains__(self, statistic_id: object) -> bool:
        return statistic_id in self._owners

    def __len__(self) -> int:
        return len(self._owners)


_PROCESS_OWNERS = ProcessOwnerRegistry()


# -------------------------------------------------------------------- port API


class ExternalStatisticsPort(Protocol):
    """The thin seam between the pure publisher logic and HA Recorder."""

    async def inventory(self, statistic_id: str) -> tuple[datetime, ...]:
        """Every hour already recorded for ``statistic_id`` (open-ended)."""
        raise NotImplementedError

    async def inspect(self, statistic_id: str) -> "IdentityInspection":
        """Full-identity view: metadata plus every occupied hour -> state/sum."""
        raise NotImplementedError

    async def enqueue(self, payload: EncodedPayload) -> None:
        """Queue one absolute payload. A return value is not a receipt."""
        raise NotImplementedError

    async def verify_visible(self, payload: EncodedPayload, *, timeout: float) -> bool:
        """Bounded read-back: True only if the payload rows are observable."""
        raise NotImplementedError


@dataclass(frozen=True)
class IdentityInspection:
    """Whole-identity Recorder evidence for one statistic id (startup validation).

    ``metadata`` is the unconverted Recorder metadata mapping and ``rows`` maps every
    occupied UTC hour to its ``(state, sum)``. Metadata-only occupancy is deliberately
    representable (``metadata`` set, ``rows`` empty) so it is never mistaken for an
    empty identity.
    """

    statistic_id: str
    metadata: Any | None
    rows: dict[datetime, tuple[Any, Any]]


@dataclass(frozen=True)
class PublishOutcome:
    """Result of one serialized publication step."""

    status: str
    reason: str | None = None
    generation: int | None = None
    submitted_hours: int = 0
    last_verified_hour: datetime | None = None
    degraded_reason: str | None = None

    @property
    def published(self) -> bool:
        return self.status == STATUS_PUBLISHED

    @property
    def pending(self) -> bool:
        """A payload reached the queue but its visibility was never confirmed."""
        return self.status in (STATUS_ENQUEUE_FAILED, STATUS_VERIFY_FAILED)


# ------------------------------------------------------------------- publisher


class CompatiblePublisher:
    """Serialized, append-compatible, process-lifetime publisher for one stream."""

    def __init__(
        self,
        identity: StreamIdentity,
        *,
        ledger: IntervalLedger | None,
        port: ExternalStatisticsPort,
        registry: ProcessOwnerRegistry | None = None,
        verify_timeout: float = DEFAULT_VERIFY_TIMEOUT,
        owner_store: energy_runtime.RuntimeOwnerStorage | None = None,
        ledger_operations: energy_runtime.RuntimeLedgerOperations | None = None,
        entry_id: str | None = None,
        source_binding: energy_runtime.SourceBinding | None = None,
        origin: datetime | None = None,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
    ) -> None:
        identity.validate()
        if owner_store is not None and (ledger_operations is None or not entry_id or source_binding is None or origin is None):
            raise ValueError("durable ownership requires complete collaborators and binding")
        if owner_store is None and ledger is None:
            raise ValueError("ledger required")
        self._owner_store = owner_store
        self._operations = ledger_operations
        self._entry_id = entry_id
        self._source_binding = source_binding
        self._origin = origin
        self._startup_timeout = float(startup_timeout)
        if not math.isfinite(self._startup_timeout) or self._startup_timeout <= 0:
            raise ValueError("startup timeout must be finite and positive")
        self._record = None
        self._recover_pending = False
        self._identity = identity
        self._ledger = ledger
        self._port = port
        self._registry = _PROCESS_OWNERS if registry is None else registry
        self._verify_timeout = float(verify_timeout)

        # Retained process-lifetime claim; never released by unload.
        self._registry.claim(identity.statistic_id, self)

        self._submitted: dict[datetime, tuple[float, float]] = {}
        self._submitted_metadata: EncodedMetadata | None = None
        self._last_verified_hour: datetime | None = None
        self._degraded_reason: str | None = None
        self._initialized = False
        self._admitting = True
        self._lock = asyncio.Lock()

    # -- introspection -----------------------------------------------------

    @property
    def identity(self) -> StreamIdentity:
        return self._identity

    @property
    def admitting(self) -> bool:
        return self._admitting

    @property
    def degraded(self) -> bool:
        return self._degraded_reason is not None

    @property
    def degraded_reason(self) -> str | None:
        return self._degraded_reason

    @property
    def last_verified_hour(self) -> datetime | None:
        return self._last_verified_hour

    @property
    def submitted_hours(self) -> int:
        return len(self._submitted)

    @property
    def submitted_values(self) -> dict[datetime, tuple[float, float]]:
        """The frozen submitted prefix (hour -> encoded state/sum). Never cleared."""
        return dict(self._submitted)

    # -- compatibility guard (pure) ----------------------------------------

    def check_compatible(self, payload: EncodedPayload) -> str | None:
        """Return a rejection reason when ``payload`` is not append-compatible.

        Overlap with the submitted prefix must match exactly; a submitted hour may
        not be removed; new hours must extend the contiguous suffix after the last
        submitted hour (no prepended origin, no interior backfill); and the bound
        metadata must be identical. ``None`` means compatible.
        """
        payload.validate()
        if not self._submitted:
            return None
        if payload.metadata != self._submitted_metadata:
            return CORRECTION_METADATA_DRIFT
        candidate = payload.value_map()
        for hour, prior in self._submitted.items():
            current = candidate.get(hour)
            if current is None:
                return CORRECTION_REMOVED_HOUR
            if current != prior:
                return CORRECTION_CHANGED_HOUR
        new_hours = [hour for hour in candidate if hour not in self._submitted]
        if not new_hours:
            return None
        if min(new_hours) <= max(self._submitted):
            return CORRECTION_BACKFILL
        return None

    # -- lifecycle ---------------------------------------------------------

    async def async_initialize(self) -> PublishOutcome:
        """Fail-closed identity pre-flight; call once before the first submission.

        Any occupied statistic id is refused. Ledger presence is not ownership
        evidence, and no baseline is fabricated.
        """
        async with self._lock:
            return await self._initialize_locked()

    async def async_unload(self) -> None:
        """Stop admissions. The process-lifetime claim is intentionally retained."""
        self._admitting = False

    # -- publication -------------------------------------------------------

    async def async_merge_and_publish(self, readings: Iterable[IntervalReading]) -> PublishOutcome:
        """Merge one normalized batch, then publish whatever is now pending."""
        async with self._lock:
            if not self._admitting:
                return self._outcome(STATUS_FENCED)
            if not self._initialized:
                # Fail closed *before* the merge: merging would create the very
                # ledger state that makes an already-occupied statistic id look
                # owned, so a pre-flight run afterwards can no longer see it.
                blocked = await self._initialize_locked()
                if blocked.status != STATUS_READY:
                    return blocked
            if not self._admitting:
                return self._outcome(STATUS_FENCED)
            await self._ledger_call("merge",
                self._identity.stream_id,
                readings,
                unit=DEFAULT_UNIT,
                contract_id=self._identity.contract_id,
            )
            return await self._publish_pending_locked()

    async def async_publish_pending(self) -> PublishOutcome:
        """Publish the ledger's current pending generation, if any."""
        async with self._lock:
            return await self._publish_pending_locked()

    async def async_publish_snapshot(self, snapshot: PublicationSnapshot) -> PublishOutcome:
        """Publish one explicit approved ledger snapshot."""
        async with self._lock:
            return await self._publish_locked(snapshot)

    # -- internals ---------------------------------------------------------

    async def _publish_pending_locked(self) -> PublishOutcome:
        if self._owner_store is not None and not self._initialized:
            outcome = await self._initialize_locked()
            if outcome.status != STATUS_READY:
                return outcome
        try:
            snapshot = await self._ledger_call("pending_publication", self._identity.stream_id)
        except UnknownStreamError:
            return self._outcome(STATUS_NOTHING_NEW)
        if snapshot is None:
            return self._outcome(STATUS_NOTHING_NEW)
        return await self._publish_locked(snapshot)

    async def _publish_locked(self, snapshot: PublicationSnapshot) -> PublishOutcome:
        try:
            payload = encode_snapshot(self._identity, snapshot)
        except SnapshotNotPublishable as error:
            return self._outcome(
                STATUS_BLOCKED_INCOMPLETE, reason=error.reason, generation=snapshot.generation
            )

        # The compatibility guard runs first: a correction stays rejected even
        # after a verification timeout or an unload, because the submitted prefix
        # outlives both.
        conflict = self.check_compatible(payload)
        if conflict is not None:
            return self._outcome(
                STATUS_CORRECTION_PENDING, reason=conflict, generation=snapshot.generation
            )

        if not self._admitting:
            return self._outcome(STATUS_FENCED, generation=snapshot.generation)

        if self._degraded_reason is not None:
            return self._outcome(
                STATUS_DEGRADED, reason=self._degraded_reason, generation=snapshot.generation
            )

        if not self._initialized:
            blocked = await self._initialize_locked()
            if blocked.status != STATUS_READY:
                return blocked

        if self._owner_store is not None:
            actual_snapshot = await self._operations.async_publication_snapshot(self._identity.stream_id)
            if actual_snapshot != snapshot:
                return self._outcome(STATUS_BLOCKED_OWNERSHIP, reason=BLOCK_LEDGER_PROVENANCE)
            if not self._admitting:
                return self._outcome(STATUS_FENCED)
        if self._owner_store is not None and payload.rows[0][0] != self._origin:
            return self._outcome(STATUS_CORRECTION_PENDING, reason=CORRECTION_ORIGIN_MISMATCH)
        candidate = payload.value_map()
        if candidate == self._submitted and not self._recover_pending:
            return self._outcome(STATUS_NOTHING_NEW, generation=snapshot.generation)

        # Mark every encoded state/sum/meta key as submitted BEFORE enqueue: an
        # enqueue that fails or times out can still have queued the write, so the
        # prefix is frozen from this instant and no later payload may differ.
        self._submitted_metadata = payload.metadata
        self._submitted.update(candidate)

        replay_visible = False
        if self._recover_pending:
            try:
                inspection = await self._bounded_inspect()
            except BaseException as error:
                self._admitting = False
                self._degraded_reason = BLOCK_INVENTORY_UNREADABLE
                if isinstance(error, asyncio.CancelledError):
                    raise
                if not isinstance(error, Exception):
                    raise
                return self._outcome(STATUS_DEGRADED, reason=self._degraded_reason)
            replay_visible = _rows_visible(inspection.rows, candidate) and _metadata_matches(inspection.metadata, payload.metadata)
            self._recover_pending = False
        if self._owner_store is not None:
            if payload.rows[0][0] != self._origin:
                return self._outcome(STATUS_CORRECTION_PENDING, reason=CORRECTION_ORIGIN_MISMATCH)
            next_record = replace(self._record, submitted_rows=payload.rows, submitted_generation=snapshot.generation)
            if not await self._persist(next_record):
                return self._outcome(STATUS_DEGRADED, reason=self._degraded_reason)
        if not self._admitting:
            return self._outcome(STATUS_FENCED)
        try:
            if not replay_visible:
                await self._port.enqueue(payload)
        except asyncio.CancelledError:
            self._admitting = False
            raise
        except Exception as error:  # noqa: BLE001 - transport failure is reported
            return self._outcome(
                STATUS_ENQUEUE_FAILED, reason=type(error).__name__, generation=snapshot.generation
            )

        verify_error: str | None = None
        try:
            visible = replay_visible or await self._port.verify_visible(payload, timeout=self._verify_timeout)
        except asyncio.CancelledError:
            self._admitting = False
            raise
        except Exception as error:  # noqa: BLE001 - a read-back error is not a licence to retry
            visible = False
            verify_error = type(error).__name__

        if not visible:
            # Stop admissions for the process, leave the generation pending and
            # keep the last verified coverage. Never a retry storm, never a
            # "queue drained" claim.
            self._degraded_reason = DEGRADED_VERIFICATION_FAILED
            return self._outcome(
                STATUS_VERIFY_FAILED,
                reason=verify_error or DEGRADED_VERIFICATION_FAILED,
                generation=snapshot.generation,
            )

        if not self._admitting:
            return self._outcome(STATUS_FENCED)
        if self._owner_store is not None:
            if not await self._persist(replace(self._record, verified_length=len(payload.rows), verified_generation=snapshot.generation)):
                return self._outcome(STATUS_DEGRADED, reason=self._degraded_reason)
        if not self._admitting:
            return self._outcome(STATUS_FENCED)
        self._last_verified_hour = payload.rows[-1][0]
        try:
            acknowledged = await self._ledger_call("acknowledge", self._identity.stream_id, snapshot.generation)
        except asyncio.CancelledError:
            self._admitting = False
            raise
        except Exception:
            acknowledged = False
        if acknowledged is not True:
            self._degraded_reason = DEGRADED_ACKNOWLEDGE_FAILED
            return self._outcome(STATUS_DEGRADED, reason=self._degraded_reason, generation=snapshot.generation)
        return self._outcome(STATUS_PUBLISHED, generation=snapshot.generation)

    async def _initialize_locked(self) -> PublishOutcome:
        if self._initialized:
            return self._outcome(STATUS_READY)
        if not self._admitting:
            return self._outcome(STATUS_FENCED)
        if self._degraded_reason:
            return self._outcome(STATUS_DEGRADED, reason=self._degraded_reason)
        if self._owner_store is not None:
            return await self._initialize_durable()
        try:
            async with asyncio.timeout(self._startup_timeout):
                occupied = tuple(await self._port.inventory(self._identity.statistic_id))
        except Exception:
            return self._outcome(STATUS_BLOCKED_OWNERSHIP, reason=BLOCK_INVENTORY_UNREADABLE)
        if occupied:
            return self._outcome(
                STATUS_BLOCKED_OWNERSHIP, reason=BLOCK_OCCUPIED_STATISTIC_ID
            )
        self._initialized = True
        return self._outcome(STATUS_READY)

    async def _ledger_call(self, operation, *args, **kwargs):
        if self._operations is not None:
            result = await getattr(self._operations, "async_" + operation)(*args, **kwargs)
        else:
            result = getattr(self._ledger, operation)(*args, **kwargs)
        return result

    async def _bounded_inspect(self):
        async with asyncio.timeout(self._startup_timeout):
            return await self._port.inspect(self._identity.statistic_id)

    async def _persist(self, record):
        try:
            current = await self._owner_store.async_load()
            if current != self._record:
                raise energy_runtime.OwnerRecordWriteError("durable provenance changed")
            reopened = await self._owner_store.async_save(record)
            if reopened != record:
                raise energy_runtime.OwnerRecordWriteError("readback mismatch")
            self._record = reopened
            return True
        except BaseException as error:
            self._degraded_reason = DEGRADED_DURABLE_WRITE_FAILED
            self._admitting = False
            if isinstance(error, asyncio.CancelledError):
                raise
            if not isinstance(error, Exception):
                raise
            return False

    def _empty_record(self):
        return energy_runtime.OwnerRecord(
            entry_id=self._entry_id, slug=self._identity.slug,
            statistic_id=self._identity.statistic_id, source=EXTERNAL_SOURCE,
            product=energy_runtime.PRODUCT_INTERVAL_KWH, source_binding=self._source_binding,
            ledger_contract=self._identity.contract_id, unit=DEFAULT_UNIT,
            unit_class=EXTERNAL_UNIT_CLASS, mean_type=int(StatisticMeanType.NONE),
            has_sum=True, origin=self._origin, submitted_generation=0)

    async def _initialize_durable(self):
        reason = BLOCK_OWNER_RECORD_CORRUPT
        try:
            async with asyncio.timeout(self._startup_timeout):
                record = await self._owner_store.async_load()
                reason = BLOCK_LEDGER_PROVENANCE
                state = await self._operations.async_read_stream_state(self._identity.stream_id)
                reason = BLOCK_INVENTORY_UNREADABLE
                inspection = await self._port.inspect(self._identity.statistic_id)
                if inspection.statistic_id != self._identity.statistic_id:
                    raise ValueError("identity mismatch")
                expected = self._empty_record().validate()
                if record is None:
                    if state is not None or self._operations.ledger_exists:
                        reason = BLOCK_LEDGER_WITHOUT_OWNER
                        raise ValueError("unowned ledger")
                    if inspection.metadata is not None or inspection.rows:
                        reason = BLOCK_OCCUPIED_STATISTIC_ID
                        raise ValueError("occupied")
                    if not await self._persist(expected):
                        return self._outcome(STATUS_DEGRADED, reason=self._degraded_reason)
                else:
                    reason = BLOCK_OWNER_BINDING_MISMATCH
                    if replace(record, submitted_generation=0, submitted_rows=(), verified_length=0, verified_generation=0) != expected:
                        raise ValueError("binding mismatch")
                    reason = BLOCK_LEDGER_PROVENANCE
                    if state is None:
                        if record.submitted_rows or self._operations.ledger_exists:
                            raise ValueError("missing ledger")
                    else:
                        if record.verified_generation > state.generation or (not state.dirty and state.verified_generation != record.verified_generation):
                            raise ValueError("verification marker mismatch")
                        if state.contract_id != expected.ledger_contract or state.unit != expected.unit or state.generation < record.submitted_generation or state.verified_generation > record.verified_generation:
                            raise ValueError("ledger marker mismatch")
                        snapshot = await self._operations.async_publication_snapshot(self._identity.stream_id)
                        if (snapshot.stream_id != self._identity.stream_id or not snapshot.rows
                                or snapshot.rows[0].hour_start != expected.origin
                                or len(snapshot.rows) < len(record.submitted_rows)):
                            raise ValueError("ledger origin or prefix missing")
                        # Ownership covers submitted hours, not an unsubmitted
                        # dirty suffix. A partial suffix remains unpublishable,
                        # but must not block the merge that can complete it.
                        if record.submitted_rows:
                            prefix = replace(snapshot, rows=snapshot.rows[:len(record.submitted_rows)])
                            payload = encode_snapshot(self._identity, prefix)
                            if payload.rows != record.submitted_rows:
                                raise ValueError("ledger prefix mismatch")
                        if (record.verified_generation == 0) != (record.verified_length == 0) or (state.verified_generation and not record.verified_length):
                            raise ValueError("verification mismatch")
                    reason = BLOCK_RECORDER_PROVENANCE
                    metadata = EncodedMetadata(statistic_id=self._identity.statistic_id, source=EXTERNAL_SOURCE, unit=DEFAULT_UNIT, unit_class=EXTERNAL_UNIT_CLASS, mean_type=StatisticMeanType.NONE, has_sum=True)
                    if inspection.metadata is not None and not _metadata_matches(inspection.metadata, metadata):
                        raise ValueError("metadata mismatch")
                    values = record.submitted_value_map()
                    if (inspection.rows and inspection.metadata is None) or (inspection.metadata is not None and not values):
                        raise ValueError("metadata without intent")
                    if any(values.get(h) != v for h, v in inspection.rows.items()):
                        raise ValueError("foreign recorder rows")
                    reason = BLOCK_MISSING_VERIFIED_HISTORY
                    if any(inspection.rows.get(h) != (st, total) for h, st, total in record.submitted_rows[:record.verified_length]):
                        raise ValueError("missing verified rows")
                    self._record = record
                    self._submitted = values
                    self._submitted_metadata = metadata if values else None
                    self._last_verified_hour = record.last_verified_hour
                    self._recover_pending = state is not None and state.dirty
            if not self._admitting:
                return self._outcome(STATUS_FENCED)
            self._initialized = True
            return self._outcome(STATUS_READY)
        except asyncio.CancelledError:
            self._admitting = False
            raise
        except Exception:
            return self._outcome(STATUS_BLOCKED_OWNERSHIP, reason=reason)

    def _outcome(
        self,
        status: str,
        *,
        reason: str | None = None,
        generation: int | None = None,
    ) -> PublishOutcome:
        return PublishOutcome(
            status=status,
            reason=reason,
            generation=generation,
            submitted_hours=len(self._submitted),
            last_verified_hour=self._last_verified_hour,
            degraded_reason=self._degraded_reason,
        )


# ------------------------------------------------------------------- HA port


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        hour = value
    else:
        hour = datetime.fromtimestamp(float(value), tz=timezone.utc)
    if hour.tzinfo is None:
        hour = hour.replace(tzinfo=timezone.utc)
    return hour.astimezone(timezone.utc)


def _read_rows_sync(
    hass: HomeAssistant,
    statistic_id: str,
    window_start: datetime,
    window_end: datetime | None,
) -> dict[datetime, tuple[Any, Any]]:
    """Blocking hour-row read (state+sum) for an executor job."""
    result = statistics_during_period(
        hass,
        window_start,
        window_end,
        {statistic_id},
        "hour",
        {"energy": EXTERNAL_UNIT_OF_MEASUREMENT},
        {"state", "sum"},
    )
    rows = result.get(statistic_id) or []
    out: dict[datetime, tuple[Any, Any]] = {}
    for row in rows:
        hour = _as_utc(row["start"])
        if hour in out:
            raise CompatiblePublisherError(f"recorder returned duplicate hour rows at {hour}")
        out[hour] = (row.get("state"), row.get("sum"))
    return out


def _read_metadata_sync(hass: HomeAssistant, statistic_id: str) -> StatisticMetaData | None:
    found = get_metadata(hass, statistic_ids={statistic_id})
    entry = found.get(statistic_id)
    return None if entry is None else entry[1]


def _metadata_matches(metadata: StatisticMetaData | None, expected: EncodedMetadata) -> bool:
    if metadata is None:
        return False
    for field, want in (
        ("statistic_id", expected.statistic_id),
        ("source", expected.source),
        ("unit_of_measurement", expected.unit),
        ("unit_class", expected.unit_class),
        ("mean_type", expected.mean_type),
        ("has_sum", expected.has_sum),
    ):
        if metadata.get(field) != want:
            return False
    return True


class HassExternalStatisticsPort:
    """Thin public-HA port: real enqueue, real bounded visibility read-back."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        settle_timeout: float = DEFAULT_SETTLE_TIMEOUT,
        poll_interval: float = VERIFY_POLL_INTERVAL,
    ) -> None:
        self._hass = hass
        self._settle_timeout = float(settle_timeout)
        self._poll_interval = float(poll_interval)

    def _ha_metadata(self, metadata: EncodedMetadata) -> StatisticMetaData:
        return StatisticMetaData(
            mean_type=metadata.mean_type,
            has_sum=metadata.has_sum,
            name=EXTERNAL_STATISTIC_NAME,
            source=metadata.source,
            statistic_id=metadata.statistic_id,
            unit_class=metadata.unit_class,
            unit_of_measurement=metadata.unit,
        )

    async def inventory(self, statistic_id: str) -> tuple[datetime, ...]:
        rows = await get_instance(self._hass).async_add_executor_job(
            _read_rows_sync, self._hass, statistic_id, INVENTORY_START, None
        )
        return tuple(sorted(rows))

    async def inspect(self, statistic_id: str) -> IdentityInspection:
        instance = get_instance(self._hass)
        metadata = await instance.async_add_executor_job(_read_metadata_sync, self._hass, statistic_id)
        rows = await instance.async_add_executor_job(_read_rows_sync, self._hass, statistic_id, INVENTORY_START, None)
        return IdentityInspection(statistic_id, metadata, rows)

    async def enqueue(self, payload: EncodedPayload) -> None:
        rows = [
            StatisticData(start=hour, state=state, sum=total, last_reset=None)
            for hour, state, total in payload.rows
        ]
        async_add_external_statistics(self._hass, self._ha_metadata(payload.metadata), rows)

    async def verify_visible(self, payload: EncodedPayload, *, timeout: float) -> bool:
        """Bounded read-back of one payload: visibility only, no drain claim."""
        instance = get_instance(self._hass)
        expected = payload.value_map()
        window_start = payload.rows[0][0] - _HOUR
        window_end = payload.rows[-1][0] + 2 * _HOUR
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                async with asyncio.timeout(remaining):
                    try:
                        async with asyncio.timeout(min(self._settle_timeout, remaining)):
                            await instance.async_block_till_done()
                    except TimeoutError:
                        # Settlement is best-effort, never a write/drain receipt.
                        pass
                    metadata = await instance.async_add_executor_job(
                        _read_metadata_sync, self._hass, payload.metadata.statistic_id
                    )
                    rows = await instance.async_add_executor_job(
                        _read_rows_sync,
                        self._hass,
                        payload.metadata.statistic_id,
                        window_start,
                        window_end,
                    )
            except Exception:  # noqa: BLE001 - a read error is reported as not-visible
                return False
            if time.monotonic() >= deadline:
                return False
            if _metadata_matches(metadata, payload.metadata) and _rows_visible(rows, expected):
                return True
            await asyncio.sleep(min(self._poll_interval, max(0.0, deadline - time.monotonic())))


def _rows_visible(
    rows: dict[datetime, tuple[Any, Any]],
    expected: dict[datetime, tuple[float, float]],
) -> bool:
    if set(rows) != set(expected):
        return False
    for hour, (state, total) in expected.items():
        got_state, got_sum = rows[hour]
        if got_state is None or got_sum is None:
            return False
        if float(got_state) != state or float(got_sum) != total:
            return False
    return True


__all__ = [
    "EXTERNAL_SOURCE",
    "EXTERNAL_UNIT_OF_MEASUREMENT",
    "EXTERNAL_UNIT_CLASS",
    "EXTERNAL_STATISTIC_ID_SUFFIX",
    "INVENTORY_START",
    "STATUS_READY",
    "STATUS_PUBLISHED",
    "STATUS_NOTHING_NEW",
    "STATUS_BLOCKED_INCOMPLETE",
    "STATUS_BLOCKED_OWNERSHIP",
    "STATUS_CORRECTION_PENDING",
    "STATUS_ENQUEUE_FAILED",
    "STATUS_VERIFY_FAILED",
    "STATUS_DEGRADED",
    "STATUS_FENCED",
    "CORRECTION_CHANGED_HOUR",
    "CORRECTION_REMOVED_HOUR",
    "CORRECTION_BACKFILL",
    "CORRECTION_METADATA_DRIFT",
    "BLOCK_EMPTY_SNAPSHOT",
    "BLOCK_INCOMPLETE_HOUR",
    "BLOCK_HOUR_GAP",
    "BLOCK_OCCUPIED_STATISTIC_ID",
    "DEGRADED_VERIFICATION_FAILED",
    "CompatiblePublisherError",
    "DuplicateOwnerError",
    "SnapshotNotPublishable",
    "EncodedMetadata",
    "EncodedPayload",
    "StreamIdentity",
    "ProcessOwnerRegistry",
    "ExternalStatisticsPort",
    "PublishOutcome",
    "CompatiblePublisher",
    "HassExternalStatisticsPort",
    "statistic_id_for_slug",
    "to_recorder_float",
    "encode_snapshot",
]
