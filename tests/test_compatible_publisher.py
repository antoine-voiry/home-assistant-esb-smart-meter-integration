"""Behaviour tests for the compatible-prefix external-statistics publisher.

Two layers, both against real artifacts:

* Pure layer: the real ``IntervalLedger`` on a real per-test ``tmp_path`` SQLite
  file, driven through an **injected** port that records what was enqueued. This
  is where the append-compatibility guard, the frozen submitted prefix, the
  complete-hour filter and the degrade/ownership outcomes are exercised.
* Genuine Recorder layer: ``HassExternalStatisticsPort`` with its default real
  ports and a real file-backed Recorder (``persistent_database=True``), following
  the fixture order proven in ``tests/test_external_recorder.py``.

The process-lifetime owner registry is always injected. A fresh
``ProcessOwnerRegistry`` is an explicit *simulation* of a new process boundary
(the module's own docstring says so); no test here claims to cross a real
process boundary or to have induced a physical database outage.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.components.recorder import Recorder, get_instance
from homeassistant.components.recorder import statistics as ha_statistics
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    get_metadata,
    statistics_during_period,
)
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.esb_smart_meter import compatible_publisher as cp
from custom_components.esb_smart_meter.ledger import (
    Coverage,
    IntervalLedger,
    IntervalReading,
    PublicationSnapshot,
)

# NOTE: ``persistent_database`` is parametrized per genuine-Recorder test below,
# not module-wide: a module-wide parametrize marker requires every test to consume
# that fixture (transitively), which the pure injected-port tests deliberately do not.

BASE = datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc)
SLUG = "compatpubtest01"


# --------------------------------------------------------------------------- harness


class FakePort:
    """Injected port: records enqueues, never touches HA, never sleeps."""

    def __init__(
        self,
        *,
        visible: bool = True,
        inventory_hours: tuple[datetime, ...] = (),
        enqueue_error: BaseException | None = None,
        verify_error: BaseException | None = None,
    ) -> None:
        self.inventory_hours = tuple(inventory_hours)
        self.enqueue_error = enqueue_error
        self.verify_error = verify_error
        self.visible = visible
        self.enqueued: list[cp.EncodedPayload] = []
        self.inventory_calls = 0
        self.enqueue_calls = 0
        self.verify_calls = 0

    async def inventory(self, statistic_id: str) -> tuple[datetime, ...]:
        self.inventory_calls += 1
        return self.inventory_hours

    async def enqueue(self, payload: cp.EncodedPayload) -> None:
        self.enqueue_calls += 1
        if self.enqueue_error is not None:
            raise self.enqueue_error
        self.enqueued.append(payload)

    async def verify_visible(self, payload: cp.EncodedPayload, *, timeout: float) -> bool:
        self.verify_calls += 1
        if self.verify_error is not None:
            raise self.verify_error
        return self.visible


@pytest.fixture
def registry() -> cp.ProcessOwnerRegistry:
    """A fresh registry == an explicitly simulated new process boundary."""
    return cp.ProcessOwnerRegistry()


def ledger_for(tmp_path: Path, slug: str = SLUG) -> IntervalLedger:
    return IntervalLedger(tmp_path / f"esb_smart_meter.{slug}.ledger")


def identity_for(slug: str = SLUG) -> cp.StreamIdentity:
    return cp.StreamIdentity(
        slug=slug,
        stream_id=slug,
        statistic_id=cp.statistic_id_for_slug(slug),
    )


def half_hour_readings(energies, origin: datetime = BASE) -> list[IntervalReading]:
    """Two half-hour END intervals per hour, splitting each hour's energy evenly."""
    readings: list[IntervalReading] = []
    for index, energy in enumerate(energies):
        start = origin + timedelta(hours=index)
        half = Decimal(str(energy)) / 2
        readings.append(IntervalReading(end=start + timedelta(minutes=30), energy_kwh=half))
        readings.append(IntervalReading(end=start + timedelta(minutes=60), energy_kwh=half))
    return readings


def hours(count: int, origin: datetime = BASE) -> list[datetime]:
    return [origin + timedelta(hours=index) for index in range(count)]


def make_publisher(ledger, port, registry, slug: str = SLUG) -> cp.CompatiblePublisher:
    return cp.CompatiblePublisher(
        identity_for(slug), ledger=ledger, port=port, registry=registry
    )


# ------------------------------------------------- pure: initial publish/replay/append


async def test_initial_publish_replay_and_append(tmp_path, registry):
    ledger = ledger_for(tmp_path)
    port = FakePort()
    publisher = make_publisher(ledger, port, registry)

    first = await publisher.async_merge_and_publish(half_hour_readings([1, 2, 3]))
    assert first.status == cp.STATUS_PUBLISHED
    assert first.generation == 1
    assert port.inventory_calls == 1  # fail-closed pre-flight ran once
    assert port.enqueue_calls == 1
    payload = port.enqueued[0]
    assert payload.metadata.statistic_id == f"esb_smart_meter:{SLUG}_grid_import"
    assert payload.metadata.unit == "kWh"
    assert payload.metadata.unit_class == "energy"
    assert [row[0] for row in payload.rows] == hours(3)
    assert [row[1] for row in payload.rows] == [1.0, 3.0, 6.0]
    assert [row[2] for row in payload.rows] == [1.0, 3.0, 6.0]
    assert publisher.submitted_hours == 3
    assert publisher.last_verified_hour == BASE + timedelta(hours=2)

    # Identical replay of the same batch: ledger is clean -> nothing new, no enqueue.
    replay = await publisher.async_merge_and_publish(half_hour_readings([1, 2, 3]))
    assert replay.status == cp.STATUS_NOTHING_NEW
    assert port.enqueue_calls == 1

    # Extending one complete hour is a compatible append.
    append = await publisher.async_merge_and_publish(half_hour_readings([4], origin=BASE + timedelta(hours=3)))
    assert append.status == cp.STATUS_PUBLISHED
    assert append.generation == 2
    assert port.enqueue_calls == 2
    assert [row[0] for row in port.enqueued[1].rows] == hours(4)
    assert [row[1] for row in port.enqueued[1].rows] == [1.0, 3.0, 6.0, 10.0]


# --------------------------------------------- pure: duplicate batch is never double counted


async def test_duplicate_batch_is_not_double_counted(tmp_path, registry):
    ledger = ledger_for(tmp_path)
    port = FakePort()
    publisher = make_publisher(ledger, port, registry)

    first = await publisher.async_merge_and_publish(half_hour_readings([1, 2, 3]))
    assert first.status == cp.STATUS_PUBLISHED

    duplicate = await publisher.async_merge_and_publish(half_hour_readings([1, 2, 3]))
    assert duplicate.status == cp.STATUS_NOTHING_NEW
    # A clean ledger reports no pending generation; the authoritative generation
    # is the ledger's, and it did not advance.
    assert duplicate.generation is None
    assert ledger.publication_snapshot(SLUG).generation == 1
    assert duplicate.submitted_hours == 3
    assert port.enqueue_calls == 1

    snapshot = ledger.publication_snapshot(SLUG)
    assert len(snapshot.rows) == 3
    assert snapshot.coverage.known_intervals == 6  # 3 hours x 2 half-hour intervals, not 12
    assert snapshot.known_total_kwh == Decimal("6.000000")


# ------------------------------------------------------- pure: gap / partial excluded


async def test_partial_hour_is_not_publishable(tmp_path, registry):
    ledger = ledger_for(tmp_path)
    port = FakePort()
    publisher = make_publisher(ledger, port, registry)

    outcome = await publisher.async_merge_and_publish(
        [IntervalReading(end=BASE + timedelta(minutes=30), energy_kwh=Decimal("1"))]
    )
    assert outcome.status == cp.STATUS_BLOCKED_INCOMPLETE
    assert outcome.reason == cp.BLOCK_INCOMPLETE_HOUR
    assert port.enqueue_calls == 0
    assert publisher.submitted_hours == 0
    # The generation stays pending: a partial hour is never zero-filled or acked.
    pending = ledger.pending_publication(SLUG)
    assert pending is not None and pending.generation == 1


async def test_interior_hour_gap_is_not_publishable(tmp_path, registry):
    ledger = ledger_for(tmp_path)
    port = FakePort()
    publisher = make_publisher(ledger, port, registry)

    gapped = half_hour_readings([1], origin=BASE) + half_hour_readings([3], origin=BASE + timedelta(hours=2))
    outcome = await publisher.async_merge_and_publish(gapped)
    assert outcome.status == cp.STATUS_BLOCKED_INCOMPLETE
    assert outcome.reason == cp.BLOCK_HOUR_GAP
    assert port.enqueue_calls == 0
    assert publisher.submitted_hours == 0
    assert ledger.pending_publication(SLUG) is not None


def test_empty_snapshot_encoding_is_refused():
    empty = PublicationSnapshot(
        stream_id=SLUG,
        generation=0,
        rows=(),
        coverage=Coverage(None, None, 0, 0, 0, 0, 0),
    )
    with pytest.raises(cp.SnapshotNotPublishable) as raised:
        cp.encode_snapshot(identity_for(), empty)
    assert raised.value.reason == cp.BLOCK_EMPTY_SNAPSHOT


# ---------------------------------- pure: corrections rejected BEFORE enqueue (always)


async def test_correction_is_rejected_before_enqueue_after_success(tmp_path, registry):
    ledger = ledger_for(tmp_path)
    port = FakePort()
    publisher = make_publisher(ledger, port, registry)
    await publisher.async_merge_and_publish(half_hour_readings([1, 2, 3]))
    assert port.enqueue_calls == 1

    corrected = await publisher.async_merge_and_publish(half_hour_readings([4], origin=BASE))
    assert corrected.status == cp.STATUS_CORRECTION_PENDING
    assert corrected.reason == cp.CORRECTION_CHANGED_HOUR
    assert port.enqueue_calls == 1  # rejected before the port saw anything
    assert publisher.submitted_hours == 3


async def test_correction_is_rejected_before_enqueue_after_verify_timeout(tmp_path, registry):
    ledger = ledger_for(tmp_path)
    port = FakePort()
    publisher = make_publisher(ledger, port, registry)
    await publisher.async_merge_and_publish(half_hour_readings([1, 2, 3]))

    # The bounded read-back stops confirming visibility -> degraded, generation pending.
    port.visible = False
    failed = await publisher.async_merge_and_publish(half_hour_readings([4], origin=BASE + timedelta(hours=3)))
    assert failed.status == cp.STATUS_VERIFY_FAILED
    assert publisher.degraded is True
    assert port.enqueue_calls == 2

    corrected = await publisher.async_merge_and_publish(half_hour_readings([4], origin=BASE))
    assert corrected.status == cp.STATUS_CORRECTION_PENDING
    assert corrected.reason == cp.CORRECTION_CHANGED_HOUR
    assert port.enqueue_calls == 2  # still rejected ahead of the port
    assert corrected.degraded_reason == cp.DEGRADED_VERIFICATION_FAILED


async def test_correction_is_rejected_before_enqueue_after_unload(tmp_path, registry):
    ledger = ledger_for(tmp_path)
    port = FakePort()
    publisher = make_publisher(ledger, port, registry)
    await publisher.async_merge_and_publish(half_hour_readings([1, 2, 3]))
    assert port.enqueue_calls == 1

    await publisher.async_unload()
    assert publisher.admitting is False

    # Admissions are stopped first: no new merge reaches the ledger through the publisher.
    fenced = await publisher.async_merge_and_publish(half_hour_readings([4], origin=BASE))
    assert fenced.status == cp.STATUS_FENCED
    assert ledger.publication_snapshot(SLUG).generation == 1

    # Reconciliation at the process boundary may have already revised the ledger; the
    # publisher must still refuse the changed prefix before the port, after unload.
    ledger.merge(SLUG, half_hour_readings([4], origin=BASE))
    assert ledger.publication_snapshot(SLUG).generation == 2
    rejected = await publisher.async_publish_pending()
    assert rejected.status == cp.STATUS_CORRECTION_PENDING
    assert rejected.reason == cp.CORRECTION_CHANGED_HOUR
    assert port.enqueue_calls == 1


async def test_metadata_drift_is_rejected(tmp_path, registry):
    ledger = ledger_for(tmp_path)
    port = FakePort()
    publisher = make_publisher(ledger, port, registry)
    await publisher.async_merge_and_publish(half_hour_readings([1, 2, 3]))

    published = port.enqueued[0]
    drifted = cp.EncodedPayload(
        metadata=cp.EncodedMetadata(
            statistic_id=published.metadata.statistic_id,
            source=published.metadata.source,
            unit="MWh",  # drifted unit
            unit_class=published.metadata.unit_class,
            mean_type=published.metadata.mean_type,
            has_sum=published.metadata.has_sum,
        ),
        rows=published.rows,
    )
    assert publisher.check_compatible(drifted) == cp.CORRECTION_METADATA_DRIFT
    assert publisher.check_compatible(published) is None  # the frozen payload still matches


# ------------------------------------------------------- pure: enqueue failure / prefix


async def test_enqueue_failure_freezes_prefix_before_handoff(tmp_path, registry):
    ledger = ledger_for(tmp_path)
    port = FakePort(enqueue_error=RuntimeError("queue unavailable"))
    publisher = make_publisher(ledger, port, registry)

    outcome = await publisher.async_merge_and_publish(half_hour_readings([1, 2, 3]))
    assert outcome.status == cp.STATUS_ENQUEUE_FAILED
    assert outcome.reason == "RuntimeError"
    assert port.enqueue_calls == 1
    # Frozen from the instant of handoff: the failed write may still have landed.
    assert publisher.submitted_values == {
        BASE: (1.0, 1.0),
        BASE + timedelta(hours=1): (3.0, 3.0),
        BASE + timedelta(hours=2): (6.0, 6.0),
    }
    assert ledger.pending_publication(SLUG) is not None

    # Any later differing payload for a submitted hour is still refused pre-enqueue.
    corrected = await publisher.async_merge_and_publish(half_hour_readings([4], origin=BASE))
    assert corrected.status == cp.STATUS_CORRECTION_PENDING
    assert corrected.reason == cp.CORRECTION_CHANGED_HOUR
    assert port.enqueue_calls == 1


# ----------------------------------------------- pure: verification failure -> degraded


async def test_verify_timeout_leaves_generation_pending_and_degrades(tmp_path, registry):
    ledger = ledger_for(tmp_path)
    port = FakePort(visible=False)
    publisher = make_publisher(ledger, port, registry)

    outcome = await publisher.async_merge_and_publish(half_hour_readings([1, 2, 3]))
    assert outcome.status == cp.STATUS_VERIFY_FAILED
    assert outcome.reason == cp.DEGRADED_VERIFICATION_FAILED
    assert outcome.degraded_reason == cp.DEGRADED_VERIFICATION_FAILED
    assert publisher.degraded is True
    assert publisher.last_verified_hour is None
    assert port.enqueue_calls == 1

    pending = ledger.pending_publication(SLUG)
    assert pending is not None and pending.generation == 1  # never acknowledged

    # No retry storm: the next attempt is refused without touching the port.
    again = await publisher.async_publish_pending()
    assert again.status == cp.STATUS_DEGRADED
    assert again.reason == cp.DEGRADED_VERIFICATION_FAILED
    assert port.enqueue_calls == 1


async def test_verify_exception_is_reported_and_degrades(tmp_path, registry):
    ledger = ledger_for(tmp_path)
    port = FakePort(verify_error=ValueError("read-back blew up"))
    publisher = make_publisher(ledger, port, registry)

    outcome = await publisher.async_merge_and_publish(half_hour_readings([2]))
    assert outcome.status == cp.STATUS_VERIFY_FAILED
    assert outcome.reason == "ValueError"
    assert publisher.degraded_reason == cp.DEGRADED_VERIFICATION_FAILED
    assert ledger.pending_publication(SLUG) is not None


# ------------------------------------------------- pure: ownership / process boundary


async def test_second_owner_in_same_process_is_refused_even_after_unload(tmp_path, registry):
    ledger = ledger_for(tmp_path)
    first = make_publisher(ledger, FakePort(), registry)
    await first.async_merge_and_publish(half_hour_readings([1, 2, 3]))

    with pytest.raises(cp.DuplicateOwnerError):
        make_publisher(ledger, FakePort(), registry)

    await first.async_unload()
    with pytest.raises(cp.DuplicateOwnerError):
        make_publisher(ledger, FakePort(), registry)
    # The retained claim is keyed by the *physical* statistic id, not the slug.
    assert identity_for().statistic_id in registry
    assert len(registry) == 1


async def test_physical_statistic_id_alias_cannot_evade_the_process_claim(tmp_path, registry):
    """Two logical stream ids for one physical statistic id must not coexist.

    ``stream_id`` is only a logical alias; the Recorder series is the physical
    statistic id, so a differently-named writer for the same identity is the same
    owner and must be refused before construction, before and after unload.
    """
    ledger = ledger_for(tmp_path)
    first = make_publisher(ledger, FakePort(), registry)
    await first.async_merge_and_publish(half_hour_readings([1, 2, 3]))

    alias = cp.StreamIdentity(
        slug=SLUG,
        stream_id="compatpubtest01_alias",  # same slug/statistic id, different stream id
        statistic_id=cp.statistic_id_for_slug(SLUG),
    )
    with pytest.raises(cp.DuplicateOwnerError):
        cp.CompatiblePublisher(alias, ledger=ledger, port=FakePort(), registry=registry)

    await first.async_unload()
    with pytest.raises(cp.DuplicateOwnerError):
        cp.CompatiblePublisher(alias, ledger=ledger, port=FakePort(), registry=registry)

    assert alias.statistic_id in registry
    assert len(registry) == 1


async def test_distinct_physical_statistic_ids_are_independently_claimed(tmp_path, registry):
    """Genuinely different statistic ids still get independent process claims."""
    slug_a, slug_b = "compatpuba01", "compatpubb01"
    assert cp.statistic_id_for_slug(slug_a) != cp.statistic_id_for_slug(slug_b)
    port_a, port_b = FakePort(), FakePort()
    publisher_a = make_publisher(ledger_for(tmp_path, slug_a), port_a, registry, slug=slug_a)
    publisher_b = make_publisher(ledger_for(tmp_path, slug_b), port_b, registry, slug=slug_b)
    assert len(registry) == 2

    first_a = await publisher_a.async_merge_and_publish(half_hour_readings([1, 2, 3]))
    first_b = await publisher_b.async_merge_and_publish(half_hour_readings([5]))
    assert first_a.status == cp.STATUS_PUBLISHED
    assert first_b.status == cp.STATUS_PUBLISHED
    assert port_a.enqueued[0].metadata.statistic_id == cp.statistic_id_for_slug(slug_a)
    assert port_b.enqueued[0].metadata.statistic_id == cp.statistic_id_for_slug(slug_b)


async def test_fresh_registry_simulates_a_new_process(tmp_path):
    ledger = ledger_for(tmp_path)
    process_a = cp.ProcessOwnerRegistry()
    first = make_publisher(ledger, FakePort(), process_a)
    await first.async_merge_and_publish(half_hour_readings([1, 2, 3]))

    # A new registry is an explicit simulation of a new process, not a real boundary.
    process_b = cp.ProcessOwnerRegistry()
    port_b = FakePort()
    second = make_publisher(ledger, port_b, process_b)
    assert process_a is not process_b
    replay = await second.async_merge_and_publish(half_hour_readings([1, 2, 3]))
    assert replay.status == cp.STATUS_NOTHING_NEW  # ledger already holds this generation
    assert port_b.enqueue_calls == 0

    extension = await second.async_merge_and_publish(half_hour_readings([4], origin=BASE + timedelta(hours=3)))
    assert extension.status == cp.STATUS_PUBLISHED
    assert [row[0] for row in port_b.enqueued[0].rows] == hours(4)


async def test_initialized_owner_keeps_replaying_after_its_own_rows_appear(tmp_path, registry):
    """The empty-identity pre-flight is one-shot for the process that claimed it.

    A publisher that confirmed the identity empty *before* its own first enqueue
    keeps ordinary replay/append rights in this process: its own committed rows
    becoming visible in the identity must not re-block it.
    """
    ledger = ledger_for(tmp_path)
    port = FakePort()
    publisher = make_publisher(ledger, port, registry)

    first = await publisher.async_merge_and_publish(half_hour_readings([1, 2, 3]))
    assert first.status == cp.STATUS_PUBLISHED
    assert port.inventory_calls == 1  # confirmed empty before the first enqueue

    port.inventory_hours = tuple(hours(3))  # this process's own rows are now visible
    replay = await publisher.async_merge_and_publish(half_hour_readings([1, 2, 3]))
    assert replay.status == cp.STATUS_NOTHING_NEW
    assert port.inventory_calls == 1  # no second pre-flight

    append = await publisher.async_merge_and_publish(
        half_hour_readings([4], origin=BASE + timedelta(hours=3))
    )
    assert append.status == cp.STATUS_PUBLISHED
    assert port.inventory_calls == 1
    assert [row[0] for row in port.enqueued[1].rows] == hours(4)


async def test_occupied_statistic_id_without_ledger_is_blocked(tmp_path, registry):
    ledger = ledger_for(tmp_path)
    port = FakePort(inventory_hours=(BASE,))
    publisher = make_publisher(ledger, port, registry)

    initialized = await publisher.async_initialize()
    assert initialized.status == cp.STATUS_BLOCKED_OWNERSHIP
    assert initialized.reason == cp.BLOCK_OCCUPIED_STATISTIC_ID

    # No baseline is fabricated and nothing is enqueued over unknown history.
    outcome = await publisher.async_merge_and_publish(half_hour_readings([1]))
    assert outcome.status == cp.STATUS_BLOCKED_OWNERSHIP
    assert port.enqueue_calls == 0


async def test_occupied_statistic_id_with_offline_seeded_ledger_is_rejected(tmp_path, registry):
    """Ledger presence is not ownership evidence for the occupied Recorder series.

    An offline-created ledger that was never published looks exactly like an
    established owner from the ledger alone, so an occupied identity is refused
    even with a seeded ledger and no baseline history is adopted.
    """
    ledger = ledger_for(tmp_path)
    ledger.merge(SLUG, half_hour_readings([1, 2, 3]))  # seeded offline, never published
    port = FakePort(inventory_hours=tuple(hours(3)))
    publisher = make_publisher(ledger, port, registry)

    initialized = await publisher.async_initialize()
    assert initialized.status == cp.STATUS_BLOCKED_OWNERSHIP
    assert initialized.reason == cp.BLOCK_OCCUPIED_STATISTIC_ID

    # The seeded generation stays pending: nothing is adopted and nothing acked.
    pending = await publisher.async_publish_pending()
    assert pending.status == cp.STATUS_BLOCKED_OWNERSHIP
    assert port.enqueue_calls == 0
    assert publisher.submitted_hours == 0
    assert ledger.pending_publication(SLUG) is not None


@pytest.mark.parametrize(
    "occupied",
    [
        pytest.param((BASE,), id="unknown_overlap"),
        pytest.param((BASE - timedelta(days=500),), id="foreign_pre_origin"),
        pytest.param((BASE + timedelta(hours=10),), id="foreign_suffix"),
    ],
)
async def test_unknown_occupied_history_is_rejected_before_ordinary_merge(
    tmp_path, registry, occupied
):
    """Overlap, pre-origin and suffix occupancy all refuse the ordinary merge.

    The identity pre-flight runs before the merge, so an occupied identity can
    never be made to look owned by the very merge it is about to perform.
    """
    ledger = ledger_for(tmp_path)
    ledger.merge(SLUG, half_hour_readings([1, 2, 3]))
    port = FakePort(inventory_hours=occupied)
    publisher = make_publisher(ledger, port, registry)

    outcome = await publisher.async_merge_and_publish(
        half_hour_readings([4], origin=BASE + timedelta(hours=3))
    )
    assert outcome.status == cp.STATUS_BLOCKED_OWNERSHIP
    assert outcome.reason == cp.BLOCK_OCCUPIED_STATISTIC_ID
    assert port.inventory_calls == 1
    assert port.enqueue_calls == 0
    assert publisher.submitted_hours == 0
    assert ledger.pending_publication(SLUG) is not None


def test_publisher_rejects_a_mismatched_statistic_id(tmp_path):
    with pytest.raises(cp.CompatiblePublisherError):
        cp.CompatiblePublisher(
            cp.StreamIdentity(slug=SLUG, stream_id=SLUG, statistic_id="esb_smart_meter:other_grid_import"),
            ledger=ledger_for(tmp_path),
            port=FakePort(),
            registry=cp.ProcessOwnerRegistry(),
        )


# ------------------------------------- bounded verification seam (held executor read)


class HeldReadRecorder:
    """Minimal Recorder seam: settlement returns, verification reads never resolve.

    Used with the real ``HassExternalStatisticsPort`` to exercise the advertised
    *bounded* verification seam without spawning a thread, a service or a
    database. The inventory read (``window_end is None``) answers empty so the
    identity pre-flight succeeds; the metadata/rows read-backs are held on an
    unresolved future -- exactly the seam that used to leave ``verify_visible``
    pending past its deadline.
    """

    def __init__(self) -> None:
        self.settled = 0
        self.held_reads = 0

    async def async_block_till_done(self) -> None:
        self.settled += 1

    def async_add_executor_job(self, target, *args):
        if target is cp._read_rows_sync and args[-1] is None:
            return self._empty_inventory()
        return self._hold()

    @staticmethod
    async def _empty_inventory() -> dict:
        return {}

    async def _hold(self) -> None:
        self.held_reads += 1
        await asyncio.Future()


async def test_held_executor_read_cannot_outlive_the_verify_deadline(tmp_path, registry, monkeypatch):
    """The caller deadline covers settlement *and* the executor read-backs.

    A held metadata/rows read must not leave the publisher waiting: the bounded
    read-back returns not-visible, the generation stays pending, admissions are
    closed and nothing is re-enqueued or acknowledged -- while the process owner
    and the frozen submitted prefix are retained (no thread is terminated or
    drained, which is explicitly not required).
    """
    ledger = ledger_for(tmp_path)
    seam = HeldReadRecorder()
    monkeypatch.setattr(cp, "get_instance", lambda hass: seam)
    enqueued: list[tuple] = []

    def record_enqueue(hass, metadata, rows):
        enqueued.append(tuple(row["start"] for row in rows))

    monkeypatch.setattr(cp, "async_add_external_statistics", record_enqueue)

    port = cp.HassExternalStatisticsPort(object(), settle_timeout=30.0)
    publisher = cp.CompatiblePublisher(
        identity_for(), ledger=ledger, port=port, registry=registry, verify_timeout=0.05
    )

    started = time.monotonic()
    # The publisher's own 0.05 s deadline must fire long before this outer guard:
    # the guard only makes an unbounded regression fail fast instead of hanging.
    outcome = await asyncio.wait_for(
        publisher.async_merge_and_publish(half_hour_readings([1, 2, 3])), timeout=5.0
    )
    elapsed = time.monotonic() - started

    assert outcome.status == cp.STATUS_VERIFY_FAILED
    assert outcome.reason == cp.DEGRADED_VERIFICATION_FAILED
    assert publisher.degraded is True
    assert publisher.last_verified_hour is None
    assert publisher.submitted_hours == 3  # prefix frozen before the held read
    assert elapsed < 2.0  # far below the 30 s settle budget the read is held under
    assert seam.settled >= 1  # settlement ran inside the same bounded deadline
    assert seam.held_reads >= 1
    assert len(enqueued) == 1

    pending = ledger.pending_publication(SLUG)
    assert pending is not None and pending.generation == 1  # never acknowledged

    # Degraded closes admissions: no later enqueue for a newer pending generation.
    later = await publisher.async_merge_and_publish(
        half_hour_readings([4], origin=BASE + timedelta(hours=3))
    )
    assert later.status == cp.STATUS_DEGRADED
    assert len(enqueued) == 1

    # The process-lifetime owner claim survives the degrade.
    with pytest.raises(cp.DuplicateOwnerError):
        cp.CompatiblePublisher(identity_for(), ledger=ledger, port=port, registry=registry)


# ------------------------------------------------- genuine Recorder layer (executed)
# The two tests below drive ``HassExternalStatisticsPort`` (real inventory ->
# real ``async_add_external_statistics`` -> real bounded visibility read-back)
# against HA's own file-backed Recorder (``persistent_database=True``) and read
# the result back through HA's real period query. Fixture order mirrors
# ``tests/test_external_recorder.py``: the non-autouse ``genuine_recorder``
# fixture is requested first in each signature, so the real database URL is
# resolved before ``hass`` is set up, and it proves ``get_instance(hass)`` is
# HA's live ``Recorder`` (``is recorder_mock``). ``persistent_database`` is
# parametrized per genuine test only: a module-wide marker would also force that
# fixture on the injected-port tests, which deliberately never start a Recorder.


def _hour_of(raw) -> datetime:
    """Normalize a Recorder period start to UTC (written independently here)."""
    hour = raw if isinstance(raw, datetime) else datetime.fromtimestamp(float(raw), tz=timezone.utc)
    if hour.tzinfo is None:
        hour = hour.replace(tzinfo=timezone.utc)
    return hour.astimezone(timezone.utc)


def _read_state_sum_sync(hass, statistic_id, window_start, window_end):
    """Blocking real hour read: state+sum for one statistic id."""
    result = statistics_during_period(
        hass,
        window_start,
        window_end,
        {statistic_id},
        "hour",
        {"energy": cp.EXTERNAL_UNIT_OF_MEASUREMENT},
        {"state", "sum"},
    )
    return {
        _hour_of(row["start"]): (row.get("state"), row.get("sum"))
        for row in result.get(statistic_id) or []
    }


def _read_changes_sync(hass, statistic_id, window_start, window_end):
    """Blocking real Energy-change read: the per-period consumption delta."""
    result = statistics_during_period(
        hass,
        window_start,
        window_end,
        {statistic_id},
        "hour",
        {"energy": cp.EXTERNAL_UNIT_OF_MEASUREMENT},
        {"change"},
    )
    return [
        (_hour_of(row["start"]), row.get("change"))
        for row in result.get(statistic_id) or []
    ]


def _read_metadata_sync(hass, statistic_id):
    found = get_metadata(hass, statistic_ids={statistic_id})
    entry = found.get(statistic_id)
    return None if entry is None else entry[1]


async def read_state_sum_rows(hass, statistic_id, window_start, window_end):
    return await get_instance(hass).async_add_executor_job(
        _read_state_sum_sync, hass, statistic_id, window_start, window_end
    )


async def read_changes(hass, statistic_id, window_start, window_end):
    return await get_instance(hass).async_add_executor_job(
        _read_changes_sync, hass, statistic_id, window_start, window_end
    )


async def read_metadata(hass, statistic_id):
    return await get_instance(hass).async_add_executor_job(
        _read_metadata_sync, hass, statistic_id
    )


@pytest.fixture(autouse=True)
def mock_asyncio_sleep():
    """Keep this module out of the repository-wide global ``asyncio.sleep`` fake.

    Same opt-out as ``tests/test_external_recorder.py``: the genuine tests need a
    real bounded visibility poll (an ``AsyncMock`` sleep would busy-spin the port's
    deadline), and the Recorder's own internals must not run against a mocked
    sleep. The injected-port tests never call it.
    """
    yield


@pytest.fixture
def genuine_recorder(recorder_db_url, recorder_mock, hass):
    """Resolve the real Recorder database and prove ``hass`` is backed by it.

    Arguments are ordered deliberately: ``recorder_db_url`` must be resolved while
    ``hass_fixture_setup`` is still empty. Tests request this fixture first, so its
    dependencies (including ``hass``) are set up in this order.
    """
    assert recorder_db_url.startswith("sqlite:///"), recorder_db_url
    db_file = Path(recorder_db_url[len("sqlite:///") :])
    assert db_file.exists(), db_file
    instance = get_instance(hass)
    assert isinstance(instance, Recorder)
    assert instance is recorder_mock
    assert instance.is_alive()
    return instance


@pytest.mark.parametrize("persistent_database", [True])
async def test_first_hour_through_real_port_keeps_its_own_energy(
    genuine_recorder, hass, recorder_mock, tmp_path
):
    """Real inventory/enqueue/verify_visible; the origin hour carries its own energy.

    The first hour is never a fabricated zero: HA's real period ``change`` query
    reports the origin hour's own interval energy.
    """
    statistic_id = identity_for().statistic_id
    ledger = ledger_for(tmp_path)
    port = cp.HassExternalStatisticsPort(hass, settle_timeout=20.0)
    publisher = cp.CompatiblePublisher(
        identity_for(),
        ledger=ledger,
        port=port,
        registry=cp.ProcessOwnerRegistry(),
        verify_timeout=20.0,
    )

    # Real pre-flight: an open-ended inventory read against the live Recorder.
    initialized = await publisher.async_initialize()
    assert initialized.status == cp.STATUS_READY

    # Real publication: inventory -> async_add_external_statistics -> visibility read-back.
    outcome = await publisher.async_merge_and_publish(half_hour_readings([1, 2, 3]))
    assert outcome.status == cp.STATUS_PUBLISHED
    assert outcome.generation == 1
    assert outcome.submitted_hours == 3
    assert publisher.last_verified_hour == BASE + timedelta(hours=2)
    assert publisher.degraded is False
    # The generation was acknowledged by the real read-back, so nothing stays dirty.
    assert ledger.pending_publication(SLUG) is None

    rows = await read_state_sum_rows(
        hass, statistic_id, BASE - timedelta(hours=1), BASE + timedelta(hours=4)
    )
    assert rows == {
        BASE: (1.0, 1.0),
        BASE + timedelta(hours=1): (3.0, 3.0),
        BASE + timedelta(hours=2): (6.0, 6.0),
    }

    metadata = await read_metadata(hass, statistic_id)
    assert metadata is not None
    assert metadata["statistic_id"] == statistic_id
    assert metadata["source"] == cp.EXTERNAL_SOURCE
    assert metadata["unit_of_measurement"] == cp.EXTERNAL_UNIT_OF_MEASUREMENT
    assert metadata["unit_class"] == cp.EXTERNAL_UNIT_CLASS
    assert metadata["mean_type"] == StatisticMeanType.NONE
    assert metadata["has_sum"] is True

    # Energy period delta: the origin hour keeps its own energy, no fabricated zero.
    changes = await read_changes(hass, statistic_id, BASE, BASE + timedelta(hours=3))
    assert [start for start, _change in changes] == hours(3)
    values = [change for _start, change in changes]
    assert values == [1.0, 2.0, 3.0]
    assert values[0] != 0.0


@pytest.mark.parametrize("persistent_database", [True])
async def test_compatible_stale_retry_keeps_overlap_and_new_suffix(
    genuine_recorder, hass, recorder_mock, tmp_path
):
    """The real ImportStatisticsTask retry of a stale but compatible payload.

    One real import is made to fail once, which is the task's own retry branch
    (``ImportStatisticsTask.run`` re-queues itself on a ``False`` result). The
    failed attempt is held inside the recorder thread until *after* the
    append-compatible extension has been handed to the real public API, so the
    re-queued stale payload commits last. Because both payloads come from the
    module's approved encoder and are append-compatible (identical overlap, one
    contiguous new hour), the stale last writer cannot change the overlap, cannot
    drop the new suffix and cannot double count: the committed series is exactly
    the newer payload.
    """
    statistic_id = identity_for().statistic_id
    ledger = ledger_for(tmp_path)

    ledger.merge(SLUG, half_hour_readings([1, 2, 3]))
    older = cp.encode_snapshot(identity_for(), ledger.publication_snapshot(SLUG))
    ledger.merge(SLUG, half_hour_readings([4], origin=BASE + timedelta(hours=3)))
    newer = cp.encode_snapshot(identity_for(), ledger.publication_snapshot(SLUG))

    older_map = older.value_map()
    newer_map = newer.value_map()
    assert [row[0] for row in older.rows] == hours(3)
    assert [row[0] for row in newer.rows] == hours(4)
    assert {hour: newer_map[hour] for hour in older_map} == older_map  # overlap unchanged
    assert sorted(set(newer_map) - set(older_map)) == [BASE + timedelta(hours=3)]  # suffix only

    port = cp.HassExternalStatisticsPort(hass, settle_timeout=20.0)
    entered, release = threading.Event(), threading.Event()
    original = ha_statistics.import_statistics
    calls: list[list[float]] = []
    failed = False

    def inject(instance, metadata, payload, table):
        """Fail the first real import once; the task re-queues that same payload."""
        nonlocal failed
        if metadata["statistic_id"] == statistic_id:
            calls.append([row["sum"] for row in payload])
            if not failed:
                failed = True
                entered.set()
                assert release.wait(20), "QA release deadline"
                return False
        return original(instance, metadata, payload, table)

    with patch.object(ha_statistics, "import_statistics", inject):
        try:
            await port.enqueue(older)  # attempt 1 fails once and is held
            assert await asyncio.to_thread(entered.wait, 10)
            await port.enqueue(newer)  # queued behind the held failure
            release.set()  # the stale retry is released after the newer payload
            await get_instance(hass).async_block_till_done()
        finally:
            release.set()
            await async_wait_recording_done(hass)

    # The observed task order proves the out-of-order stale retry really ran.
    assert calls == [[1.0, 3.0, 6.0], [1.0, 3.0, 6.0, 10.0], [1.0, 3.0, 6.0]]

    rows = await read_state_sum_rows(
        hass, statistic_id, BASE - timedelta(hours=1), BASE + timedelta(hours=5)
    )
    assert rows == {
        BASE: (1.0, 1.0),
        BASE + timedelta(hours=1): (3.0, 3.0),
        BASE + timedelta(hours=2): (6.0, 6.0),
        BASE + timedelta(hours=3): (10.0, 10.0),
    }
    assert len(rows) == 4  # one row per hour: no added row and no double count

async def test_durable_intent_is_reopened_before_enqueue(tmp_path, hass):
    from custom_components.esb_smart_meter import energy_runtime as er
    store = er.DurableOwnerStore(tmp_path / 'owner.json')
    operations = er.RuntimeLedgerOperations(hass, er.ExecutorIntervalLedger(tmp_path / 'durable.ledger'))
    class DurablePort(FakePort):
        async def inspect(self, statistic_id):
            return cp.IdentityInspection(statistic_id, None, {})
        async def enqueue(self, payload):
            reopened = store.load()
            assert reopened.submitted_value_map() == payload.value_map()
            await super().enqueue(payload)
    port = DurablePort()
    publisher = cp.CompatiblePublisher(identity_for(), ledger=None, port=port,
        registry=cp.ProcessOwnerRegistry(), owner_store=er.RuntimeOwnerStorage(hass, store),
        ledger_operations=operations, entry_id='synthetic-entry',
        source_binding=er.SourceBinding.synthetic(mprn='synthetic'), origin=BASE)
    assert (await publisher.async_merge_and_publish(half_hour_readings([1]))).published
    assert store.load().verified_length == 1
    assert (await publisher.async_merge_and_publish(half_hour_readings([1]))).status == cp.STATUS_NOTHING_NEW
    assert port.enqueue_calls == 1

class DurableFakePort(FakePort):
    def __init__(self):
        super().__init__()
        self.metadata = None
        self.rows = {}
    async def inspect(self, statistic_id):
        return cp.IdentityInspection(statistic_id, self.metadata, dict(self.rows))
    async def enqueue(self, payload):
        await super().enqueue(payload)
        if self.visible:
            self.rows = payload.value_map()
            m = payload.metadata
            self.metadata = dict(statistic_id=m.statistic_id, source=m.source,
                unit_of_measurement=m.unit, unit_class=m.unit_class,
                mean_type=m.mean_type, has_sum=m.has_sum)


def durable_publisher(tmp_path, hass, port, **overrides):
    from custom_components.esb_smart_meter import energy_runtime as er
    store = er.DurableOwnerStore(tmp_path / 'owner.json')
    operations = er.RuntimeLedgerOperations(hass, er.ExecutorIntervalLedger(tmp_path / 'durable.ledger'))
    options = dict(ledger=None, port=port, registry=cp.ProcessOwnerRegistry(),
        owner_store=er.RuntimeOwnerStorage(hass, store), ledger_operations=operations,
        entry_id='synthetic-entry', source_binding=er.SourceBinding.synthetic(mprn='synthetic'), origin=BASE)
    options.update(overrides)
    return cp.CompatiblePublisher(identity_for(), **options), store, operations


@pytest.mark.parametrize('already_visible', [False, True])
async def test_durable_pending_recovery_and_clean_restart(tmp_path, hass, already_visible):
    port = DurableFakePort()
    port.visible = False
    publisher, store, operations = durable_publisher(tmp_path, hass, port)
    assert (await publisher.async_merge_and_publish(half_hour_readings([1]))).status == cp.STATUS_VERIFY_FAILED
    if already_visible:
        port.visible = True
        await port.enqueue(cp.encode_snapshot(identity_for(), await operations.async_publication_snapshot(SLUG)))
    port.visible = True
    before = port.enqueue_calls
    resumed, _, _ = durable_publisher(tmp_path, hass, port)
    assert (await resumed.async_publish_pending()).published
    assert port.enqueue_calls == before + (not already_visible)
    clean, _, _ = durable_publisher(tmp_path, hass, port)
    assert (await clean.async_publish_pending()).status == cp.STATUS_NOTHING_NEW
    assert port.enqueue_calls == before + (not already_visible)


@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'binding', 'metadata', 'history', 'suffix', 'preorigin', 'overlap'])
async def test_durable_provenance_rejected_before_merge(tmp_path, hass, damage):
    from dataclasses import replace
    from custom_components.esb_smart_meter import energy_runtime as er
    port = DurableFakePort()
    publisher, store, operations = durable_publisher(tmp_path, hass, port)
    assert (await publisher.async_merge_and_publish(half_hour_readings([1]))).published
    if damage == 'missing':
        Path(store.path).unlink()
    elif damage == 'corrupt':
        Path(store.path).write_text('{}')
    elif damage == 'binding':
        Path(store.path).write_bytes(replace(store.load(), entry_id='foreign').encode())
    elif damage == 'metadata':
        port.metadata['unit_of_measurement'] = 'Wh'
    elif damage == 'history':
        port.rows.clear()
    else:
        hour = BASE + timedelta(hours=3) if damage == 'suffix' else BASE - timedelta(hours=1) if damage == 'preorigin' else BASE
        port.rows[hour] = (99.0, 99.0)
    resumed, _, _ = durable_publisher(tmp_path, hass, port)
    assert (await resumed.async_merge_and_publish(half_hour_readings([2], BASE + timedelta(hours=1)))).status == cp.STATUS_BLOCKED_OWNERSHIP
    assert (await operations.async_read_stream_state(SLUG)).generation == 1
    assert port.enqueue_calls == 1


@pytest.mark.parametrize('failure', ['write', 'ack_false', 'ack_error', 'unload', 'cancel'])
async def test_durable_failure_fences_and_ack_errors(tmp_path, hass, monkeypatch, failure):
    port = DurableFakePort()
    publisher, store, operations = durable_publisher(tmp_path, hass, port)
    assert (await publisher.async_initialize()).status == cp.STATUS_READY
    original = publisher._owner_store.async_save
    async def save(record):
        if failure == 'write':
            raise OSError('synthetic write failure')
        if failure == 'cancel':
            raise asyncio.CancelledError()
        result = await original(record)
        await publisher.async_unload()
        return result
    if failure in ('write', 'unload', 'cancel'):
        monkeypatch.setattr(publisher._owner_store, 'async_save', save)
    else:
        async def ack(*args):
            if failure == 'ack_error':
                raise OSError('synthetic ack failure')
            return False
        monkeypatch.setattr(operations, 'async_acknowledge', ack)
    if failure == 'cancel':
        with pytest.raises(asyncio.CancelledError):
            await publisher.async_merge_and_publish(half_hour_readings([1]))
    else:
        outcome = await publisher.async_merge_and_publish(half_hour_readings([1]))
        assert not outcome.published
    assert publisher.submitted_hours == 1
    assert port.enqueue_calls == (1 if failure.startswith('ack') else 0)
    assert await operations.async_pending_publication(SLUG) is not None

@pytest.mark.parametrize('persistent_database', [True])
async def test_durable_actual_recorder_and_energy_change(genuine_recorder, hass, recorder_mock, tmp_path):
    port = cp.HassExternalStatisticsPort(hass, settle_timeout=10)
    publisher, store, operations = durable_publisher(tmp_path, hass, port, verify_timeout=10)
    enqueue = port.enqueue
    async def checked_enqueue(payload):
        assert store.load().submitted_value_map() == payload.value_map()
        await enqueue(payload)
    port.enqueue = checked_enqueue
    assert (await publisher.async_merge_and_publish(half_hour_readings([1, 2]))).published
    assert store.load().verified_length == 2
    assert await operations.async_pending_publication(SLUG) is None
    changes = await read_changes(hass, publisher.identity.statistic_id, BASE, BASE + timedelta(hours=3))
    assert [value for _, value in changes] == [1.0, 2.0]

@pytest.mark.parametrize('stage', ['fsync', 'replace', 'directory', 'readback'])
async def test_actual_durable_storage_failure_prevents_enqueue(tmp_path, hass, monkeypatch, stage):
    from custom_components.esb_smart_meter import energy_runtime as er
    port = DurableFakePort()
    publisher, store, operations = durable_publisher(tmp_path, hass, port)
    assert (await publisher.async_initialize()).status == cp.STATUS_READY
    def boom(*args):
        raise OSError('injected storage failure')
    if stage == 'readback':
        original = er.DurableOwnerStore.load
        def load(self):
            if self is not store:
                raise er.OwnerRecordCorruptError('injected target reopen failure')
            return original(self)
        monkeypatch.setattr(er.DurableOwnerStore, 'load', load)
    else:
        monkeypatch.setattr(er, {'fsync': '_fsync_fd', 'replace': '_replace_file', 'directory': '_fsync_dir'}[stage], boom)
    result = await publisher.async_merge_and_publish(half_hour_readings([1]))
    assert not result.published
    assert not publisher.admitting
    assert publisher.submitted_hours == 1
    assert port.enqueue_calls == 0
    assert await operations.async_pending_publication(SLUG) is not None


@pytest.mark.parametrize('kind', ['metadata_only', 'ledger_only', 'rows_only', 'held'])
async def test_initial_durability_requires_truly_absent_provenance(tmp_path, hass, kind):
    port = DurableFakePort()
    publisher, store, operations = durable_publisher(tmp_path, hass, port, startup_timeout=0.02)
    if kind == 'metadata_only':
        port.metadata = {'statistic_id': identity_for().statistic_id}
    elif kind == 'rows_only':
        port.rows = {BASE: (1.0, 1.0)}
    elif kind == 'ledger_only':
        await operations.async_merge(SLUG, half_hour_readings([1]), contract_id=identity_for().contract_id)
    else:
        async def held(*args):
            await asyncio.Future()
        port.inspect = held
    result = await publisher.async_merge_and_publish(half_hour_readings([2]))
    assert result.status == cp.STATUS_BLOCKED_OWNERSHIP
    assert store.load() is None
    assert port.enqueue_calls == 0


@pytest.mark.parametrize('verified_prefix', [False, True])
async def test_partial_dirty_suffix_restart_preserves_provenance_and_completes(tmp_path, hass, verified_prefix):
    port = DurableFakePort()
    publisher, store, operations = durable_publisher(tmp_path, hass, port)
    if verified_prefix:
        assert (await publisher.async_merge_and_publish(half_hour_readings([1]))).published
    else:
        assert (await publisher.async_initialize()).status == cp.STATUS_READY
    retained = store.load()
    suffix_start = BASE + timedelta(hours=1) if verified_prefix else BASE
    suffix = half_hour_readings([2], suffix_start)
    assert (await publisher.async_merge_and_publish(suffix[:1])).status == cp.STATUS_BLOCKED_INCOMPLETE
    assert store.load() == retained
    resumed, _, _ = durable_publisher(tmp_path, hass, port)
    assert (await resumed.async_initialize()).status == cp.STATUS_READY
    assert (await resumed.async_publish_pending()).status == cp.STATUS_BLOCKED_INCOMPLETE
    assert store.load() == retained
    result = await resumed.async_merge_and_publish(suffix[1:])
    assert result.published
    expected = {BASE: (1.0, 1.0), suffix_start: (3.0, 3.0)} if verified_prefix else {BASE: (2.0, 2.0)}
    assert port.rows == expected
    assert await operations.async_pending_publication(SLUG) is None
    completed_record = store.load()
    assert completed_record is not None
    assert completed_record.verified_length == len(expected)
