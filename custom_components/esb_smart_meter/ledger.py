"""Durable half-hour interval accounting core for ESB Smart Meter (Phase-1 candidate).

NOT WIRED IN
------------
This module is a deliberate Phase-1 staging candidate. It is not imported by
``__init__.py``, ``coordinator.py``, ``sensor.py`` or ``statistics.py``, and nothing
in the integration writes statistics from it yet. Do not treat its presence as a
working end-to-end accounting path; the adapter phase (ledger -> statistics
publication) is a separate, independently reviewed change.

Why it exists (review findings H1-H3)
-------------------------------------
The PR8 importer aggregates a transient CSV window into hourly buckets with no
durable per-interval identity, no revision rule and a 90-day wall-clock cutoff.
Consequences reproduced by the QA packet: duplicate rows inflate totals, a truncated
batch rewrites previously complete hours, a correction does not shift later cumulative
sums, and a >366-day-old baseline restarts accounting at zero. This module is the
durable ledger those paths need: identity per physical half-hour interval, atomic
merges, a persisted dirty generation, and full-origin replay snapshots.

Input boundary (fixed by design, not inferred here)
---------------------------------------------------
``merge()`` accepts readings that a *future verified ESB parser* has already
normalized to:

* offset-aware UTC datetimes representing the half-hour interval **END** instant,
  exactly on the :00/:30 boundaries. Naive instants and non-UTC offsets are rejected
  (fail closed) rather than converted: silently attaching a timezone is the exact
  class of bug under remediation.
* finite, non-negative interval energy in kWh.

This module never parses an ESB civil label, never guesses a timezone or ``fold``,
never infers ordering or completeness from a CSV, and never repairs a malformed
stream identity. The ESB timestamp basis (UTC vs fixed GMT vs Irish civil time) is
still UNKNOWN and remains a release gate: no historical publication may be enabled
until the endpoint/file contract is evidenced. Until then, callers must feed only
verified normalized instants.

Storage
-------
Standard library only (``sqlite3``, ``decimal``, ``datetime``). The ledger is a
small SQLite file in a caller-provided Home Assistant integration storage path --
never the HA Recorder database, and never written to directly by this module's
consumers. An existing file is only adopted when it carries this ledger's explicit
ownership marker and the exact required schema (tables, columns, primary keys and
foreign keys); anything else fails closed before any write, so an unrelated database
is never adopted or mutated. Schema version and per-stream normalization-contract
identity are stored and checked; a mismatch fails closed with no writes. Version-1
files from the never-deployed phase-1 candidate are rejected as unowned rather than
migrated.

Energy representation
---------------------
Fixed point: interval energy is stored as an integer number of micro-kWh
(``ENERGY_SCALE = 6`` decimal places), so sums are exact and replay after a restart
is byte-identical. Values requiring more precision are quantized with
``ROUND_HALF_UP``; values outside the documented magnitude guard are rejected
(``InvalidIntervalError``). Non-finite and negative inputs are rejected.

Semantics
---------
* Missing intervals never delete or zero a known record; a truncated batch is a no-op
  for the intervals it omits.
* Same interval plus same value is a no-op across reopens; the publication generation
  only advances when data actually changes.
* Conflicting values for one interval inside a single batch reject the whole batch
  atomically -- row order is never used to pick a winner. A later batch that carries a
  single unambiguous value for an interval revises it.
* There is no 90-day retention, no pruning and no arbitrary baseline expiry.
* Snapshots replay absolute hourly state/sum from the full origin, so an earlier
  backfill or correction rebases every later retained hour automatically.
* Sparse hours are reported as known-only energy with explicit coverage/completeness.
  The cumulative value is the sum of *known* energy, NOT a meter lifetime total and
  NOT a register reading; do not label it as such in an entity or attribute.
* ``dirty`` is set in the same transaction as the data change, before any publication
  attempt. ``acknowledge()`` accepts only the exact published generation, so a stale
  confirmation cannot clear a newer pending state. Acknowledging is a caller
  obligation that must happen only after the caller has verified the recorder rows for
  that generation (adapter phase).
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Any, Iterable, Iterator

SCHEMA_VERSION = 2

# Explicit storage ownership marker. Only a database that carries this exact
# key/value pair is adopted as a ledger; phase-1 version-1 files never shipped, so
# they are rejected as unowned instead of being migrated in place.
OWNERSHIP_MARKER_KEY = "ledger_owner"
OWNERSHIP_MARKER = "esb-smart-meter-interval-ledger"

# Identity of the normalization contract a stream's data was produced under. Bump it
# only when the meaning of a stored interval changes; a mismatch fails closed.
NORMALIZATION_CONTRACT_ID = "esb-halfhour-interval-end-utc/v1"

DEFAULT_UNIT = "kWh"
ENERGY_SCALE = 6  # micro-kWh fixed point
ENERGY_QUANTUM = Decimal(1).scaleb(-ENERGY_SCALE)
MAX_INTERVAL_UKWH = 10**15  # 1e9 kWh per interval: int64 safety guard, not a judgment
HALF_HOUR_SECONDS = 1800
HOUR_SECONDS = 3600
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MAX_IDENTIFIER_LENGTH = 200

_SCHEMA_STATEMENTS = (
    "CREATE TABLE ledger_meta (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL)",
    """
    CREATE TABLE stream (
        stream_id TEXT PRIMARY KEY NOT NULL,
        contract_id TEXT NOT NULL,
        unit TEXT NOT NULL,
        generation INTEGER NOT NULL DEFAULT 0,
        dirty INTEGER NOT NULL DEFAULT 0,
        dirty_generation INTEGER NOT NULL DEFAULT 0,
        verified_generation INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE interval_record (
        stream_id TEXT NOT NULL REFERENCES stream (stream_id),
        end_ts INTEGER NOT NULL,
        energy_ukwh INTEGER NOT NULL,
        PRIMARY KEY (stream_id, end_ts)
    )
    """,
)

# Required shape of an adopted ledger database. Validated on open rather than
# inferred from the version marker alone: a version number with missing tables,
# columns, primary keys or the interval foreign key is not a usable ledger.
_REQUIRED_COLUMNS = {
    "ledger_meta": ("key", "value"),
    "stream": (
        "stream_id",
        "contract_id",
        "unit",
        "generation",
        "dirty",
        "dirty_generation",
        "verified_generation",
    ),
    "interval_record": ("stream_id", "end_ts", "energy_ukwh"),
}
_REQUIRED_PRIMARY_KEYS = {
    "ledger_meta": ("key",),
    "stream": ("stream_id",),
    "interval_record": ("stream_id", "end_ts"),
}
_REQUIRED_FOREIGN_KEYS = {
    "interval_record": (("stream_id", "stream", "stream_id"),),
}


class LedgerError(Exception):
    """Base class for ledger failures."""


class LedgerSchemaError(LedgerError):
    """The ledger file has an unsupported or unreadable schema version."""


class ContractMismatchError(LedgerError):
    """A stream's stored normalization contract/unit differs from the incoming one."""


class InvalidIntervalError(LedgerError):
    """A reading violates the normalized input boundary or a caller argument is invalid."""


class ConflictingIntervalError(LedgerError):
    """One batch carries two different values for the same interval; no ordering exists."""


class UnknownStreamError(LedgerError):
    """The requested stream has no persisted state yet."""


@dataclass(frozen=True)
class IntervalReading:
    """One already-normalized half-hour interval END reading."""

    end: datetime
    energy_kwh: Any


@dataclass(frozen=True)
class MergeResult:
    """Outcome of one atomic batch merge."""

    stream_id: str
    generation: int
    changed: bool
    inserted: int
    revised: int
    unchanged: int
    earliest_changed_hour: datetime | None


@dataclass(frozen=True)
class HourRow:
    """One UTC hour of known-only energy and its cumulative known-only state/sum."""

    hour_start: datetime
    energy_kwh: Decimal
    state_kwh: Decimal
    complete: bool


@dataclass(frozen=True)
class Coverage:
    """Explicit coverage/completeness of the retained series; gaps are never filled."""

    first_hour: datetime | None
    last_hour: datetime | None
    known_hours: int
    complete_hours: int
    incomplete_hours: int
    known_intervals: int
    missing_hours: int


@dataclass(frozen=True)
class PublicationSnapshot:
    """Immutable full-origin replay for one generation of one stream."""

    stream_id: str
    generation: int
    rows: tuple[HourRow, ...]
    coverage: Coverage

    @property
    def known_total_kwh(self) -> Decimal:
        """Cumulative known-only energy in the snapshot; not a meter lifetime total."""
        return self.rows[-1].state_kwh if self.rows else Decimal(0)


def _validate_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise InvalidIntervalError(f"{label} must be a non-empty trimmed string")
    if len(value) > _MAX_IDENTIFIER_LENGTH:
        raise InvalidIntervalError(f"{label} exceeds {_MAX_IDENTIFIER_LENGTH} characters")
    return value


def _end_to_epoch(end: Any) -> int:
    """Normalize an interval END instant to an exact half-hour UTC epoch second."""
    if not isinstance(end, datetime):
        raise InvalidIntervalError("interval end must be a datetime")
    if end.tzinfo is None or end.utcoffset() is None:
        raise InvalidIntervalError("interval end must be offset-aware; naive instants are rejected")
    if end.utcoffset() != timedelta(0):
        raise InvalidIntervalError("interval end must be normalized UTC (offset 0)")
    delta = end.astimezone(timezone.utc) - _EPOCH
    micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    if micros % (HALF_HOUR_SECONDS * 1_000_000):
        raise InvalidIntervalError("interval end must be exactly on a half-hour boundary")
    return micros // 1_000_000


def _energy_to_ukwh(value: Any) -> int:
    """Convert kWh to exact micro-kWh under the documented precision policy."""
    try:
        if isinstance(value, Decimal):
            decimal_value = value
        elif isinstance(value, bool):
            raise InvalidIntervalError("interval energy must be numeric")
        elif isinstance(value, (int, float)):
            decimal_value = Decimal(str(value))
        elif isinstance(value, str):
            decimal_value = Decimal(value.strip())
        else:
            raise InvalidIntervalError("interval energy must be numeric")
    except InvalidOperation as err:
        raise InvalidIntervalError(f"interval energy is not a valid number: {value!r}") from err
    except ValueError as err:
        raise InvalidIntervalError(f"interval energy is not a valid number: {value!r}") from err

    if not decimal_value.is_finite():
        raise InvalidIntervalError("interval energy must be finite")
    if decimal_value < 0:
        raise InvalidIntervalError("interval energy must be non-negative")

    with localcontext() as context:
        context.prec = 60
        try:
            quantized = decimal_value.quantize(ENERGY_QUANTUM, rounding=ROUND_HALF_UP)
            ukwh = int(quantized.scaleb(ENERGY_SCALE))
        except InvalidOperation as err:
            raise InvalidIntervalError("interval energy is outside the supported range") from err

    if ukwh > MAX_INTERVAL_UKWH:
        raise InvalidIntervalError("interval energy is outside the supported range")
    return ukwh


def _ukwh_to_decimal(ukwh: int) -> Decimal:
    """Exact Decimal for an integer micro-kWh value, independent of caller context.

    The value is built from a plain string, so no active ``decimal`` context can
    round, re-scale or trap the published hourly energy or cumulative state.
    """
    sign = "-" if ukwh < 0 else ""
    digits = str(abs(ukwh)).rjust(ENERGY_SCALE + 1, "0")
    return Decimal(f"{sign}{digits[:-ENERGY_SCALE]}.{digits[-ENERGY_SCALE:]}")


def _hour_start_ts(end_ts: int) -> int:
    """UTC hour start of the interval ending at ``end_ts`` (elapsed 30 min, then floor)."""
    return ((end_ts - HALF_HOUR_SECONDS) // HOUR_SECONDS) * HOUR_SECONDS


def _from_epoch(epoch_seconds: int) -> datetime:
    return _EPOCH + timedelta(seconds=epoch_seconds)


class IntervalLedger:
    """One logical owner per stream over a small SQLite interval ledger.

    A single instance owns one connection and is intended for one thread. Several
    instances (threads or processes) may share a file: merges run in
    ``BEGIN IMMEDIATE`` transactions with a busy timeout, which serializes writers
    instead of interleaving them.
    """

    def __init__(self, path: str | os.PathLike[str], *, busy_timeout_ms: int = 10000) -> None:
        self._path = os.fspath(path)
        self._usable = True
        self._conn = sqlite3.connect(
            self._path,
            isolation_level=None,
            timeout=busy_timeout_ms / 1000.0,
        )
        self._conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        self._conn.execute("PRAGMA foreign_keys = ON")
        try:
            self._schema_version = self._initialise()
        except BaseException:
            # A rejected constructor must not leak an open connection or its lock.
            self._conn.close()
            self._usable = False
            raise

    # -- lifecycle ---------------------------------------------------------

    @property
    def schema_version(self) -> int:
        return self._schema_version

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "IntervalLedger":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    @contextmanager
    def _write_txn(self) -> Iterator[None]:
        self._require_usable()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as err:
            raise LedgerError(f"could not acquire the ledger write lock: {err}") from err
        try:
            yield
        except BaseException:
            self._rollback_or_fail_closed()
            raise
        try:
            # COMMIT shares the failure boundary: a busy or failed commit must roll
            # the transaction back instead of leaving failed work open and published.
            self._conn.execute("COMMIT")
        except BaseException:
            self._rollback_or_fail_closed()
            raise

    @contextmanager
    def _read_txn(self) -> Iterator[None]:
        """One consistent read snapshot covering stream metadata and all rows.

        Metadata, the dirty decision, interval rows and coverage are read inside a
        single SQLite read transaction, so a concurrent commit cannot be observed
        between them (the generation printed on a snapshot always matches its payload).
        """
        self._require_usable()
        try:
            self._conn.execute("BEGIN")
        except sqlite3.OperationalError as err:
            raise LedgerError(f"could not open the ledger read transaction: {err}") from err
        try:
            yield
        except BaseException:
            self._rollback_or_fail_closed()
            raise
        try:
            self._conn.execute("COMMIT")
        except BaseException:
            self._rollback_or_fail_closed()
            raise

    def _require_usable(self) -> None:
        if not self._usable:
            raise LedgerError("ledger connection is no longer usable after failed cleanup")

    def _rollback_or_fail_closed(self) -> None:
        """Roll back if a transaction is still open, else fail closed.

        The caller re-raises the original failure, so any error raised while cleaning
        up is deliberately not allowed to mask it; if cleanup cannot prove the
        connection is out of its transaction the instance becomes unusable.
        """
        try:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            if self._conn.in_transaction:
                raise sqlite3.OperationalError("ROLLBACK did not end the transaction")
        except BaseException:  # noqa: BLE001 - fail closed, keep the original error
            self._usable = False

    def _initialise(self) -> int:
        try:
            if not self._db_is_empty():
                return self._verify_existing_ledger()
            with self._write_txn():
                if self._db_is_empty():
                    self._create_schema()
                    return SCHEMA_VERSION
            return self._verify_existing_ledger()
        except sqlite3.DatabaseError as err:
            raise LedgerError(f"{self._path} is not a usable ledger database: {err}") from err

    def _db_is_empty(self) -> bool:
        return int(self._conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]) == 0

    def _create_schema(self) -> None:
        for statement in _SCHEMA_STATEMENTS:
            self._conn.execute(statement)
        self._conn.executemany(
            "INSERT INTO ledger_meta (key, value) VALUES (?, ?)",
            (
                (OWNERSHIP_MARKER_KEY, OWNERSHIP_MARKER),
                ("schema_version", str(SCHEMA_VERSION)),
            ),
        )

    def _meta_value(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM ledger_meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else row[0]

    def _table_names(self) -> set[str]:
        return {
            row[0]
            for row in self._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    def _verify_existing_ledger(self) -> int:
        """Validate ownership and the exact schema needed for atomic accounting.

        Rejects an unrelated database, a marker-only file and a malformed ledger
        without writing anything or repairing anything in place.
        """
        tables = self._table_names()
        # Validate the required shape before reading any metadata value: a file whose
        # ledger_meta lacks the 'value' column (or any required table) is rejected
        # here instead of failing later on a malformed read.
        self._verify_required_schema(tables)
        if self._meta_value(OWNERSHIP_MARKER_KEY) != OWNERSHIP_MARKER:
            raise LedgerSchemaError(
                f"{self._path} is not an ESB interval ledger database "
                f"(missing {OWNERSHIP_MARKER_KEY!r} ownership marker)"
            )
        raw_version = self._meta_value("schema_version")
        try:
            version = int(raw_version) if raw_version is not None else -1
        except (TypeError, ValueError):
            version = -1
        if version != SCHEMA_VERSION:
            raise LedgerSchemaError(
                f"ledger schema version {version} is unsupported (expected {SCHEMA_VERSION})"
            )
        return version

    def _verify_required_schema(self, tables: set[str]) -> None:
        for table, columns in _REQUIRED_COLUMNS.items():
            if table not in tables:
                raise LedgerSchemaError(f"ledger database is missing required table {table!r}")
            info = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
            present = {row[1] for row in info}
            missing = [column for column in columns if column not in present]
            if missing:
                raise LedgerSchemaError(
                    f"ledger table {table!r} is missing required columns {missing}"
                )
            primary_key = tuple(row[1] for row in sorted(info, key=lambda row: row[5]) if row[5])
            if primary_key != _REQUIRED_PRIMARY_KEYS[table]:
                raise LedgerSchemaError(
                    f"ledger table {table!r} has an unexpected primary key {primary_key!r}"
                )
        for table, keys in _REQUIRED_FOREIGN_KEYS.items():
            # PRAGMA foreign_key_list emits one row per referenced column, so matching
            # individual triples would let one component of a composite constraint pass
            # as the required single-column relationship. Rebuild each whole constraint
            # by its id in sequence order and accept only one-column constraints.
            by_constraint: dict[Any, list[tuple[Any, ...]]] = {}
            for row in self._conn.execute(f"PRAGMA foreign_key_list({table})"):
                by_constraint.setdefault(row[0], []).append((row[1], row[3], row[2], row[4]))
            declared = set()
            for components in by_constraint.values():
                ordered = sorted(components)
                if len(ordered) == 1:
                    _, from_column, ref_table, to_column = ordered[0]
                    declared.add((from_column, ref_table, to_column))
            for key in keys:
                if key not in declared:
                    raise LedgerSchemaError(
                        f"ledger table {table!r} is missing required foreign key {key!r}"
                    )

    # -- ingestion ---------------------------------------------------------

    def merge(
        self,
        stream_id: str,
        readings: Iterable[IntervalReading],
        *,
        unit: str = DEFAULT_UNIT,
        contract_id: str | None = None,
    ) -> MergeResult:
        """Atomically merge one batch of normalized readings for one stream.

        Validation completes before any write, so an invalid or conflicting batch
        changes nothing. Identical replays are no-ops. When data changes, the stream's
        publication generation advances and the dirty flag is committed in the same
        transaction.
        """
        # Guard the public entry before any connection access: an empty batch must not
        # be answered by a read of an invalidated, still-open transaction.
        self._require_usable()
        stream_id = _validate_identifier(stream_id, "stream_id")
        contract = _validate_identifier(
            contract_id if contract_id is not None else NORMALIZATION_CONTRACT_ID,
            "contract_id",
        )
        if unit != DEFAULT_UNIT:
            raise InvalidIntervalError(f"unsupported unit {unit!r}; expected {DEFAULT_UNIT!r}")

        pending: dict[int, int] = {}
        for item in readings:
            if not isinstance(item, IntervalReading):
                raise InvalidIntervalError("readings must be IntervalReading instances")
            end_ts = _end_to_epoch(item.end)
            ukwh = _energy_to_ukwh(item.energy_kwh)
            existing = pending.get(end_ts)
            if existing is not None and existing != ukwh:
                raise ConflictingIntervalError(
                    f"batch carries conflicting values for the interval ending at {end_ts}"
                )
            pending[end_ts] = ukwh

        if not pending:
            return self._empty_merge_result(stream_id)

        inserted = revised = unchanged = 0
        earliest_changed: int | None = None

        with self._write_txn():
            row = self._conn.execute(
                "SELECT contract_id, unit, generation FROM stream WHERE stream_id = ?",
                (stream_id,),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO stream (stream_id, contract_id, unit) VALUES (?, ?, ?)",
                    (stream_id, contract, unit),
                )
                generation = 0
            else:
                if row[0] != contract:
                    raise ContractMismatchError(
                        f"stream {stream_id!r} was recorded under contract {row[0]!r}, "
                        f"not {contract!r}"
                    )
                if row[1] != unit:
                    raise ContractMismatchError(
                        f"stream {stream_id!r} was recorded in {row[1]!r}, not {unit!r}"
                    )
                generation = int(row[2])

            for end_ts in sorted(pending):
                ukwh = pending[end_ts]
                stored = self._conn.execute(
                    "SELECT energy_ukwh FROM interval_record WHERE stream_id = ? AND end_ts = ?",
                    (stream_id, end_ts),
                ).fetchone()
                if stored is None:
                    self._conn.execute(
                        "INSERT INTO interval_record (stream_id, end_ts, energy_ukwh) VALUES (?, ?, ?)",
                        (stream_id, end_ts, ukwh),
                    )
                    inserted += 1
                elif int(stored[0]) != ukwh:
                    self._conn.execute(
                        "UPDATE interval_record SET energy_ukwh = ? WHERE stream_id = ? AND end_ts = ?",
                        (ukwh, stream_id, end_ts),
                    )
                    revised += 1
                else:
                    unchanged += 1
                    continue
                earliest_changed = end_ts if earliest_changed is None else min(earliest_changed, end_ts)

            changed = bool(inserted or revised)
            if changed:
                generation += 1
                self._conn.execute(
                    "UPDATE stream SET generation = ?, dirty = 1, dirty_generation = ? WHERE stream_id = ?",
                    (generation, generation, stream_id),
                )

        return MergeResult(
            stream_id=stream_id,
            generation=generation,
            changed=changed,
            inserted=inserted,
            revised=revised,
            unchanged=unchanged,
            earliest_changed_hour=(
                _from_epoch(_hour_start_ts(earliest_changed)) if earliest_changed is not None else None
            ),
        )

    def _empty_merge_result(self, stream_id: str) -> MergeResult:
        row = self._conn.execute(
            "SELECT generation FROM stream WHERE stream_id = ?", (stream_id,)
        ).fetchone()
        return MergeResult(
            stream_id=stream_id,
            generation=int(row[0]) if row is not None else 0,
            changed=False,
            inserted=0,
            revised=0,
            unchanged=0,
            earliest_changed_hour=None,
        )

    # -- publication -------------------------------------------------------

    def publication_snapshot(self, stream_id: str) -> PublicationSnapshot:
        """Full-origin replay of the current generation (known-only, gaps preserved)."""
        stream_id = _validate_identifier(stream_id, "stream_id")
        with self._read_txn():
            row = self._stream_row(stream_id)
            return self._snapshot(stream_id, int(row[2]))

    def pending_publication(self, stream_id: str) -> PublicationSnapshot | None:
        """Snapshot that must be published, or ``None`` when nothing is dirty."""
        stream_id = _validate_identifier(stream_id, "stream_id")
        with self._read_txn():
            row = self._stream_row(stream_id)
            if not int(row[3]):
                return None
            return self._snapshot(stream_id, int(row[4]))

    def acknowledge(self, stream_id: str, generation: int) -> bool:
        """Verify the exact published generation; a stale ack never clears newer state.

        Only call this after the caller has verified the recorder rows for that
        generation. Returns ``True`` when the pending generation was cleared (or was
        already cleared), ``False`` for a stale or unknown generation.
        """
        stream_id = _validate_identifier(stream_id, "stream_id")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise LedgerError("generation must be a non-negative integer")

        with self._write_txn():
            row = self._stream_row(stream_id)
            if generation != int(row[4]):
                return False
            if not int(row[3]):
                return True
            self._conn.execute(
                "UPDATE stream SET dirty = 0, verified_generation = ? WHERE stream_id = ?",
                (generation, stream_id),
            )
        return True

    def _stream_row(self, stream_id: str) -> tuple[Any, ...]:
        row = self._conn.execute(
            "SELECT stream_id, contract_id, generation, dirty, dirty_generation, "
            "verified_generation FROM stream WHERE stream_id = ?",
            (stream_id,),
        ).fetchone()
        if row is None:
            raise UnknownStreamError(f"stream {stream_id!r} has no ledger state")
        return row

    def _snapshot(self, stream_id: str, generation: int) -> PublicationSnapshot:
        buckets: dict[int, list[int]] = {}
        for end_ts, energy_ukwh in self._conn.execute(
            "SELECT end_ts, energy_ukwh FROM interval_record WHERE stream_id = ? ORDER BY end_ts",
            (stream_id,),
        ):
            hour_ts = _hour_start_ts(int(end_ts))
            bucket = buckets.setdefault(hour_ts, [0, 0])
            bucket[0] += 1
            bucket[1] += int(energy_ukwh)

        rows: list[HourRow] = []
        running = 0
        known_intervals = 0
        for hour_ts in sorted(buckets):
            count, energy_ukwh = buckets[hour_ts]
            known_intervals += count
            running += energy_ukwh
            rows.append(
                HourRow(
                    hour_start=_from_epoch(hour_ts),
                    energy_kwh=_ukwh_to_decimal(energy_ukwh),
                    state_kwh=_ukwh_to_decimal(running),
                    complete=count == 2,
                )
            )

        coverage = Coverage(
            first_hour=rows[0].hour_start if rows else None,
            last_hour=rows[-1].hour_start if rows else None,
            known_hours=len(rows),
            complete_hours=sum(1 for row in rows if row.complete),
            incomplete_hours=sum(1 for row in rows if not row.complete),
            known_intervals=known_intervals,
            missing_hours=(
                int((rows[-1].hour_start - rows[0].hour_start).total_seconds() // HOUR_SECONDS) + 1 - len(rows)
                if rows
                else 0
            ),
        )
        return PublicationSnapshot(
            stream_id=stream_id,
            generation=generation,
            rows=tuple(rows),
            coverage=coverage,
        )
