"""Focused tests for the durable half-hour interval accounting core (Phase 1).

Import boundary
---------------
The accounting core must be stdlib-only and must NOT import Home Assistant, so these
tests load ``custom_components/esb_smart_meter/ledger.py`` directly from its file path
instead of through the ``custom_components.esb_smart_meter`` package, whose
``__init__.py`` imports ``homeassistant`` (and the coordinator/api client). That keeps
this suite runnable in any interpreter with no HA installed, and
``test_accounting_core_imports_without_homeassistant`` proves that boundary explicitly.

All timestamps here are synthetic offset-aware UTC half-hour interval END instants.
No ESB civil label, timezone or fold is parsed or inferred anywhere, and no test
contacts a public endpoint. Phase-1 candidate is deliberately not wired into the
coordinator, sensors or statistics import.
"""

from __future__ import annotations

import ast
import decimal
import importlib.util
import sqlite3
import subprocess
import sys
import textwrap
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

UTC = timezone.utc
H0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
LEDGER_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "esb_smart_meter" / "ledger.py"
STREAM = "mprn:12345678901:import"
OTHER_STREAM = "mprn:12345678901:export"


def _load_ledger_module():
    """Load ledger.py by path without triggering the HA-importing package __init__."""
    spec = importlib.util.spec_from_file_location("esb_smart_meter_ledger_under_test", LEDGER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def led():
    """The accounting core module under test."""
    return _load_ledger_module()


@pytest.fixture
def ledger(led, tmp_path):
    """A real on-disk SQLite ledger."""
    instance = led.IntervalLedger(tmp_path / "ledger.sqlite3")
    yield instance
    instance.close()


def hm(minutes: int) -> datetime:
    """UTC instant ``minutes`` after the test origin."""
    return H0 + timedelta(minutes=minutes)


def reading(led, end, energy):
    """One normalized interval-END reading."""
    return led.IntervalReading(end=end, energy_kwh=energy)


def half_hour_batch(led, hour_start, hourly_energies):
    """Two half-hour interval END readings per hour, splitting each hour's energy evenly."""
    out = []
    for index, energy in enumerate(hourly_energies):
        hour = hour_start + timedelta(hours=index)
        first = Decimal(energy) / 2
        out.append(reading(led, hour + timedelta(minutes=30), first))
        out.append(reading(led, hour + timedelta(minutes=60), Decimal(energy) - first))
    return out


def _num(value: Decimal) -> str:
    """Shortest plain string form of an exact fixed-point Decimal (no exponent, no float)."""
    text = f"{value:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def hours(snapshot):
    return [row.hour_start.isoformat() for row in snapshot.rows]


def states(snapshot):
    return [_num(row.state_kwh) for row in snapshot.rows]


def energies(snapshot):
    return [_num(row.energy_kwh) for row in snapshot.rows]


def complete(snapshot):
    return [row.complete for row in snapshot.rows]


# ---------------------------------------------------------------------------
# Input boundary / normalization contract
# ---------------------------------------------------------------------------


def test_rejects_naive_timestamp(led, ledger):
    with pytest.raises(led.InvalidIntervalError):
        ledger.merge(STREAM, [reading(led, datetime(2026, 1, 1, 0, 30), Decimal("1"))])


@pytest.mark.parametrize(
    "bad_energy",
    [float("nan"), float("inf"), float("-inf"), Decimal("NaN"), Decimal("Infinity")],
)
def test_rejects_non_finite_energy(led, ledger, bad_energy):
    with pytest.raises(led.InvalidIntervalError):
        ledger.merge(STREAM, [reading(led, hm(30), bad_energy)])


@pytest.mark.parametrize("bad_energy", [Decimal("-0.5"), -1, "-2.25", Decimal("-0.000001")])
def test_rejects_negative_energy(led, ledger, bad_energy):
    with pytest.raises(led.InvalidIntervalError):
        ledger.merge(STREAM, [reading(led, hm(30), bad_energy)])


@pytest.mark.parametrize(
    "bad_end",
    [
        H0 + timedelta(minutes=1),
        H0 + timedelta(minutes=15),
        H0 + timedelta(minutes=45),
        H0 + timedelta(seconds=5),
        hm(30) + timedelta(microseconds=1),
    ],
)
def test_rejects_non_half_hour_boundaries(led, ledger, bad_end):
    with pytest.raises(led.InvalidIntervalError):
        ledger.merge(STREAM, [reading(led, bad_end, Decimal("1"))])


def test_accepts_exact_half_hour_end_boundaries(led, ledger):
    result = ledger.merge(
        STREAM,
        [reading(led, hm(30), Decimal("1")), reading(led, hm(60), Decimal("1"))],
    )
    assert result.changed is True
    assert result.inserted == 2


def test_rejects_non_utc_offset_instants(led, ledger):
    # The declared boundary is already-normalized UTC; a non-zero offset means the
    # upstream parser broke the contract, so fail closed instead of converting.
    offset_end = datetime(2026, 1, 1, 1, 30, tzinfo=timezone(timedelta(hours=1)))
    with pytest.raises(led.InvalidIntervalError):
        ledger.merge(STREAM, [reading(led, offset_end, Decimal("1"))])


@pytest.mark.parametrize("bad_stream", ["", "   ", " padded ", 123, None, "x" * 201])
def test_rejects_unstable_or_missing_stream_identity(led, ledger, bad_stream):
    with pytest.raises(led.InvalidIntervalError):
        ledger.merge(bad_stream, [reading(led, hm(30), Decimal("1"))])


def test_rejects_unsupported_unit(led, ledger):
    with pytest.raises(led.InvalidIntervalError):
        ledger.merge(STREAM, [reading(led, hm(30), Decimal("1"))], unit="kVAh")


def test_rejects_non_interval_reading_objects(led, ledger):
    with pytest.raises(led.InvalidIntervalError):
        ledger.merge(STREAM, [(hm(30), Decimal("1"))])


def test_rejects_energy_beyond_supported_range(led, ledger):
    with pytest.raises(led.InvalidIntervalError):
        ledger.merge(STREAM, [reading(led, hm(30), Decimal("1e12"))])


def test_fixed_point_precision_policy(led, ledger):
    """6 decimal places (micro-kWh), ROUND_HALF_UP, no binary float drift."""
    ledger.merge(
        STREAM,
        [
            reading(led, hm(30), 0.1),  # float -> Decimal(str(0.1)), exact decimal
            reading(led, hm(90), Decimal("1.23456789")),  # rounds up at 6 dp
            reading(led, hm(150), Decimal("0.0000005")),  # half-up to 1 micro-kWh
        ],
    )
    snapshot = ledger.publication_snapshot(STREAM)
    assert energies(snapshot) == ["0.1", "1.234568", "0.000001"]
    # Stored representation is the documented micro-kWh fixed-point scale.
    assert [str(row.energy_kwh) for row in snapshot.rows] == ["0.100000", "1.234568", "0.000001"]


# ---------------------------------------------------------------------------
# Ledger schema, identity and fail-closed contracts
# ---------------------------------------------------------------------------


def test_ledger_is_a_real_versioned_sqlite_file(led, tmp_path):
    path = tmp_path / "ledger.sqlite3"
    instance = led.IntervalLedger(path)
    assert instance.schema_version == led.SCHEMA_VERSION
    instance.merge(STREAM, [reading(led, hm(30), Decimal("1"))])
    instance.close()

    assert path.exists() and path.stat().st_size > 0
    connection = sqlite3.connect(path)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    version = connection.execute("SELECT value FROM ledger_meta WHERE key='schema_version'").fetchone()[0]
    owner = connection.execute("SELECT value FROM ledger_meta WHERE key='ledger_owner'").fetchone()[0]
    stored = connection.execute("SELECT COUNT(*) FROM interval_record").fetchone()[0]
    connection.close()
    assert {"ledger_meta", "stream", "interval_record"} <= tables
    assert int(version) == led.SCHEMA_VERSION
    assert owner == led.OWNERSHIP_MARKER
    assert stored == 1


def test_schema_version_mismatch_fails_closed(led, tmp_path):
    path = tmp_path / "ledger.sqlite3"
    led.IntervalLedger(path).close()
    connection = sqlite3.connect(path)
    connection.execute("UPDATE ledger_meta SET value = '999' WHERE key = 'schema_version'")
    connection.commit()
    connection.close()
    with pytest.raises(led.LedgerSchemaError):
        led.IntervalLedger(path)


def test_non_sqlite_file_fails_closed(led, tmp_path):
    path = tmp_path / "ledger.sqlite3"
    path.write_bytes(b"this is not a sqlite database")
    with pytest.raises(led.LedgerError):
        led.IntervalLedger(path)


def test_contract_mismatch_fails_closed_and_writes_nothing(led, tmp_path):
    path = tmp_path / "ledger.sqlite3"
    instance = led.IntervalLedger(path)
    instance.merge(STREAM, half_hour_batch(led, H0, [1]))
    before = instance.publication_snapshot(STREAM)

    with pytest.raises(led.ContractMismatchError):
        instance.merge(
            STREAM,
            half_hour_batch(led, H0 + timedelta(hours=5), [7]),
            contract_id="esb-halfhour-interval-end-utc/v2",
        )

    after = instance.publication_snapshot(STREAM)
    assert after.generation == before.generation
    assert hours(after) == hours(before)
    pending = instance.pending_publication(STREAM)
    assert pending is not None and pending.generation == before.generation
    assert instance.acknowledge(STREAM, after.generation) is True
    instance.close()


def test_unknown_stream_snapshot_raises(led, ledger):
    with pytest.raises(led.UnknownStreamError):
        ledger.publication_snapshot("mprn:unknown:import")
    with pytest.raises(led.UnknownStreamError):
        ledger.pending_publication("mprn:unknown:import")


# ---------------------------------------------------------------------------
# Interval identity: duplicates, revisions, atomic rejection, persistence
# ---------------------------------------------------------------------------


def test_identical_replay_is_a_noop_and_keeps_generation(led, ledger):
    batch = half_hour_batch(led, H0, [1, 2, 3])
    first = ledger.merge(STREAM, batch)
    second = ledger.merge(STREAM, batch)

    assert first.changed is True and first.generation == 1
    assert second.changed is False and second.generation == 1
    assert second.unchanged == 6 and second.inserted == 0 and second.revised == 0
    assert states(ledger.publication_snapshot(STREAM)) == ["1", "3", "6"]


def test_identical_replay_after_reopen_is_a_noop(led, tmp_path):
    path = tmp_path / "ledger.sqlite3"
    batch = half_hour_batch(led, H0, [1, 2, 3])
    instance = led.IntervalLedger(path)
    assert instance.merge(STREAM, batch).generation == 1
    instance.close()

    reopened = led.IntervalLedger(path)
    replay = reopened.merge(STREAM, batch)
    assert replay.changed is False and replay.generation == 1
    assert states(reopened.publication_snapshot(STREAM)) == ["1", "3", "6"]
    reopened.close()


def test_conflicting_duplicate_values_reject_the_whole_batch_atomically(led, tmp_path):
    path = tmp_path / "ledger.sqlite3"
    instance = led.IntervalLedger(path)
    instance.merge(STREAM, half_hour_batch(led, H0, [1]))
    before = instance.publication_snapshot(STREAM)

    # Valid new interval plus one conflicting duplicate for an existing interval.
    conflicting = [
        reading(led, hm(300), Decimal("9")),  # new, would insert
        reading(led, hm(30), Decimal("1")),  # identical to stored -> no-op
        reading(led, hm(30), Decimal("5")),  # conflict, unordered duplicate
    ]
    with pytest.raises(led.ConflictingIntervalError):
        instance.merge(STREAM, conflicting)

    after = instance.publication_snapshot(STREAM)
    assert after.generation == before.generation
    assert hours(after) == hours(before)
    pending = instance.pending_publication(STREAM)
    assert pending is not None and pending.generation == before.generation

    # A rejected batch must not create a stream either.
    with pytest.raises(led.ConflictingIntervalError):
        instance.merge("mprn:fresh:import", [reading(led, hm(30), 1), reading(led, hm(30), 2)])
    with pytest.raises(led.UnknownStreamError):
        instance.publication_snapshot("mprn:fresh:import")
    instance.close()


def test_later_unambiguous_batch_revises_a_previous_value(led, ledger):
    ledger.merge(STREAM, [reading(led, hm(30), Decimal("1"))])
    revised = ledger.merge(STREAM, [reading(led, hm(30), Decimal("4"))])

    assert revised.changed is True and revised.revised == 1
    assert revised.generation == 2
    assert revised.earliest_changed_hour == H0
    assert energies(ledger.publication_snapshot(STREAM)) == ["4"]


def test_missing_intervals_do_not_delete_known_records(led, ledger):
    ledger.merge(STREAM, half_hour_batch(led, H0, [1, 2, 3]))
    # A truncated later batch that only contains the last hour.
    result = ledger.merge(STREAM, half_hour_batch(led, H0 + timedelta(hours=2), [3]))

    assert result.changed is False
    snapshot = ledger.publication_snapshot(STREAM)
    assert hours(snapshot) == [
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T01:00:00+00:00",
        "2026-01-01T02:00:00+00:00",
    ]
    assert states(snapshot) == ["1", "3", "6"]


def test_empty_batch_is_a_noop_and_creates_no_stream(led, ledger):
    result = ledger.merge(STREAM, [])
    assert result.changed is False and result.generation == 0
    with pytest.raises(led.UnknownStreamError):
        ledger.publication_snapshot(STREAM)


def test_two_streams_with_the_same_timestamp_are_isolated(led, ledger):
    ledger.merge(STREAM, [reading(led, hm(30), Decimal("1"))])
    ledger.merge(OTHER_STREAM, [reading(led, hm(30), Decimal("7"))])

    assert energies(ledger.publication_snapshot(STREAM)) == ["1"]
    assert energies(ledger.publication_snapshot(OTHER_STREAM)) == ["7"]
    assert ledger.publication_snapshot(STREAM).generation == 1
    assert ledger.publication_snapshot(OTHER_STREAM).generation == 1


def test_reopen_preserves_intervals_generation_and_dirty_state(led, tmp_path):
    path = tmp_path / "ledger.sqlite3"
    instance = led.IntervalLedger(path)
    instance.merge(STREAM, half_hour_batch(led, H0, [1, 2, 3]))
    instance.close()

    reopened = led.IntervalLedger(path)
    snapshot = reopened.publication_snapshot(STREAM)
    pending = reopened.pending_publication(STREAM)
    assert states(snapshot) == ["1", "3", "6"]
    assert snapshot.generation == 1
    assert pending is not None and pending.generation == 1
    assert reopened.schema_version == led.SCHEMA_VERSION
    reopened.close()


# ---------------------------------------------------------------------------
# Snapshot semantics: full-origin replay, rebasing, coverage, long history
# ---------------------------------------------------------------------------


def test_snapshot_reports_hourly_state_and_sum_from_origin(led, ledger):
    ledger.merge(STREAM, half_hour_batch(led, H0, [1, 2, 3]))
    snapshot = ledger.publication_snapshot(STREAM)

    assert states(snapshot) == ["1", "3", "6"]
    assert energies(snapshot) == ["1", "2", "3"]
    assert complete(snapshot) == [True, True, True]
    assert snapshot.known_total_kwh == Decimal("6")
    assert snapshot.coverage.known_hours == 3
    assert snapshot.coverage.complete_hours == 3
    assert snapshot.coverage.incomplete_hours == 0
    assert snapshot.coverage.known_intervals == 6
    assert snapshot.coverage.missing_hours == 0


def test_correction_rebases_every_later_hour(led, ledger):
    """Review H3 case: [1,2,3] corrected to [4,2,3] must yield [4,6,9]."""
    ledger.merge(STREAM, half_hour_batch(led, H0, [1, 2, 3]))
    correction = ledger.merge(STREAM, half_hour_batch(led, H0, [4, 2, 3]))

    assert correction.changed is True
    assert correction.revised == 2  # both half-hours of the corrected hour
    assert correction.earliest_changed_hour == H0
    snapshot = ledger.publication_snapshot(STREAM)
    assert energies(snapshot) == ["4", "2", "3"]
    assert states(snapshot) == ["4", "6", "9"]


def test_earlier_backfill_rebases_the_retained_suffix(led, ledger):
    ledger.merge(STREAM, half_hour_batch(led, H0 + timedelta(hours=1), [2, 3]))
    assert states(ledger.publication_snapshot(STREAM)) == ["2", "5"]

    backfill = ledger.merge(STREAM, half_hour_batch(led, H0, [1]))
    assert backfill.earliest_changed_hour == H0
    snapshot = ledger.publication_snapshot(STREAM)
    assert energies(snapshot) == ["1", "2", "3"]
    assert states(snapshot) == ["1", "3", "6"]
    assert snapshot.coverage.first_hour == H0


def test_delayed_second_half_hour_is_known_only_then_revises_the_suffix(led, ledger):
    ledger.merge(STREAM, [reading(led, hm(30), Decimal("1"))])
    partial = ledger.publication_snapshot(STREAM)
    assert complete(partial) == [False]
    assert energies(partial) == ["1"]
    assert partial.coverage.incomplete_hours == 1

    ledger.merge(STREAM, [reading(led, hm(60), Decimal("1"))])
    completed = ledger.publication_snapshot(STREAM)
    assert complete(completed) == [True]
    assert energies(completed) == ["2"]
    assert states(completed) == ["2"]
    assert completed.coverage.incomplete_hours == 0


def test_missing_hours_are_not_invented_as_zero_rows(led, ledger):
    ledger.merge(STREAM, half_hour_batch(led, H0, [1]))
    ledger.merge(STREAM, half_hour_batch(led, H0 + timedelta(hours=3), [1]))

    snapshot = ledger.publication_snapshot(STREAM)
    assert hours(snapshot) == [
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T03:00:00+00:00",
    ]
    assert states(snapshot) == ["1", "2"]  # known-only cumulative, gap not zeroed or filled
    assert snapshot.coverage.known_hours == 2
    assert snapshot.coverage.missing_hours == 2
    assert snapshot.coverage.first_hour == H0
    assert snapshot.coverage.last_hour == H0 + timedelta(hours=3)


def test_history_beyond_90_days_and_400_day_gap_is_retained(led, ledger):
    old = H0 - timedelta(days=500)
    ledger.merge(STREAM, half_hour_batch(led, old, [1]))
    ledger.merge(STREAM, half_hour_batch(led, old + timedelta(days=401), [2]))
    ledger.merge(STREAM, half_hour_batch(led, H0, [3]))

    snapshot = ledger.publication_snapshot(STREAM)
    assert hours(snapshot)[0] == old.isoformat()
    assert states(snapshot) == ["1", "3", "6"]
    assert snapshot.coverage.known_hours == 3
    assert snapshot.coverage.known_intervals == 6


def test_month_and_year_rollover_uses_utc_elapsed_and_does_not_reset(led, ledger):
    ledger.merge(
        STREAM,
        [
            reading(led, datetime(2026, 12, 31, 23, 30, tzinfo=UTC), Decimal("1")),
            reading(led, datetime(2027, 1, 1, 0, 0, tzinfo=UTC), Decimal("2")),
            reading(led, datetime(2027, 1, 1, 0, 30, tzinfo=UTC), Decimal("4")),
        ],
    )
    snapshot = ledger.publication_snapshot(STREAM)
    assert hours(snapshot) == ["2026-12-31T23:00:00+00:00", "2027-01-01T00:00:00+00:00"]
    assert energies(snapshot) == ["3", "4"]
    assert states(snapshot) == ["3", "7"]
    assert complete(snapshot) == [True, False]


def test_dst_repeated_civil_labels_map_to_distinct_utc_hours(led, ledger):
    """Autumn transition: two interval ENDs share the civil label 01:30 but not a UTC hour."""
    dublin = ZoneInfo("Europe/Dublin")
    first_end = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    second_end = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    assert first_end.astimezone(dublin).strftime("%H:%M") == "01:30"
    assert second_end.astimezone(dublin).strftime("%H:%M") == "01:30"

    ledger.merge(
        STREAM,
        [
            reading(led, first_end, Decimal("1")),
            reading(led, datetime(2026, 10, 25, 1, 0, tzinfo=UTC), Decimal("2")),
            reading(led, second_end, Decimal("4")),
            reading(led, datetime(2026, 10, 25, 2, 0, tzinfo=UTC), Decimal("8")),
        ],
    )
    snapshot = ledger.publication_snapshot(STREAM)
    assert hours(snapshot) == ["2026-10-25T00:00:00+00:00", "2026-10-25T01:00:00+00:00"]
    assert energies(snapshot) == ["3", "12"]
    assert states(snapshot) == ["3", "15"]


def test_spring_transition_interval_subtraction_uses_utc_elapsed(led, ledger):
    ledger.merge(
        STREAM,
        [
            reading(led, datetime(2026, 3, 29, 0, 30, tzinfo=UTC), Decimal("1")),
            reading(led, datetime(2026, 3, 29, 1, 30, tzinfo=UTC), Decimal("2")),
        ],
    )
    snapshot = ledger.publication_snapshot(STREAM)
    # 01:30Z - 30 elapsed minutes = 01:00Z, i.e. the 01:00Z hour; a naive local
    # subtraction would have produced a nonexistent local 01:30 and collided keys.
    assert hours(snapshot) == ["2026-03-29T00:00:00+00:00", "2026-03-29T01:00:00+00:00"]
    assert states(snapshot) == ["1", "3"]


# ---------------------------------------------------------------------------
# Dirty generation / outbox acknowledgement
# ---------------------------------------------------------------------------


def test_generation_only_advances_when_data_changes(led, ledger):
    first = ledger.merge(STREAM, half_hour_batch(led, H0, [1]))
    unchanged = ledger.merge(STREAM, half_hour_batch(led, H0, [1]))
    changed = ledger.merge(STREAM, [reading(led, hm(30), Decimal("2"))])

    assert (first.generation, unchanged.generation, changed.generation) == (1, 1, 2)


def test_pending_publication_reports_dirty_generation_then_none_after_ack(led, ledger):
    ledger.merge(STREAM, half_hour_batch(led, H0, [1, 2]))
    pending = ledger.pending_publication(STREAM)
    assert pending is not None
    assert pending.generation == 1
    assert states(pending) == ["1", "3"]

    assert ledger.acknowledge(STREAM, pending.generation) is True
    assert ledger.pending_publication(STREAM) is None
    assert states(ledger.publication_snapshot(STREAM)) == ["1", "3"]


def test_stale_acknowledgement_cannot_clear_newer_dirty_state(led, ledger):
    ledger.merge(STREAM, half_hour_batch(led, H0, [1, 2, 3]))
    published = ledger.pending_publication(STREAM)
    ledger.merge(STREAM, half_hour_batch(led, H0, [4, 2, 3]))

    assert ledger.acknowledge(STREAM, published.generation) is False
    still_pending = ledger.pending_publication(STREAM)
    assert still_pending is not None and still_pending.generation == 2
    assert states(still_pending) == ["4", "6", "9"]

    assert ledger.acknowledge(STREAM, 2) is True
    assert ledger.pending_publication(STREAM) is None


def test_unknown_generation_acknowledgement_is_rejected(led, ledger):
    ledger.merge(STREAM, half_hour_batch(led, H0, [1]))
    assert ledger.acknowledge(STREAM, 99) is False
    assert ledger.pending_publication(STREAM) is not None


def test_repeated_acknowledgement_is_idempotent(led, ledger):
    ledger.merge(STREAM, half_hour_batch(led, H0, [1]))
    assert ledger.acknowledge(STREAM, 1) is True
    assert ledger.acknowledge(STREAM, 1) is True
    assert ledger.pending_publication(STREAM) is None


@pytest.mark.parametrize("state", ["dirty", "verified", "dirty_again", "revised_after_verified"])
def test_reopen_at_each_ledger_and_outbox_state(led, tmp_path, state):
    path = tmp_path / "ledger.sqlite3"
    instance = led.IntervalLedger(path)

    if state == "dirty":
        instance.merge(STREAM, half_hour_batch(led, H0, [1, 2]))
        expected_generation, expected_pending, expected_states = 1, 1, ["1", "3"]
    elif state == "verified":
        instance.merge(STREAM, half_hour_batch(led, H0, [1, 2]))
        instance.acknowledge(STREAM, 1)
        expected_generation, expected_pending, expected_states = 1, None, ["1", "3"]
    elif state == "dirty_again":
        instance.merge(STREAM, half_hour_batch(led, H0, [1, 2]))
        instance.acknowledge(STREAM, 1)
        instance.merge(STREAM, half_hour_batch(led, H0 + timedelta(hours=2), [3]))
        expected_generation, expected_pending, expected_states = 2, 2, ["1", "3", "6"]
    else:
        instance.merge(STREAM, half_hour_batch(led, H0, [1, 2]))
        instance.acknowledge(STREAM, 1)
        instance.merge(STREAM, half_hour_batch(led, H0, [4, 2]))
        expected_generation, expected_pending, expected_states = 2, 2, ["4", "6"]
    instance.close()

    reopened = led.IntervalLedger(path)
    snapshot = reopened.publication_snapshot(STREAM)
    assert snapshot.generation == expected_generation
    assert states(snapshot) == expected_states
    pending = reopened.pending_publication(STREAM)
    if expected_pending is None:
        assert pending is None
    else:
        assert pending is not None
        assert pending.generation == expected_pending
        assert states(pending) == expected_states
    reopened.close()


def test_dirty_state_survives_reopen_and_ack_after_reopen(led, tmp_path):
    path = tmp_path / "ledger.sqlite3"
    instance = led.IntervalLedger(path)
    instance.merge(STREAM, half_hour_batch(led, H0, [1]))
    instance.close()

    reopened = led.IntervalLedger(path)
    assert reopened.pending_publication(STREAM).generation == 1
    assert reopened.acknowledge(STREAM, 1) is True
    reopened.close()

    again = led.IntervalLedger(path)
    assert again.pending_publication(STREAM) is None
    assert states(again.publication_snapshot(STREAM)) == ["1"]
    again.close()


# ---------------------------------------------------------------------------
# Concurrency / serialized merge ownership
# ---------------------------------------------------------------------------


def test_concurrent_connections_serialize_merges(led, tmp_path):
    path = tmp_path / "ledger.sqlite3"
    led.IntervalLedger(path).close()

    errors = []
    lock = threading.Lock()

    def worker(batch_start_hour, batches):
        try:
            instance = led.IntervalLedger(path)
            for batch_index in range(batches):
                hour = batch_start_hour + batch_index
                instance.merge(STREAM, half_hour_batch(led, H0 + timedelta(hours=hour), [1]))
            instance.close()
        except BaseException as err:  # noqa: BLE001 - surfaced via assertion below
            with lock:
                errors.append(err)

    threads = [
        threading.Thread(target=worker, args=(0, 2)),
        threading.Thread(target=worker, args=(2, 2)),
        threading.Thread(target=worker, args=(4, 2)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    verifier = led.IntervalLedger(path)
    snapshot = verifier.publication_snapshot(STREAM)
    assert states(snapshot) == ["1", "2", "3", "4", "5", "6"]
    assert snapshot.coverage.known_hours == 6
    assert snapshot.generation == 6  # one generation per changed merge, serialized
    pending = verifier.pending_publication(STREAM)
    assert pending is not None and pending.generation == 6
    verifier.close()


def test_concurrent_identical_batch_is_idempotent(led, tmp_path):
    path = tmp_path / "ledger.sqlite3"
    led.IntervalLedger(path).close()
    errors = []

    def worker():
        try:
            instance = led.IntervalLedger(path)
            instance.merge(STREAM, half_hour_batch(led, H0, [1, 2]))
            instance.close()
        except BaseException as err:  # noqa: BLE001
            errors.append(err)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    verifier = led.IntervalLedger(path)
    snapshot = verifier.publication_snapshot(STREAM)
    assert states(snapshot) == ["1", "3"]
    assert snapshot.generation == 1  # first merge changed, the other two were no-ops
    verifier.close()


# ---------------------------------------------------------------------------
# Staging boundary guards
# ---------------------------------------------------------------------------


def test_accounting_core_imports_without_homeassistant(led):
    """The core is stdlib-only: importing it with homeassistant blocked must succeed."""
    script = textwrap.dedent(
        f"""
        import importlib.util, sys

        class BlockHomeAssistant:
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "homeassistant" or fullname.startswith("homeassistant."):
                    raise ImportError("homeassistant import blocked by boundary test: " + fullname)
                return None

        sys.meta_path.insert(0, BlockHomeAssistant())
        spec = importlib.util.spec_from_file_location("esb_ledger_boundary", {str(LEDGER_PATH)!r})
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        leaked = sorted(m for m in sys.modules if m.startswith("homeassistant"))
        assert not leaked, leaked
        print("stdlib-only-boundary-ok", module.SCHEMA_VERSION, module.NORMALIZATION_CONTRACT_ID)
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert f"stdlib-only-boundary-ok {led.SCHEMA_VERSION}" in completed.stdout


def test_phase1_core_is_not_wired_into_the_integration(led):
    """Authorized optional bounded wiring; retired publication remains forbidden."""
    package_dir = LEDGER_PATH.parent
    def references(source):
        tree = ast.parse(source)
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    found.add(node.module.rsplit(".", 1)[-1])
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Name):
                found.add(node.id)
        return found
    retired = {"publication_adapter", "PublicationAdapter"}
    def guard(source):
        assert not references(source) & retired
    # Negative control proves a newly introduced retired adapter import is caught.
    try:
        guard("from .publication_adapter import PublicationAdapter")
    except AssertionError:
        pass
    else:
        raise AssertionError("retired import guard did not reject its negative control")
    for name in ("__init__", "coordinator", "api_client", "sensor", "config_flow", "energy_runtime", "compatible_publisher"):
        guard((package_dir / f"{name}.py").read_text())
    for name in ("sensor", "config_flow"):
        source = (package_dir / f"{name}.py").read_text()
        assert not references(source) & {"CompatiblePublisher", "create_bounded_runtime", "energy_runtime", "compatible_publisher"}
    setup = ast.parse((package_dir / "__init__.py").read_text())
    gate = [node for node in ast.walk(setup) if isinstance(node, ast.If)
            and "enable_bounded_energy" in ast.unparse(node.test)]
    assert len(gate) == 1
    assert ast.unparse(gate[0].test) == "entry.options.get('enable_bounded_energy', False) is True"
    assert "create_bounded_runtime" in ast.unparse(gate[0])
    coordinator = ast.parse((package_dir / "coordinator.py").read_text())
    branches = [node for node in ast.walk(coordinator) if isinstance(node, ast.If)
                and ast.unparse(node.test) == "self.energy_runtime is None"]
    assert len(branches) == 1
    assert "self.esb_api.fetch()" in ast.unparse(branches[0].body[0])
    assert "fetch_with_raw" not in " ".join(ast.unparse(n) for n in branches[0].body)
    assert "async_process_csv" in " ".join(ast.unparse(n) for n in branches[0].orelse)


# ---------------------------------------------------------------------------
# M1: the COMMIT boundary shares the rollback/recovery cleanup of the body
# ---------------------------------------------------------------------------


def test_real_busy_commit_rolls_back_and_recovers(led, tmp_path):
    """A read lock blocks COMMIT; the failed batch must leave no observable state."""
    path = tmp_path / "ledger.sqlite3"
    instance = led.IntervalLedger(path, busy_timeout_ms=20)
    instance.merge(STREAM, [reading(led, hm(30), Decimal("1"))])
    pending_before = instance.pending_publication(STREAM)
    assert pending_before is not None and pending_before.generation == 1

    blocker = sqlite3.connect(path, isolation_level=None)
    blocker.execute("BEGIN")
    blocker.execute("SELECT end_ts FROM interval_record").fetchall()
    assert blocker.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    try:
        with pytest.raises(sqlite3.OperationalError):
            instance.merge(STREAM, [reading(led, hm(30), Decimal("2"))])

        # COMMIT failure must leave the connection out of any transaction ...
        assert instance._conn.in_transaction is False
        # ... and must not expose the failed batch or advance the dirty generation.
        snapshot = instance.publication_snapshot(STREAM)
        assert snapshot.generation == 1
        assert states(snapshot) == ["1"]
        pending_after = instance.pending_publication(STREAM)
        assert pending_after is not None and pending_after.generation == 1
        # The independent reader still sees the committed original micro-kWh.
        assert blocker.execute("SELECT energy_ukwh FROM interval_record").fetchall() == [(1_000_000,)]
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()

    # Once the lock is gone the same connection must merge normally again.
    recovered = instance.merge(STREAM, [reading(led, hm(30), Decimal("2"))])
    assert recovered.changed is True and recovered.generation == 2
    assert states(instance.publication_snapshot(STREAM)) == ["2"]
    instance.close()


def test_injected_commit_error_preserves_error_and_recovers(led, tmp_path):
    path = tmp_path / "ledger.sqlite3"
    instance = led.IntervalLedger(path)
    instance.merge(STREAM, [reading(led, hm(30), Decimal("1"))])
    real = instance._conn

    class FailCommit:
        def __init__(self, real_connection):
            self._real = real_connection

        def __getattr__(self, name):
            return getattr(self._real, name)

        def execute(self, sql, *args):
            if sql == "COMMIT":
                raise sqlite3.OperationalError("injected commit failure")
            return self._real.execute(sql, *args)

    instance._conn = FailCommit(real)
    try:
        with pytest.raises(sqlite3.OperationalError, match="injected commit failure"):
            instance.merge(STREAM, [reading(led, hm(30), Decimal("2"))])
    finally:
        instance._conn = real

    assert real.in_transaction is False
    assert states(instance.publication_snapshot(STREAM)) == ["1"]
    recovered = instance.merge(STREAM, [reading(led, hm(30), Decimal("2"))])
    assert recovered.generation == 2
    instance.close()


def test_commit_and_cleanup_failure_fails_closed(led, tmp_path):
    """If ROLLBACK cannot recover the connection, the instance must become unusable."""
    path = tmp_path / "ledger.sqlite3"
    instance = led.IntervalLedger(path)
    instance.merge(STREAM, [reading(led, hm(30), Decimal("1"))])
    real = instance._conn

    class FailCommitAndRollback:
        def __init__(self, real_connection):
            self._real = real_connection

        def __getattr__(self, name):
            return getattr(self._real, name)

        def execute(self, sql, *args):
            if sql == "COMMIT":
                raise sqlite3.OperationalError("injected commit failure")
            if sql == "ROLLBACK":
                raise sqlite3.OperationalError("injected rollback failure")
            return self._real.execute(sql, *args)

    instance._conn = FailCommitAndRollback(real)
    try:
        # The original COMMIT failure is preserved even when cleanup also fails.
        with pytest.raises(sqlite3.OperationalError, match="injected commit failure"):
            instance.merge(STREAM, [reading(led, hm(30), Decimal("2"))])
        # Fail closed: no further use of the uncertain connection is allowed,
        # including the no-op (empty batch) ingestion path.
        with pytest.raises(led.LedgerError):
            instance.merge(STREAM, [reading(led, hm(30), Decimal("3"))])
        with pytest.raises(led.LedgerError):
            instance.merge(STREAM, [])
        with pytest.raises(led.LedgerError):
            instance.publication_snapshot(STREAM)
        with pytest.raises(led.LedgerError):
            instance.pending_publication(STREAM)
    finally:
        instance._conn = real
        instance.close()


def test_empty_merge_on_invalidated_instance_does_not_read_uncertain_state(led, tmp_path):
    """An empty merge must not answer from the still-open, uncommitted transaction."""
    path = tmp_path / "ledger.sqlite3"
    instance = led.IntervalLedger(path)
    instance.merge(STREAM, [reading(led, hm(30), Decimal("1"))])
    real = instance._conn

    class FailCommitAndRollback:
        def __init__(self, real_connection):
            self._real = real_connection

        def __getattr__(self, name):
            return getattr(self._real, name)

        def execute(self, sql, *args):
            if sql in ("COMMIT", "ROLLBACK"):
                raise sqlite3.OperationalError("injected " + sql + " failure")
            return self._real.execute(sql, *args)

    instance._conn = FailCommitAndRollback(real)
    with pytest.raises(sqlite3.OperationalError, match="injected COMMIT failure"):
        instance.merge(STREAM, [reading(led, hm(30), Decimal("2"))])
    # The connection is invalidated while the uncommitted generation stays open.
    assert instance._usable is False and real.in_transaction

    reads = []

    class CountingConnection:
        def __getattr__(self, name):
            return getattr(real, name)

        def execute(self, sql, *args):
            if sql.lstrip().upper().startswith("SELECT"):
                reads.append(sql)  # would report the never-committed generation 2
            return real.execute(sql, *args)

    instance._conn = CountingConnection()
    try:
        with pytest.raises(led.LedgerError):
            instance.merge(STREAM, [])
        with pytest.raises(led.LedgerError):
            instance.merge(STREAM, [reading(led, hm(30), Decimal("3"))])
        assert reads == []
    finally:
        instance._conn = real
        instance.close()

    with led.IntervalLedger(path) as reopened:
        snapshot = reopened.publication_snapshot(STREAM)
        assert snapshot.generation == 1
        assert str(snapshot.known_total_kwh) == "1.000000"


# ---------------------------------------------------------------------------
# M2: one consistent read snapshot for metadata, dirty decision, rows, coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("api", ["publication_snapshot", "pending_publication"])
@pytest.mark.parametrize("journal_mode", ["delete", "wal"])
def test_read_snapshot_is_consistent_across_independent_connections(led, tmp_path, api, journal_mode):
    path = tmp_path / "ledger.sqlite3"
    reader = led.IntervalLedger(path)
    reader.merge(STREAM, [reading(led, hm(30), Decimal("1"))])

    tuning = sqlite3.connect(path, isolation_level=None)
    applied = tuning.execute(f"PRAGMA journal_mode = {journal_mode}").fetchone()[0]
    tuning.close()
    assert applied.lower() == journal_mode

    writer = led.IntervalLedger(path, busy_timeout_ms=20)
    real = reader._conn
    correction = {"attempted": False, "error": None}

    class BetweenMetadataAndRows:
        def __getattr__(self, name):
            return getattr(real, name)

        def execute(self, sql, *args):
            if sql.startswith("SELECT end_ts") and not correction["attempted"]:
                correction["attempted"] = True
                try:
                    writer.merge(STREAM, [reading(led, hm(30), Decimal("2"))])
                except Exception as err:  # noqa: BLE001 - recorded as evidence
                    correction["error"] = err
            return real.execute(sql, *args)

    reader._conn = BetweenMetadataAndRows()
    try:
        snapshot = getattr(reader, api)(STREAM)
    finally:
        reader._conn = real

    assert correction["attempted"] is True
    # Whatever the concurrent writer did, the returned payload belongs to the
    # generation printed on the snapshot.
    assert snapshot is not None
    assert snapshot.generation == 1
    assert states(snapshot) == ["1"]
    if journal_mode == "delete":
        # A read lock cannot be committed through in rollback-journal mode, so the
        # correction never landed and generation 1 is still the current generation.
        assert correction["error"] is not None
        assert reader.acknowledge(STREAM, 1) is True
        assert reader.pending_publication(STREAM) is None
    else:
        # WAL lets the writer commit, but the reader keeps its own snapshot.
        assert correction["error"] is None
        # A stale acknowledgement must still not clear the newer committed generation.
        assert reader.acknowledge(STREAM, 1) is False
        pending = reader.pending_publication(STREAM)
        assert pending is not None and pending.generation == 2
        assert states(pending) == ["2"]
    reader.close()
    writer.close()


def test_read_transaction_is_cleaned_up_after_a_read_error(led, tmp_path):
    """A failing row read must not leave a transaction or read lock open."""
    path = tmp_path / "ledger.sqlite3"
    instance = led.IntervalLedger(path)
    instance.merge(STREAM, [reading(led, hm(30), Decimal("1"))])
    real = instance._conn

    class FailingRows:
        def __getattr__(self, name):
            return getattr(real, name)

        def execute(self, sql, *args):
            if sql.startswith("SELECT end_ts"):
                raise sqlite3.OperationalError("injected read failure")
            return real.execute(sql, *args)

    instance._conn = FailingRows()
    try:
        with pytest.raises(sqlite3.OperationalError, match="injected read failure"):
            instance.publication_snapshot(STREAM)
    finally:
        instance._conn = real

    assert real.in_transaction is False
    assert states(instance.publication_snapshot(STREAM)) == ["1"]
    # An independent writer can proceed: no read lock was leaked.
    witness = sqlite3.connect(path, isolation_level=None)
    witness.execute("BEGIN IMMEDIATE")
    witness.execute("ROLLBACK")
    witness.close()
    instance.close()


# ---------------------------------------------------------------------------
# M3: exact context-independent output Decimals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prec", [3, 6, 28])
@pytest.mark.parametrize(
    "rounding",
    [
        decimal.ROUND_DOWN,
        decimal.ROUND_UP,
        decimal.ROUND_HALF_EVEN,
        decimal.ROUND_CEILING,
        decimal.ROUND_FLOOR,
    ],
)
def test_publication_decimals_ignore_ambient_context(led, tmp_path, prec, rounding):
    path = tmp_path / "ledger.sqlite3"
    instance = led.IntervalLedger(path)
    instance.merge(
        STREAM,
        [reading(led, hm(30), Decimal("1.234567")), reading(led, hm(90), Decimal("0.000001"))],
    )
    with decimal.localcontext() as context:
        context.prec = prec
        context.rounding = rounding
        context.traps[decimal.Inexact] = True
        context.traps[decimal.Rounded] = True
        snapshot = instance.publication_snapshot(STREAM)
        pending = instance.pending_publication(STREAM)

    assert [str(row.energy_kwh) for row in snapshot.rows] == ["1.234567", "0.000001"]
    assert [str(row.state_kwh) for row in snapshot.rows] == ["1.234567", "1.234568"]
    assert str(snapshot.known_total_kwh) == "1.234568"
    assert pending is not None and str(pending.known_total_kwh) == "1.234568"
    instance.close()


def test_large_cumulative_decimals_are_exact_after_reopen(led, tmp_path):
    path = tmp_path / "ledger.sqlite3"
    instance = led.IntervalLedger(path)
    big = Decimal("100000000")
    instance.merge(
        STREAM,
        [reading(led, hm(30), big), reading(led, hm(90), big), reading(led, hm(150), big)],
    )
    instance.close()

    reopened = led.IntervalLedger(path)
    with decimal.localcontext() as context:
        context.prec = 3
        context.rounding = decimal.ROUND_DOWN
        snapshot = reopened.publication_snapshot(STREAM)
    assert [str(row.state_kwh) for row in snapshot.rows] == [
        "100000000.000000",
        "200000000.000000",
        "300000000.000000",
    ]
    assert str(snapshot.known_total_kwh) == "300000000.000000"
    reopened.close()


# ---------------------------------------------------------------------------
# M4: storage ownership and required-schema validation
# ---------------------------------------------------------------------------


def _make_unrelated_database(path):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE unrelated (value TEXT)")
    connection.execute("INSERT INTO unrelated VALUES ('preserve me')")
    connection.commit()
    connection.close()


def _replace_table(path, table, ddl):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute(f"DROP TABLE {table}")
    connection.execute(ddl)
    connection.commit()
    connection.close()


def test_unrelated_nonempty_database_is_rejected_without_mutation(led, tmp_path):
    path = tmp_path / "unrelated.sqlite3"
    _make_unrelated_database(path)
    with pytest.raises(led.LedgerSchemaError):
        led.IntervalLedger(path)

    connection = sqlite3.connect(path)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    rows = connection.execute("SELECT * FROM unrelated").fetchall()
    connection.close()
    assert tables == {"unrelated"}
    assert rows == [("preserve me",)]


def test_marker_only_database_is_rejected_at_open(led, tmp_path):
    path = tmp_path / "marker_only.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE ledger_meta (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL)")
    connection.execute("INSERT INTO ledger_meta VALUES ('schema_version', '1')")
    connection.commit()
    connection.close()
    with pytest.raises(led.LedgerSchemaError):
        led.IntervalLedger(path)


@pytest.mark.parametrize(
    "table,ddl",
    [
        (
            "interval_record",
            "CREATE TABLE interval_record (stream_id TEXT NOT NULL, end_ts INTEGER NOT NULL, "
            "energy_ukwh INTEGER NOT NULL, PRIMARY KEY (stream_id, end_ts))",
        ),  # required foreign key missing
        (
            "interval_record",
            "CREATE TABLE interval_record (stream_id TEXT NOT NULL REFERENCES stream (stream_id), "
            "end_ts INTEGER NOT NULL, energy_ukwh INTEGER NOT NULL, PRIMARY KEY (end_ts))",
        ),  # wrong primary key
        (
            "stream",
            "CREATE TABLE stream (stream_id TEXT PRIMARY KEY NOT NULL, contract_id TEXT NOT NULL, "
            "unit TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0)",
        ),  # required columns missing
        (
            "ledger_meta",
            "CREATE TABLE ledger_meta (key TEXT PRIMARY KEY NOT NULL)",
        ),  # required column missing
    ],
)
def test_malformed_owned_schema_is_rejected_at_open(led, tmp_path, table, ddl):
    path = tmp_path / "malformed.sqlite3"
    led.IntervalLedger(path).close()
    _replace_table(path, table, ddl)
    with pytest.raises(led.LedgerSchemaError):
        led.IntervalLedger(path)


def test_composite_foreign_key_is_not_mistaken_for_the_required_relationship(led, tmp_path, monkeypatch):
    """A component of a composite FK must not satisfy the required one-column FK."""
    path = tmp_path / "composite_fk.sqlite3"
    led.IntervalLedger(path).close()
    _replace_table(
        path,
        "interval_record",
        "CREATE TABLE interval_record (stream_id TEXT NOT NULL, end_ts INTEGER NOT NULL, "
        "energy_ukwh INTEGER NOT NULL, PRIMARY KEY (stream_id, end_ts), "
        "FOREIGN KEY (stream_id, energy_ukwh) REFERENCES stream (stream_id, generation))",
    )
    connection = sqlite3.connect(path)
    fk_rows = connection.execute("PRAGMA foreign_key_list(interval_record)").fetchall()
    before = connection.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    connection.close()
    # One PRAGMA row per referenced column, sharing a constraint id and sequence-ordered.
    assert [(row[0], row[1], row[3], row[4]) for row in fk_rows] == [
        (0, 0, "stream_id", "stream_id"),
        (0, 1, "energy_ukwh", "generation"),
    ]
    # The required triple is present only as one component of an unusable constraint.
    assert ("stream_id", "stream", "stream_id") in {(row[3], row[2], row[4]) for row in fk_rows}

    opened = []
    real_connect = led.sqlite3.connect

    def recording_connect(*args, **kwargs):
        opened_connection = real_connect(*args, **kwargs)
        opened.append(opened_connection)
        return opened_connection

    monkeypatch.setattr(led.sqlite3, "connect", recording_connect)
    with pytest.raises(led.LedgerSchemaError):
        led.IntervalLedger(path)

    # The rejected constructor closes its connection instead of leaking a lock.
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")

    # The rejected file is neither repaired nor migrated, and no stream was written.
    connection = real_connect(path)
    after = connection.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    stream_rows = connection.execute("SELECT COUNT(*) FROM stream").fetchone()[0]
    connection.close()
    assert before == after
    assert stream_rows == 0


def test_rejected_constructor_closes_its_connection(led, tmp_path, monkeypatch):
    path = tmp_path / "unrelated.sqlite3"
    _make_unrelated_database(path)
    opened = []
    real_connect = led.sqlite3.connect

    def recording_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(led.sqlite3, "connect", recording_connect)
    with pytest.raises(led.LedgerSchemaError):
        led.IntervalLedger(path)
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


def test_genuine_ledger_reopens_after_ownership_and_schema_validation(led, tmp_path):
    path = tmp_path / "ledger.sqlite3"
    instance = led.IntervalLedger(path)
    instance.merge(STREAM, half_hour_batch(led, H0, [1, 2]))
    assert instance.acknowledge(STREAM, 1) is True
    instance.close()

    reopened = led.IntervalLedger(path)
    assert reopened.schema_version == led.SCHEMA_VERSION
    assert states(reopened.publication_snapshot(STREAM)) == ["1", "3"]
    assert reopened.pending_publication(STREAM) is None
    reopened.close()
