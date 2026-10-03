"""Durable ownership + per-operation executor ledger collaborators (approved first phase).

Why this module exists
----------------------
``compatible_publisher`` owns a physical external statistic id for the *process*
lifetime and freezes an immutable submitted prefix in memory, but a memory
prefix cannot tell a legitimate **new process** restart from an unknown or
foreign history: after a crash the same process-global registry claim is gone
and the publisher's fail-closed pre-flight would either refuse its own published
series forever or, worse, adopt unknown occupancy. This module supplies the
missing durable provenance and the executor-safe ledger seam.

What is here (and deliberately nothing more)
--------------------------------------------
* :class:`SourceBinding` -- a **canonical versioned digest** of the configured
  current-account/product plus MPRN identity. This stage it is constructed
  synthetically by the caller with an explicit versioned contract id; no raw CSV
  is parsed here and no household row is read.
* :class:`OwnerRecord` -- owner record version 1: exact entry/slug/stream/statistic
  binding, ESB source + product, the source-binding digest, the ledger contract,
  unit/unit_class/mean_type/has_sum, the frozen origin, the complete cumulative
  submitted prefix (exact hex-encoded state/sum), the submitted generation and the
  last verified prefix length/generation.
* :class:`DurableOwnerStore` -- one atomic, fail-closed storage protocol:
  validate, write the complete next record to a private sibling tempfile, flush,
  fsync the file, ``os.replace`` within that directory, fsync the directory, then
  REOPEN THE TARGET and compare the whole decoded record. The reopened target is
  the only durable success result; every write/fsync/replace/readback failure
  propagates as :class:`OwnerRecordWriteError`; a post-replace failure is
  uncertain and must fence the publisher. There is no delayed-store save and no swallowed write.
* :class:`ExecutorIntervalLedger` / :class:`RuntimeLedgerOperations` -- one ledger
  open/close per executor operation (the frozen ledger is thread-affine), plus a
  narrow read-only reader of the integration-owned ledger's own stream flags and
  ``interval_record`` rows. No ``check_same_thread`` bypass, no persistent
  connection, no thread framework.

Deliberately *not* here: no raw CSV/API/coordinator wiring, no registry reset, no
boolean adoption switch, no general storage lifecycle framework.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .ledger import (
    DEFAULT_UNIT,
    IntervalLedger,
    IntervalReading,
    MergeResult,
    NORMALIZATION_CONTRACT_ID,
    PublicationSnapshot,
)

# ---------------------------------------------------------------- record contract

OWNER_RECORD_VERSION = 1
SOURCE_BINDING_CONTRACT = "esb-smart-meter-source-binding/v1"
PRODUCT_INTERVAL_KWH = "intervalkwh"
OWNER_RECORD_SUFFIX = ".owner.json"
LEDGER_SUFFIX = ".ledger"

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_]*[a-z0-9])?$")
_HOUR = timedelta(hours=1)
_MICRO_KWH = Decimal("0.000001")


class DurableStorageError(Exception):
    """Base class for durable owner-record storage failures."""


class OwnerRecordCorruptError(DurableStorageError):
    """The owner-record target exists but is not a valid versioned record."""


class OwnerRecordWriteError(DurableStorageError):
    """The owner-record target could not be durably written and reopened."""


class LedgerCollaboratorError(Exception):
    """A per-operation ledger collaborator failure (open/merge/read/ack)."""


# ------------------------------------------------------------------ path helpers


def owner_record_path(storage_dir: str | os.PathLike[str], slug: str) -> Path:
    """Deterministic owner-record target for an opaque per-entry slug."""
    return Path(storage_dir) / f"esb_smart_meter.{_checked_slug(slug)}{OWNER_RECORD_SUFFIX}"


def ledger_path(storage_dir: str | os.PathLike[str], slug: str) -> Path:
    """Deterministic integration-ledger target for an opaque per-entry slug."""
    return Path(storage_dir) / f"esb_smart_meter.{_checked_slug(slug)}{LEDGER_SUFFIX}"


def _checked_slug(slug: Any) -> str:
    if not isinstance(slug, str) or not _SLUG_RE.match(slug):
        raise DurableStorageError(f"invalid storage slug {slug!r}")
    return slug


# ---------------------------------------------------------------- source binding


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True)
class SourceBinding:
    """Canonical versioned digest of the configured source identity.

    The digest binds the configured current account, product and MPRN identity
    without persisting or logging any of them; only ``contract`` and ``digest``
    are stored. Comparing digests detects a changed source identity on restart.
    """

    contract: str
    digest: str

    def __repr__(self) -> str:  # never print the private digest
        return f"SourceBinding(contract={self.contract!r}, digest=<redacted>)"

    @classmethod
    def synthetic(
        cls,
        *,
        mprn: str,
        current_account: str = "current",
        product: str = PRODUCT_INTERVAL_KWH,
        contract: str = SOURCE_BINDING_CONTRACT,
    ) -> "SourceBinding":
        """Build the binding from an explicit (synthetic this stage) identity."""
        for label, value in (("mprn", mprn), ("current_account", current_account), ("product", product)):
            if not isinstance(value, str) or not value.strip():
                raise DurableStorageError(f"source binding {label} must be a non-empty string")
        material = _canonical_json(
            {
                "contract": contract,
                "current_account": current_account.strip(),
                "product": product.strip(),
                "mprn": mprn.strip(),
            }
        )
        return cls(contract=contract, digest=hashlib.sha256(material.encode("utf-8")).hexdigest())

    def validate(self) -> "SourceBinding":
        if not isinstance(self.contract, str) or not self.contract:
            raise DurableStorageError("source binding contract must be a non-empty string")
        if not isinstance(self.digest, str) or not re.fullmatch(r"[0-9a-f]{64}", self.digest):
            raise DurableStorageError("source binding digest must be a lowercase sha256 hex digest")
        return self


# ------------------------------------------------------------------ owner record


def _require_utc_hour(value: Any, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise DurableStorageError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise DurableStorageError(f"{label} must be offset-aware UTC")
    hour = value.astimezone(timezone.utc)
    if hour.minute or hour.second or hour.microsecond:
        raise DurableStorageError(f"{label} must be an exact UTC hour boundary")
    return hour


def _encode_float(value: Any, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DurableStorageError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise DurableStorageError(f"{label} must be finite")
    return number.hex()


def _decode_float(value: Any, label: str) -> float:
    if not isinstance(value, str):
        raise DurableStorageError(f"{label} must be a hex float string")
    try:
        number = float.fromhex(value)
    except (ValueError, OverflowError) as error:
        raise DurableStorageError(f"{label} is not a valid hex float") from error
    if not math.isfinite(number):
        raise DurableStorageError(f"{label} must be finite")
    return number


@dataclass(frozen=True)
class OwnerRecord:
    """Version-1 durable ownership record for one physical external statistic id."""

    entry_id: str
    slug: str
    statistic_id: str
    source: str
    product: str
    source_binding: SourceBinding
    ledger_contract: str
    unit: str
    unit_class: str
    mean_type: int
    has_sum: bool
    origin: datetime
    submitted_generation: int
    submitted_rows: tuple[tuple[datetime, float, float], ...] = ()
    verified_length: int = 0
    verified_generation: int = 0
    version: int = OWNER_RECORD_VERSION

    @property
    def stream_id(self) -> str:
        return self.slug

    @property
    def last_submitted_hour(self) -> datetime | None:
        return self.submitted_rows[-1][0] if self.submitted_rows else None

    @property
    def last_verified_hour(self) -> datetime | None:
        if self.verified_length <= 0:
            return None
        return self.submitted_rows[self.verified_length - 1][0]

    def submitted_value_map(self) -> dict[datetime, tuple[float, float]]:
        return {hour: (state, total) for hour, state, total in self.submitted_rows}

    def validate(self) -> "OwnerRecord":
        """Fail closed on any malformed field, non-contiguous prefix or bad marker."""
        if self.version != OWNER_RECORD_VERSION:
            raise DurableStorageError(f"unsupported owner record version {self.version!r}")
        for label, value in (
            ("entry_id", self.entry_id),
            ("slug", self.slug),
            ("statistic_id", self.statistic_id),
            ("source", self.source),
            ("product", self.product),
            ("ledger_contract", self.ledger_contract),
            ("unit", self.unit),
            ("unit_class", self.unit_class),
        ):
            if not isinstance(value, str) or not value:
                raise DurableStorageError(f"owner record {label} must be a non-empty string")
        _checked_slug(self.slug)
        self.source_binding.validate()
        if isinstance(self.mean_type, bool) or not isinstance(self.mean_type, int):
            raise DurableStorageError("owner record mean_type must be an integer")
        if not isinstance(self.has_sum, bool):
            raise DurableStorageError("owner record has_sum must be a bool")
        origin = _require_utc_hour(self.origin, "owner record origin")

        previous: datetime | None = None
        for index, row in enumerate(self.submitted_rows):
            if not isinstance(row, tuple) or len(row) != 3:
                raise DurableStorageError(f"owner record submitted row {index} must be a 3-tuple")
            hour = _require_utc_hour(row[0], f"owner record submitted row {index} hour")
            for position, value in ((1, row[1]), (2, row[2])):
                if isinstance(value, bool) or not isinstance(value, float) or not math.isfinite(value):
                    raise DurableStorageError(
                        f"owner record submitted row {index} field {position} must be a finite float"
                    )
            if index == 0 and hour != origin:
                raise DurableStorageError("owner record prefix must start at the frozen origin")
            if previous is not None and hour != previous + _HOUR:
                raise DurableStorageError("owner record prefix must be contiguous UTC hours")
            previous = hour

        for label, value in (
            ("submitted_generation", self.submitted_generation),
            ("verified_length", self.verified_length),
            ("verified_generation", self.verified_generation),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise DurableStorageError(f"owner record {label} must be a non-negative integer")
        if (self.verified_length == 0) != (self.verified_generation == 0):
            raise DurableStorageError("verified length and generation disagree")
        if (not self.submitted_rows) != (self.submitted_generation == 0):
            raise DurableStorageError("submitted prefix and generation disagree")
        if self.verified_generation == self.submitted_generation and self.submitted_generation and self.verified_length != len(self.submitted_rows):
            raise DurableStorageError("verified generation requires the complete prefix")
        if self.verified_length > len(self.submitted_rows):
            raise DurableStorageError("owner record verified_length exceeds the submitted prefix")
        if self.verified_generation > self.submitted_generation:
            raise DurableStorageError("owner record verified_generation exceeds submitted_generation")
        return self

    # -- (de)serialization -------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "entry_id": self.entry_id,
            "slug": self.slug,
            "statistic_id": self.statistic_id,
            "source": self.source,
            "product": self.product,
            "source_binding": {"contract": self.source_binding.contract, "digest": self.source_binding.digest},
            "ledger_contract": self.ledger_contract,
            "unit": self.unit,
            "unit_class": self.unit_class,
            "mean_type": self.mean_type,
            "has_sum": self.has_sum,
            "origin": self.origin.astimezone(timezone.utc).isoformat(),
            "submitted_generation": self.submitted_generation,
            "submitted_rows": [
                [hour.astimezone(timezone.utc).isoformat(), _encode_float(state, "state"), _encode_float(total, "sum")]
                for hour, state, total in self.submitted_rows
            ],
            "verified_length": self.verified_length,
            "verified_generation": self.verified_generation,
        }

    def encode(self) -> bytes:
        self.validate()
        return _canonical_json(self.as_dict()).encode("utf-8")

    @classmethod
    def decode(cls, payload: Any) -> "OwnerRecord":
        """Strictly decode one owner record; any shape/type problem fails closed."""
        if not isinstance(payload, dict):
            raise OwnerRecordCorruptError("owner record must be a JSON object")
        required = {
            "version",
            "entry_id",
            "slug",
            "statistic_id",
            "source",
            "product",
            "source_binding",
            "ledger_contract",
            "unit",
            "unit_class",
            "mean_type",
            "has_sum",
            "origin",
            "submitted_generation",
            "submitted_rows",
            "verified_length",
            "verified_generation",
        }
        missing = sorted(required - set(payload))
        if missing:
            raise OwnerRecordCorruptError(f"owner record is missing required keys {missing}")
        extra = sorted(set(payload) - required)
        if extra:
            raise OwnerRecordCorruptError(f"owner record has unexpected keys {extra}")

        binding = payload["source_binding"]
        if not isinstance(binding, dict) or set(binding) != {"contract", "digest"}:
            raise OwnerRecordCorruptError("owner record source_binding must be {contract, digest}")
        raw_rows = payload["submitted_rows"]
        if not isinstance(raw_rows, list):
            raise OwnerRecordCorruptError("owner record submitted_rows must be a list")

        try:
            origin = _decode_hour(payload["origin"], "origin")
            rows: list[tuple[datetime, float, float]] = []
            for index, row in enumerate(raw_rows):
                if not isinstance(row, list) or len(row) != 3:
                    raise OwnerRecordCorruptError(f"owner record submitted row {index} must be a 3-element list")
                rows.append(
                    (
                        _decode_hour(row[0], f"submitted row {index} hour"),
                        _decode_float(row[1], f"submitted row {index} state"),
                        _decode_float(row[2], f"submitted row {index} sum"),
                    )
                )
            record = cls(
                entry_id=payload["entry_id"],
                slug=payload["slug"],
                statistic_id=payload["statistic_id"],
                source=payload["source"],
                product=payload["product"],
                source_binding=SourceBinding(contract=binding["contract"], digest=binding["digest"]),
                ledger_contract=payload["ledger_contract"],
                unit=payload["unit"],
                unit_class=payload["unit_class"],
                mean_type=payload["mean_type"],
                has_sum=payload["has_sum"],
                origin=origin,
                submitted_generation=payload["submitted_generation"],
                submitted_rows=tuple(rows),
                verified_length=payload["verified_length"],
                verified_generation=payload["verified_generation"],
                version=payload["version"],
            )
        except DurableStorageError as error:
            raise OwnerRecordCorruptError(str(error)) from error
        try:
            record.validate()
        except DurableStorageError as error:
            raise OwnerRecordCorruptError(str(error)) from error
        return record


def _decode_hour(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise DurableStorageError(f"owner record {label} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise DurableStorageError(f"owner record {label} is not a valid ISO-8601 instant") from error
    return _require_utc_hour(parsed, f"owner record {label}")


# ------------------------------------------------------------ durable file protocol


def _fsync_fd(fd: int) -> None:
    os.fsync(fd)


def _replace_file(source: str, target: str) -> None:
    os.replace(source, target)


def _fsync_dir(directory: str) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class DurableOwnerStore:
    """Atomic, fail-closed owner-record storage for one target path."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = os.fspath(path)

    @property
    def path(self) -> str:
        return self._path

    def load(self) -> OwnerRecord | None:
        """Read the target: ``None`` when absent, typed error when unreadable."""
        try:
            raw = Path(self._path).read_bytes()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise OwnerRecordCorruptError(f"owner record {self._path} could not be read: {error}") from error
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise OwnerRecordCorruptError(f"owner record {self._path} is not valid JSON: {error}") from error
        return OwnerRecord.decode(payload)

    def save(self, record: OwnerRecord) -> OwnerRecord:
        """Durably persist ``record``; the reopened target is the success result."""
        data = record.encode()
        previous = self.load()
        if previous is not None:
            binding = replace(record, submitted_rows=previous.submitted_rows,
                              submitted_generation=previous.submitted_generation,
                              verified_length=previous.verified_length,
                              verified_generation=previous.verified_generation)
            if binding != previous or record.submitted_rows[:len(previous.submitted_rows)] != previous.submitted_rows:
                raise OwnerRecordWriteError("owner binding or immutable prefix changed")
            if record.submitted_generation < previous.submitted_generation or record.verified_length < previous.verified_length or record.verified_generation < previous.verified_generation:
                raise OwnerRecordWriteError("owner markers regressed")
        directory = os.path.dirname(self._path) or "."
        temporary: str | None = None
        try:
            fd, temporary = tempfile.mkstemp(
                dir=directory, prefix=f"{os.path.basename(self._path)}.", suffix=".tmp"
            )
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                _fsync_fd(handle.fileno())
            _replace_file(temporary, self._path)
            temporary = None
            _fsync_dir(directory)
        except OSError as error:
            raise OwnerRecordWriteError(
                f"owner record {self._path} could not be durably written: {error}"
            ) from error
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

        try:
            reopened = DurableOwnerStore(self._path).load()
        except DurableStorageError as error:
            raise OwnerRecordWriteError(
                f"owner record {self._path} could not be reopened after replace: {error}"
            ) from error
        if reopened is None or reopened != record:
            raise OwnerRecordWriteError(
                f"owner record {self._path} did not reopen identical to the saved record"
            )
        return reopened


# ------------------------------------------------------- per-operation ledger seam

HALF_HOUR_SECONDS = 1800
HOUR_SECONDS = 3600


def half_hour_end_to_hour_start(end_ts: int) -> int:
    """UTC hour start of the half-hour interval ending at ``end_ts`` (frozen contract)."""
    return ((end_ts - HALF_HOUR_SECONDS) // HOUR_SECONDS) * HOUR_SECONDS


@dataclass(frozen=True)
class LedgerStreamState:
    """Own integration-ledger evidence for one stream, read in one read transaction."""

    contract_id: str
    unit: str
    generation: int
    dirty: bool
    dirty_generation: int
    verified_generation: int
    intervals: tuple[tuple[int, int], ...]  # (end_ts, energy_ukwh), ascending

    @property
    def rows(self) -> int:
        return len(self.intervals)

    def hour_values(self) -> dict[int, tuple[float, float]]:
        """Exact hourly cumulative (state, sum) from the half-hour records themselves."""
        buckets: dict[int, int] = {}
        for end_ts, energy_ukwh in self.intervals:
            hour_ts = half_hour_end_to_hour_start(int(end_ts))
            buckets[hour_ts] = buckets.get(hour_ts, 0) + int(energy_ukwh)
        running = 0
        values: dict[int, tuple[float, float]] = {}
        for hour_ts in sorted(buckets):
            running += buckets[hour_ts]
            exact = _ukwh_float(running)
            values[hour_ts] = (exact, exact)
        return values

    def hour_interval_counts(self) -> dict[int, int]:
        buckets: dict[int, int] = {}
        for end_ts, _energy in self.intervals:
            hour_ts = half_hour_end_to_hour_start(int(end_ts))
            buckets[hour_ts] = buckets.get(hour_ts, 0) + 1
        return buckets


def _ukwh_float(ukwh: int) -> float:
    """Exact float for integer micro-kWh, matching the publisher's encoder."""
    return float(Decimal(ukwh).scaleb(-6))


class ExecutorIntervalLedger:
    """Every operation opens and closes its own ``IntervalLedger`` on the calling thread.

    The frozen ledger is thread-affine (``sqlite3`` default), so a runtime operation
    must not share one connection across executor jobs and must not use
    ``check_same_thread=False``. Each call therefore performs a short-lived
    open/close on whichever executor thread runs it.
    """

    def __init__(self, path: str | os.PathLike[str], *, busy_timeout_ms: int = 10000) -> None:
        self._path = os.fspath(path)
        self._busy_timeout_ms = busy_timeout_ms

    @property
    def path(self) -> str:
        return self._path

    def _open(self) -> IntervalLedger:
        return IntervalLedger(self._path, busy_timeout_ms=self._busy_timeout_ms)

    def merge(
        self,
        stream_id: str,
        readings: Iterable[IntervalReading],
        *,
        unit: str = DEFAULT_UNIT,
        contract_id: str | None = None,
    ) -> MergeResult:
        with self._open() as ledger:
            return ledger.merge(stream_id, readings, unit=unit, contract_id=contract_id)

    def pending_publication(self, stream_id: str) -> PublicationSnapshot | None:
        with self._open() as ledger:
            return ledger.pending_publication(stream_id)

    def publication_snapshot(self, stream_id: str) -> PublicationSnapshot:
        with self._open() as ledger:
            return ledger.publication_snapshot(stream_id)

    def acknowledge(self, stream_id: str, generation: int) -> bool:
        with self._open() as ledger:
            return ledger.acknowledge(stream_id, generation)

    def read_stream_state(self, stream_id: str) -> LedgerStreamState | None:
        """Narrow read-only view of THIS ledger's own stream flags and interval records.

        Returns ``None`` when the ledger file does not exist yet; the ledger is opened
        first so the frozen ownership marker and exact schema are validated before any
        direct read. No write, no half-hour reconstruction from hourly totals.
        """
        if not os.path.exists(self._path):
            return None
        with self._open():
            pass  # validates ownership marker + exact schema, creates nothing
        connection = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)
        try:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT contract_id, unit, generation, dirty, dirty_generation, "
                "verified_generation FROM stream WHERE stream_id = ?",
                (stream_id,),
            ).fetchone()
            if row is None:
                return None
            intervals = tuple(
                (int(end_ts), int(energy_ukwh))
                for end_ts, energy_ukwh in connection.execute(
                    "SELECT end_ts, energy_ukwh FROM interval_record WHERE stream_id = ? "
                    "ORDER BY end_ts",
                    (stream_id,),
                )
            )
            connection.execute("COMMIT")
        except sqlite3.DatabaseError as error:
            raise LedgerCollaboratorError(
                f"integration ledger {self._path} could not be read: {error}"
            ) from error
        finally:
            connection.close()
        return LedgerStreamState(
            contract_id=row[0],
            unit=row[1],
            generation=int(row[2]),
            dirty=bool(row[3]),
            dirty_generation=int(row[4]),
            verified_generation=int(row[5]),
            intervals=intervals,
        )


class RuntimeOwnerStorage:
    """Async ownership-storage collaborator: one owner record per executor job."""

    def __init__(self, hass: Any, store: DurableOwnerStore) -> None:
        self._hass = hass
        self._store = store

    @property
    def path(self) -> str:
        return self._store.path

    async def async_load(self) -> OwnerRecord | None:
        return await self._hass.async_add_executor_job(self._store.load)

    async def async_save(self, record: OwnerRecord) -> OwnerRecord:
        return await self._hass.async_add_executor_job(self._store.save, record)


class RuntimeLedgerOperations:
    """Async ledger collaborator: every operation is one executor job, opened/closed there."""

    def __init__(self, hass: Any, ledger: ExecutorIntervalLedger) -> None:
        self._hass = hass
        self._ledger = ledger

    @property
    def ledger_exists(self) -> bool:
        return os.path.exists(self._ledger.path)

    async def async_merge(
        self,
        stream_id: str,
        readings: Iterable[IntervalReading],
        *,
        unit: str = DEFAULT_UNIT,
        contract_id: str | None = None,
    ) -> MergeResult:
        return await self._hass.async_add_executor_job(
            _merge_job, self._ledger, stream_id, tuple(readings), unit, contract_id
        )

    async def async_pending_publication(self, stream_id: str) -> PublicationSnapshot | None:
        return await self._hass.async_add_executor_job(self._ledger.pending_publication, stream_id)

    async def async_publication_snapshot(self, stream_id: str) -> PublicationSnapshot:
        return await self._hass.async_add_executor_job(self._ledger.publication_snapshot, stream_id)

    async def async_acknowledge(self, stream_id: str, generation: int) -> bool:
        return await self._hass.async_add_executor_job(
            self._ledger.acknowledge, stream_id, generation
        )

    async def async_read_stream_state(self, stream_id: str) -> LedgerStreamState | None:
        return await self._hass.async_add_executor_job(self._ledger.read_stream_state, stream_id)


def _merge_job(
    ledger: ExecutorIntervalLedger,
    stream_id: str,
    readings: Sequence[IntervalReading],
    unit: str,
    contract_id: str | None,
) -> MergeResult:
    return ledger.merge(stream_id, readings, unit=unit, contract_id=contract_id)


__all__ = [
    "OWNER_RECORD_VERSION",
    "SOURCE_BINDING_CONTRACT",
    "PRODUCT_INTERVAL_KWH",
    "OWNER_RECORD_SUFFIX",
    "LEDGER_SUFFIX",
    "DurableStorageError",
    "OwnerRecordCorruptError",
    "OwnerRecordWriteError",
    "LedgerCollaboratorError",
    "SourceBinding",
    "OwnerRecord",
    "DurableOwnerStore",
    "owner_record_path",
    "ledger_path",
    "LedgerStreamState",
    "half_hour_end_to_hour_start",
    "ExecutorIntervalLedger",
    "RuntimeOwnerStorage",
    "RuntimeLedgerOperations",
]

# ------------------------------------------------ bounded original-CSV admission
# Import ledger above BEFORE the normalizer: the public interval class must be shared.
import asyncio
import csv
from io import StringIO
from . import normalization as _normalization

assert _normalization.IntervalReading is IntervalReading

BOUNDED_ORIGIN = datetime(2026, 3, 29, 1, tzinfo=timezone.utc)


def entry_slug(entry_id: str) -> str:
    """Versioned opaque stable ID, independent of account/credentials."""
    if not isinstance(entry_id, str) or not entry_id:
        raise ValueError("entry id required")
    return hashlib.sha256(("esb-smart-meter-entry/v1:" + entry_id).encode()).hexdigest()


@dataclass(frozen=True)
class BoundedEnergyStatus:
    """Value-free publication and selected coverage, separate from transport."""
    publication_status: str
    reason: str | None
    statistic_id: str
    origin: datetime
    verified_through: datetime | None
    complete: bool = False
    interval_count: int = 0
    gap_count: int = 0
    quarantine_count: int = 0
    pending_partial: bool = False
    counts: tuple[tuple[str, int], ...] = ()
    totals_basis: str = "since 2026-03-29T01:00:00Z"
    origin_crossing_periods_partial: bool = True


@dataclass(frozen=True)
class _CSVSelection:
    readings: tuple[IntervalReading, ...] = ()
    blocked: str | None = None
    complete: bool = False
    interval_count: int = 0
    gap_count: int = 0
    quarantine_count: int = 0
    pending_partial: bool = False
    counts: tuple[tuple[str, int], ...] = ()
    reason: str | None = None


def _select_csv(raw: str, mprn: str, state: LedgerStreamState | None) -> _CSVSelection:
    """Executor-only raw/source validation using exact known half-hours."""
    n = _normalization
    reader = csv.DictReader(StringIO(raw))
    if not reader.fieldnames or reader.fieldnames.count("MPRN") != 1:
        return _CSVSelection(blocked="source_mismatch")
    rows = list(reader)
    if any(not isinstance(row.get("MPRN"), str) or not row["MPRN"].strip()
           or row["MPRN"].strip() != mprn for row in rows):
        return _CSVSelection(blocked="source_mismatch")
    result = n.normalize(raw, identity_columns=("MPRN",), origin=BOUNDED_ORIGIN, strict=True)
    known = {} if state is None else {
        datetime.fromtimestamp(end, timezone.utc): Decimal(f"{value // 1000000}.{value % 1000000:06d}")
        for end, value in state.intervals
    }
    refresh = n.validate_refresh(known, result, origin=BOUNDED_ORIGIN)
    counts = tuple(sorted(result.counts.items()))
    quarantined = len(result.quarantined)
    if refresh.blocked:
        return _CSVSelection(blocked="known_interval_conflict" if refresh.conflicts else "strict_normalization_rejected",
                             quarantine_count=quarantined, counts=counts)
    # Known records retain full coverage even when the supplier download is truncated.
    combined = {end: IntervalReading(end=end, energy_kwh=value) for end, value in known.items()}
    combined.update({r.end: r for r in result.readings if r.end - timedelta(minutes=30) >= BOUNDED_ORIGIN})
    if not combined:
        return _CSVSelection(quarantine_count=quarantined, counts=counts, reason="no_observations")
    last_end = max(combined)
    horizon = last_end.replace(minute=0, second=0, microsecond=0)
    if horizon < last_end:
        horizon += _HOUR
    selection = n.build_selected_batch(combined.values(), origin=BOUNDED_ORIGIN, requested_through=horizon)
    # Only validate_refresh's bounded new admission can enter the durable ledger.
    admissible_ends = {r.end for r in refresh.admissible}
    readings = tuple(r for r in selection.readings if r.end in admissible_ends)
    return _CSVSelection(readings=readings, complete=selection.complete and not quarantined,
        interval_count=selection.interval_count, gap_count=int(selection.blocking_reason == n.BOUNDARY_GAP),
        quarantine_count=quarantined, pending_partial=selection.pending_partial, counts=counts,
        reason="quarantined_intervals" if quarantined else selection.blocking_reason)


class BoundedEnergyRuntime:
    """One serialized raw admission path into the approved durable publisher."""
    def __init__(self, hass: Any, publisher: Any, operations: RuntimeLedgerOperations,
                 *, mprn: str, owner_path: str) -> None:
        self._hass = hass
        self.publisher = publisher
        self.operations = operations
        self.owner_path = owner_path
        self._mprn = mprn
        self._lock = asyncio.Lock()

    def _status(self, publication_status: str, reason: str | None, selection: _CSVSelection) -> BoundedEnergyStatus:
        verified = self.publisher.last_verified_hour
        return BoundedEnergyStatus(publication_status, reason, self.publisher.identity.statistic_id,
            BOUNDED_ORIGIN, verified + _HOUR if verified is not None else None,
            selection.complete, selection.interval_count, selection.gap_count,
            selection.quarantine_count, selection.pending_partial, selection.counts)

    async def async_process_csv(self, raw: str) -> BoundedEnergyStatus:
        from .compatible_publisher import STATUS_READY
        async with self._lock:
            if not self.publisher.admitting:
                return self._status("fenced", "admissions_closed", _CSVSelection())
            try:
                # Source validation MUST precede first binding establishment.
                source = await self._hass.async_add_executor_job(_select_csv, raw, self._mprn, None)
                if not self.publisher.admitting:
                    return self._status("fenced", "admissions_closed", source)
                if source.blocked:
                    return self._status("blocked_source", source.blocked, source)
                initialized = await self.publisher.async_initialize()
                if initialized.status != STATUS_READY:
                    return self._status(initialized.status, initialized.reason, source)
                state = await self.operations.async_read_stream_state(self.publisher.identity.stream_id)
                if not self.publisher.admitting:
                    return self._status("fenced", "admissions_closed", source)
                selection = await self._hass.async_add_executor_job(_select_csv, raw, self._mprn, state)
                if not self.publisher.admitting:
                    return self._status("fenced", "admissions_closed", selection)
                if selection.blocked:
                    return self._status("blocked_source", selection.blocked, selection)
                outcome = (await self.publisher.async_merge_and_publish(selection.readings)
                           if selection.readings else await self.publisher.async_publish_pending())
                return self._status(outcome.status, outcome.reason or selection.reason, selection)
            except asyncio.CancelledError:
                await self.publisher.async_unload()
                raise
            except Exception:
                await self.publisher.async_unload()
                return self._status("fenced", "runtime_failure", _CSVSelection())

    async def async_unload(self) -> None:
        await self.publisher.async_unload()


def create_bounded_runtime(hass: Any, *, entry_id: str, mprn: str,
                           storage_dir: str | os.PathLike[str] | None = None) -> BoundedEnergyRuntime:
    """Real entry binding and default physical owner registry; no adoption override."""
    from .compatible_publisher import CompatiblePublisher, HassExternalStatisticsPort, StreamIdentity, statistic_id_for_slug
    slug = entry_slug(entry_id)
    binding = SourceBinding.synthetic(mprn=mprn)
    directory = Path(storage_dir if storage_dir is not None else hass.config.path(".storage"))
    # HA owns its .storage directory; tests supply an existing disposable directory.
    store = DurableOwnerStore(owner_record_path(directory, slug))
    operations = RuntimeLedgerOperations(hass, ExecutorIntervalLedger(ledger_path(directory, slug)))
    identity = StreamIdentity(slug, slug, statistic_id_for_slug(slug), _normalization.SOURCE_CONTRACT_ID)
    publisher = CompatiblePublisher(identity, ledger=None, port=HassExternalStatisticsPort(hass),
        owner_store=RuntimeOwnerStorage(hass, store), ledger_operations=operations,
        entry_id=entry_id, source_binding=binding, origin=BOUNDED_ORIGIN)
    return BoundedEnergyRuntime(hass, publisher, operations, mprn=mprn.strip(), owner_path=store.path)
