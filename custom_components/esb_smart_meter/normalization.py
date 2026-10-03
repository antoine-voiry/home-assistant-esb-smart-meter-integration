"""Bounded, pure normalizer for the ESB current-account calculated-kWh product.

Status
------
Source-only, offline and **unwired**. This module is not imported by ``__init__.py``,
``coordinator.py``, ``sensor.py``, ``statistics.py`` or the publication adapter, and it
performs no network, credential, Recorder, Home Assistant or filesystem work. It only
turns an already-downloaded text/batch of the *observed* portal product into validated
``IntervalReading`` values plus typed dispositions, proposes a maximal recent
contiguous full-hour suffix, and validates a refresh against a frozen origin.

Scope / authority
-----------------
This is the bounded implementation approved in ``BOUNDED-SOURCE-APPROVAL.md`` for one
source contract:

``esb-current-account-calculated-kwh-dublin-civil-end-unique/v1``

The observed portal product carries a naive ``dd-mm-yyyy HH:MM`` "Read Date and End
Time" label with no offset, fold, sequence or revision field. Per the approval, a label
is interpreted as a **Europe/Dublin civil interval-END** label, and a label is usable
only when exactly one UTC instant round-trips to that civil label. It is a household
quality inference for this observed product only, not a universal ESB guarantee.

Rules implemented here
----------------------
* Strict header/schema/date-grain/unit validation. Import rows must carry the exact
  ``Active Import Interval (kWh)`` type. Recognised export and kW products are excluded
  (never added to import); an unknown import type/unit or non-finite/negative/malformed
  value rejects the whole selected batch atomically.
* For each naive END label, fold 0 and fold 1 are enumerated under ``Europe/Dublin``,
  converted to UTC, and only folds that round-trip to the original civil label (same
  wall clock **and** same UTC offset) are kept. Zero distinct instants -> nonexistent
  (quarantine); two distinct instants -> ambiguous (quarantine, even with equal values);
  one distinct instant -> canonical UTC END. The elapsed 30 minutes are subtracted in
  UTC *after* the unique instant is chosen, never in civil time.
* Exact ``Decimal`` parsing and the frozen ledger's precision/range admissibility are
  reused. The output ``Decimal`` is built from an integer micro-kWh value, so no ambient
  ``decimal`` context (precision or traps) can change it. No float intermediate is used.
* Duplicate occurrences of one unique interval with identical values are no-ops;
  conflicting values for the same unique interval reject the batch atomically. Row
  order is never used.
* The archive classifier quarantines ambiguous/nonexistent rows and excludes rows
  outside an explicit origin for proposal purposes; a selected refresh never skips an
  interior invalid/gap.
* ``propose_maximal_suffix`` returns an explicit UTC hour ``[O, T)`` for the maximal
  recent contiguous run of complete hours plus counts/reasons and a trailing-partial
  flag, without logging energy. ``build_selected_batch`` validates a complete requested
  horizon against a caller-supplied (frozen) origin. ``validate_refresh`` reports
  corrections to known intervals as an explicit conflict report before any merge and
  never rebases/auto-jumps the origin.
* Identity columns (MPRN, meter serial, ...) are ignored for pure processing and are
  captured only for caller binding; identity *values* never appear in errors/reasons.

Input boundary
--------------
``normalize()`` accepts the frozen ledger's own ``IntervalReading`` type (loaded from
``ledger.py``) and emits ``IntervalReading(end=<UTC aware END>, energy_kwh=<Decimal>)``.
``ledger.py`` is imported by file path so that importing this module never triggers the
Home Assistant-importing package ``__init__``. Because the produced readings must be the
*same* class the frozen ledger validates with ``isinstance``, this module reuses an
already-loaded ``ledger.py`` module when one is present in ``sys.modules`` (matched by
resolved file path); see ``NORMALIZER-IMPLEMENTATION.md`` for the residual wiring note.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, InvalidOperation, localcontext
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = timezone.utc
DUBLIN = ZoneInfo("Europe/Dublin")

SOURCE_CONTRACT_ID = "esb-current-account-calculated-kwh-dublin-civil-end-unique/v1"
DEFAULT_UNIT = "kWh"

HEADER_DATE = "Read Date and End Time"
HEADER_VALUE = "Read Value"
HEADER_TYPE = "Read Type"
REQUIRED_COLUMNS = (HEADER_DATE, HEADER_VALUE, HEADER_TYPE)

IMPORT_TYPE_KWH = "Active Import Interval (kWh)"
EXPORT_TYPE_KWH = "Active Export Interval (kWh)"
IMPORT_TYPE_KW = "Active Import Interval (kW)"

HALF_HOUR_SECONDS = 1800
HOUR_SECONDS = 3600
_HALF_HOUR = timedelta(seconds=HALF_HOUR_SECONDS)
_HOUR = timedelta(seconds=HOUR_SECONDS)

_DATE_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{4}) (\d{2}):(\d{2})$")

# Row/aggregate statuses.
STATUS_SELECTED = "selected"
STATUS_EXCLUDED = "excluded"
STATUS_QUARANTINED = "quarantined"
STATUS_REJECTED = "rejected"

# Explicit disposition reasons (stable, value-free strings; never hold identity/value).
REASON_OK = "selected"
REASON_RECOGNIZED_EXPORT = "recognized_export"
REASON_RECOGNIZED_KW_PRODUCT = "recognized_kw_product"
REASON_DUPLICATE_EQUAL = "duplicate_equal_noop"
REASON_OUTSIDE_ORIGIN = "outside_origin"
REASON_AMBIGUOUS = "ambiguous_local_label"
REASON_NONEXISTENT = "nonexistent_local_label"
REASON_AMBIGUOUS_OUTSIDE_ORIGIN = "ambiguous_outside_origin"
REASON_NONEXISTENT_OUTSIDE_ORIGIN = "nonexistent_outside_origin"
REASON_UNKNOWN_IMPORT_TYPE = "unknown_import_type"
REASON_MALFORMED_DATE = "malformed_date"
REASON_WRONG_GRAIN = "wrong_grain"
REASON_UNPARSEABLE_ENERGY = "unparseable_energy"
REASON_NONFINITE_ENERGY = "nonfinite_energy"
REASON_NEGATIVE_ENERGY = "negative_energy"
REASON_ENERGY_OUT_OF_RANGE = "energy_out_of_range"
REASON_CONFLICTING_DUPLICATE = "conflicting_duplicate"
REASON_SCHEMA = "schema_mismatch"
# A non-strict (archive/proposal) result is not a refresh candidate on its own.
REASON_ARCHIVE_PROVENANCE = "archive_requires_strict_revalidation"

# Selection/proposal boundary reasons.
BOUNDARY_PARTIAL_HOUR = "partial_hour"
BOUNDARY_GAP = "gap"

# A fresh context isolates conversion from any caller precision/trap settings.
_CLEAN_CONTEXT = Context(prec=60)


def _load_ledger_module():
    """Load ``ledger.py`` by path, reusing an already-loaded copy of the same file.

    Reusing a module whose resolved ``__file__`` is the same file keeps the
    ``IntervalReading`` class identity stable when a consumer (or the ledger's own test
    suite) already loaded ``ledger.py`` under another name. Loading by path also avoids
    executing ``custom_components/esb_smart_meter/__init__.py`` (which imports HA).
    """
    ledger_path = Path(__file__).with_name("ledger.py").resolve()
    for module in list(sys.modules.values()):
        module_path = getattr(module, "__file__", None)
        if module_path is not None:
            try:
                if Path(module_path).resolve() == ledger_path:
                    return module
            except OSError:  # pragma: no cover - defensive
                continue
    name = "esb_smart_meter_ledger_frozen"
    spec = importlib.util.spec_from_file_location(name, ledger_path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load frozen ledger from {ledger_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_LEDGER = _load_ledger_module()
IntervalReading = _LEDGER.IntervalReading
_energy_to_ukwh = _LEDGER._energy_to_ukwh
_ukwh_to_decimal = _LEDGER._ukwh_to_decimal
_InvalidIntervalError = _LEDGER.InvalidIntervalError


# --------------------------------------------------------------------------- types


@dataclass(frozen=True)
class RowDisposition:
    """One source data row's deterministic classification (no identity values)."""

    index: int
    label: str | None
    status: str
    reason: str
    utc_end: datetime | None = None
    utc_start: datetime | None = None
    energy_kwh: Decimal | None = None


@dataclass(frozen=True)
class Rejection:
    """One fatal reason that rejects the whole selected batch atomically."""

    index: int
    reason: str


@dataclass(frozen=True)
class SourceRows:
    """A parsed CSV header plus body rows (raw cells, header order preserved)."""

    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class NormalizationResult:
    """Typed classification of one source batch against the bounded contract."""

    readings: tuple[IntervalReading, ...]
    dispositions: tuple[RowDisposition, ...]
    rejections: tuple[Rejection, ...]
    identity: Mapping[str, str]
    identity_columns: tuple[str, ...]
    counts: Mapping[str, int]
    schema_ok: bool
    contract_id: str = SOURCE_CONTRACT_ID
    # Provenance: ``True`` for a strictly validated batch, ``False`` for the
    # non-strict archive/proposal classification. A non-strict result is never a
    # refresh candidate until it is freshly revalidated strictly (see
    # ``validate_refresh``); it may contain quarantined fatal rows rather than
    # rejecting, so admitting it at the refresh boundary would bypass strict
    # selection.
    strict: bool = True

    @property
    def rejected(self) -> bool:
        return bool(self.rejections)

    @property
    def quarantined(self) -> tuple[RowDisposition, ...]:
        return tuple(d for d in self.dispositions if d.status == STATUS_QUARANTINED)


@dataclass(frozen=True)
class SourceBinding:
    """Caller-side binding of a normalizer result to a ledger stream identity."""

    stream_id: str
    contract_id: str
    identity: Mapping[str, str]


@dataclass(frozen=True)
class OriginProposal:
    """Maximal recent contiguous complete-hour proposal ``[origin, through)``."""

    origin: datetime | None
    through: datetime | None
    complete_hours: int
    interval_count: int
    trailing_partial: bool
    pending_after: datetime | None
    counts: Mapping[str, int]
    reasons: Mapping[str, int]


@dataclass(frozen=True)
class SelectedBatch:
    """Validated complete requested horizon for one explicit frozen origin."""

    origin: datetime
    requested_through: datetime
    readings: tuple[IntervalReading, ...]
    complete_hours: int
    interval_count: int
    complete: bool
    watermark: datetime
    pending_partial: bool
    blocking_reason: str | None


@dataclass(frozen=True)
class Conflict:
    """A known interval whose incoming value differs; never merged without resolution."""

    end: datetime
    known_kwh: Decimal
    incoming_kwh: Decimal


@dataclass(frozen=True)
class RefreshValidation:
    """Refresh disposition against a frozen origin; the origin is never rewritten."""

    origin: datetime
    admissible: tuple[IntervalReading, ...]
    unchanged: tuple[IntervalReading, ...]
    conflicts: tuple[Conflict, ...]
    quarantined: tuple[RowDisposition, ...]
    held_out_of_origin: int
    extension_stopped_at: datetime | None
    rejected: bool
    reasons: Mapping[str, int]

    @property
    def blocked(self) -> bool:
        return self.rejected or bool(self.conflicts)


# --------------------------------------------------------------------- primitives


def interval_start(end: datetime) -> datetime:
    """Elapsed interval START: the UTC END minus 1800 s (never civil arithmetic)."""
    return _as_utc(end) - _HALF_HOUR


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("expected a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("expected an offset-aware datetime")
    return value.astimezone(UTC)


def _hour_start(end: datetime) -> datetime:
    """UTC hour a half-hour interval belongs to (elapsed 30 min, then floor)."""
    return (end - _HALF_HOUR).replace(minute=0, second=0, microsecond=0)


def _is_hour_boundary(value: datetime) -> bool:
    return (
        value.minute == 0
        and value.second == 0
        and value.microsecond == 0
    )


def _require_hour_origin(value: datetime, label: str) -> datetime:
    aware = _as_utc(value)
    if not _is_hour_boundary(aware):
        raise ValueError(f"{label} must be an exact UTC hour boundary")
    return aware


def resolve_local_end(naive: datetime) -> tuple[datetime, ...]:
    """Enumerate fold 0/1 for a naive Dublin civil label -> distinct valid UTC ENDs.

    A fold is kept only when its UTC instant converts back to the same civil wall clock
    *and* the same UTC offset. Zero distinct instants is a nonexistent label; two is an
    ambiguous label; one is the canonical UTC END.
    """
    if not isinstance(naive, datetime) or naive.tzinfo is not None:
        raise TypeError("resolve_local_end expects a naive datetime")
    candidates: set[datetime] = set()
    for fold in (0, 1):
        aware = naive.replace(tzinfo=DUBLIN, fold=fold)
        utc = aware.astimezone(UTC)
        back = utc.astimezone(DUBLIN)
        if back.replace(tzinfo=None) == naive and back.utcoffset() == aware.utcoffset():
            candidates.add(utc)
    return tuple(sorted(candidates))


def _parse_naive_label(text: str) -> datetime | None:
    if not _DATE_RE.match(text):
        return None
    try:
        return datetime.strptime(text, "%d-%m-%Y %H:%M")
    except ValueError:
        return None


def _parse_energy_kwh(text: str) -> tuple[Decimal | None, str | None]:
    """Exact ``Decimal`` kWh, or a value-free reason. No float intermediate."""
    stripped = text.strip()
    if not stripped:
        return None, REASON_UNPARSEABLE_ENERGY
    with localcontext(_CLEAN_CONTEXT):
        try:
            value = Decimal(stripped)
        except InvalidOperation:
            return None, REASON_UNPARSEABLE_ENERGY
        if not value.is_finite():
            return None, REASON_NONFINITE_ENERGY
        if value < 0:
            return None, REASON_NEGATIVE_ENERGY
        try:
            ukwh = _energy_to_ukwh(value)
        except _InvalidIntervalError:
            return None, REASON_ENERGY_OUT_OF_RANGE
    return _ukwh_to_decimal(ukwh), None


def parse_csv(text: str) -> SourceRows:
    """Parse CSV text into a header plus non-empty body rows (BOM/space tolerated)."""
    reader = csv.reader(io.StringIO(text))
    parsed = [row for row in reader]
    if not parsed:
        return SourceRows((), ())
    header = tuple(cell.strip().lstrip("\ufeff") for cell in parsed[0])
    body = tuple(
        tuple(row)
        for row in parsed[1:]
        if any(cell.strip() for cell in row)
    )
    return SourceRows(header, body)


def _aggregate(dispositions: Iterable[RowDisposition]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for disposition in dispositions:
        counts[disposition.reason] = counts.get(disposition.reason, 0) + 1
    return counts


# ------------------------------------------------------------------- normalizer


def _schema_rejection(message_reason: str = REASON_SCHEMA) -> NormalizationResult:
    return NormalizationResult(
        readings=(),
        dispositions=(),
        rejections=(Rejection(index=-1, reason=message_reason),),
        identity={},
        identity_columns=(),
        counts={message_reason: 1},
        schema_ok=False,
    )


def normalize(
    source: SourceRows | str,
    *,
    identity_columns: Sequence[str] = (),
    origin: datetime | None = None,
    strict: bool = True,
) -> NormalizationResult:
    """Classify one observed-product batch against the bounded source contract.

    ``strict`` (default) rejects the whole batch on a fatal row issue
    (unknown import type/unit, malformed date, wrong grain, non-finite/negative value,
    conflicting duplicate). ``strict=False`` is the archive-scan mode used to build a
    proposal: the same row issues become quarantined boundaries instead of rejections.

    ``identity_columns`` names columns whose values are ignored for processing and only
    captured for caller binding; their values never appear in a reason or rejection.
    ``origin`` (an exact UTC hour boundary) excludes rows whose START is earlier.
    """
    if isinstance(source, str):
        rows = parse_csv(source)
    elif isinstance(source, SourceRows):
        rows = source
    else:
        raise TypeError("source must be CSV text or SourceRows")

    identity_cols = tuple(identity_columns)
    if not rows.header:
        return _schema_rejection()

    header = list(rows.header)
    if len(set(header)) != len(header):
        return _schema_rejection()
    for column in REQUIRED_COLUMNS:
        if column not in header:
            return _schema_rejection()
    column_index = {name: header.index(name) for name in header}
    for column in identity_cols:
        if column not in column_index:
            return _schema_rejection()

    origin_utc = _require_hour_origin(origin, "origin") if origin is not None else None
    origin_local = (
        origin_utc.astimezone(DUBLIN).replace(tzinfo=None) if origin_utc is not None else None
    )

    dispositions: list[RowDisposition] = []
    rejections: list[Rejection] = []
    seen: dict[datetime, Decimal] = {}
    selected_index: dict[datetime, int] = {}
    conflicted: set[datetime] = set()
    identity: dict[str, str] = {}

    date_idx = column_index[HEADER_DATE]
    value_idx = column_index[HEADER_VALUE]
    type_idx = column_index[HEADER_TYPE]

    def cell(row: Sequence[str], index: int) -> str:
        return row[index] if index < len(row) else ""

    for index, row in enumerate(rows.rows):
        if index == 0:
            for column in identity_cols:
                identity[column] = cell(row, column_index[column])

        raw_label = cell(row, date_idx).strip()
        raw_type = cell(row, type_idx).strip()

        if raw_type == EXPORT_TYPE_KWH:
            dispositions.append(
                RowDisposition(index, raw_label or None, STATUS_EXCLUDED, REASON_RECOGNIZED_EXPORT)
            )
            continue
        if raw_type == IMPORT_TYPE_KW:
            dispositions.append(
                RowDisposition(
                    index, raw_label or None, STATUS_EXCLUDED, REASON_RECOGNIZED_KW_PRODUCT
                )
            )
            continue
        if raw_type != IMPORT_TYPE_KWH:
            _fatal(
                dispositions,
                rejections,
                index,
                raw_label or None,
                REASON_UNKNOWN_IMPORT_TYPE,
                strict,
            )
            continue

        naive = _parse_naive_label(raw_label)
        if naive is None:
            _fatal(
                dispositions,
                rejections,
                index,
                raw_label or None,
                REASON_MALFORMED_DATE,
                strict,
            )
            continue
        if naive.minute not in (0, 30):
            _fatal(
                dispositions,
                rejections,
                index,
                raw_label,
                REASON_WRONG_GRAIN,
                strict,
            )
            continue

        candidates = resolve_local_end(naive)
        if len(candidates) == 0:
            if origin_local is not None and naive < origin_local:
                status, reason = STATUS_EXCLUDED, REASON_NONEXISTENT_OUTSIDE_ORIGIN
            else:
                status, reason = STATUS_QUARANTINED, REASON_NONEXISTENT
            dispositions.append(RowDisposition(index, raw_label, status, reason))
            continue
        if len(candidates) == 2:
            if origin_local is not None and naive < origin_local:
                status, reason = STATUS_EXCLUDED, REASON_AMBIGUOUS_OUTSIDE_ORIGIN
            else:
                status, reason = STATUS_QUARANTINED, REASON_AMBIGUOUS
            dispositions.append(RowDisposition(index, raw_label, status, reason))
            continue

        utc_end = candidates[0]
        start = utc_end - _HALF_HOUR
        energy, energy_reason = _parse_energy_kwh(cell(row, value_idx))
        if energy is None:
            _fatal(dispositions, rejections, index, raw_label, energy_reason, strict)
            continue

        if origin_utc is not None and start < origin_utc:
            dispositions.append(
                RowDisposition(
                    index,
                    raw_label,
                    STATUS_EXCLUDED,
                    REASON_OUTSIDE_ORIGIN,
                    utc_end=utc_end,
                    utc_start=start,
                    energy_kwh=energy,
                )
            )
            continue

        if utc_end in conflicted:
            # This identity already carried conflicting values; no occurrence of it
            # may ever become a usable reading.
            _fatal(
                dispositions,
                rejections,
                index,
                raw_label,
                REASON_CONFLICTING_DUPLICATE,
                strict,
                utc_end=utc_end,
                utc_start=start,
                energy=energy,
            )
            continue

        existing = seen.get(utc_end)
        if existing is not None and existing != energy:
            # Conflicting values for one unique interval: no row order may win. Drop
            # the earlier value so it can never be a usable reading, remember the
            # identity so no later occurrence is selected, and quarantine both.
            conflicted.add(utc_end)
            _unselect_interval(dispositions, selected_index, seen, utc_end)
            _fatal(
                dispositions,
                rejections,
                index,
                raw_label,
                REASON_CONFLICTING_DUPLICATE,
                strict,
                utc_end=utc_end,
                utc_start=start,
                energy=energy,
            )
            continue
        if existing is not None:
            dispositions.append(
                RowDisposition(
                    index,
                    raw_label,
                    STATUS_EXCLUDED,
                    REASON_DUPLICATE_EQUAL,
                    utc_end=utc_end,
                    utc_start=start,
                    energy_kwh=energy,
                )
            )
            continue

        seen[utc_end] = energy
        dispositions.append(
            RowDisposition(
                index,
                raw_label,
                STATUS_SELECTED,
                REASON_OK,
                utc_end=utc_end,
                utc_start=start,
                energy_kwh=energy,
            )
        )
        selected_index[utc_end] = len(dispositions) - 1

    rejected = bool(rejections)
    selected_readings = tuple(
        sorted((_make_reading(end, energy) for end, energy in seen.items()), key=lambda r: r.end)
    )
    return NormalizationResult(
        readings=() if rejected else selected_readings,
        dispositions=tuple(dispositions),
        rejections=tuple(rejections),
        identity=identity,
        identity_columns=identity_cols,
        counts=_aggregate(dispositions),
        schema_ok=True,
        strict=strict,
    )


def _fatal(
    dispositions: list[RowDisposition],
    rejections: list[Rejection],
    index: int,
    label: str | None,
    reason: str | None,
    strict: bool,
    *,
    utc_end: datetime | None = None,
    utc_start: datetime | None = None,
    energy: Decimal | None = None,
) -> None:
    assert reason is not None
    if strict:
        rejections.append(Rejection(index=index, reason=reason))
        dispositions.append(
            RowDisposition(index, label, STATUS_REJECTED, reason, utc_end, utc_start, energy)
        )
    else:
        dispositions.append(
            RowDisposition(index, label, STATUS_QUARANTINED, reason, utc_end, utc_start, energy)
        )


def _unselect_interval(
    dispositions: list[RowDisposition],
    selected_index: dict[datetime, int],
    seen: dict[datetime, Decimal],
    utc_end: datetime,
) -> None:
    """Drop an already-selected interval so a later conflict leaves no winner.

    The prior row's disposition is rewritten to an explicit quarantine so the archive
    classification stays truthful instead of reporting a dropped interval as selected.
    """
    seen.pop(utc_end, None)
    index = selected_index.pop(utc_end, None)
    if index is None:  # pragma: no cover - the reading was always indexed on select
        return
    prior = dispositions[index]
    dispositions[index] = RowDisposition(
        prior.index,
        prior.label,
        STATUS_QUARANTINED,
        REASON_CONFLICTING_DUPLICATE,
        utc_end=prior.utc_end,
        utc_start=prior.utc_start,
        energy_kwh=prior.energy_kwh,
    )


def _make_reading(end: datetime, energy: Decimal) -> IntervalReading:
    """Build the frozen ledger's IntervalReading (validated by construction)."""
    return IntervalReading(end=end, energy_kwh=energy)


def raise_if_rejected(result: NormalizationResult) -> None:
    """Convenience: raise ``NormalizationRejectedError`` when the batch is rejected."""
    if result.rejected:
        raise NormalizationRejectedError(result.rejections)


class NormalizationRejectedError(Exception):
    """The selected batch was rejected atomically; no partial selection is usable."""

    def __init__(self, rejections: Sequence[Rejection]) -> None:
        super().__init__(f"selected batch rejected: {len(rejections)} fatal row(s)")
        self.rejections = tuple(rejections)


def bind_source(result: NormalizationResult, stream_id: str) -> SourceBinding:
    """Bind a result to a ledger stream identity without leaking identity in errors."""
    return SourceBinding(
        stream_id=stream_id,
        contract_id=result.contract_id,
        identity=dict(result.identity),
    )


# ------------------------------------------------------- proposal / selection


def _hour_counts(ends: Iterable[datetime]) -> dict[datetime, int]:
    counts: dict[datetime, int] = {}
    for end in ends:
        hour = _hour_start(end)
        counts[hour] = counts.get(hour, 0) + 1
    return counts


def _first_incomplete(
    counts: Mapping[datetime, int],
    origin: datetime,
    through: datetime | None,
) -> tuple[datetime, str] | None:
    """First hour at/after ``origin`` that is not complete, or ``None`` if all complete."""
    if through is not None:
        ceiling = through
    elif counts:
        ceiling = max(counts) + _HOUR
    else:
        return None
    hour = origin
    while hour < ceiling:
        count = counts.get(hour, 0)
        if count != 2:
            return hour, (BOUNDARY_PARTIAL_HOUR if count else BOUNDARY_GAP)
        hour += _HOUR
    return None


def propose_maximal_suffix(
    readings: Iterable[IntervalReading],
    *,
    counts: Mapping[str, int] | None = None,
    reasons: Mapping[str, int] | None = None,
) -> OriginProposal:
    """Maximal recent contiguous run of complete hours, without logging energy."""
    ends = sorted({_as_utc(r.end) for r in readings})
    base_counts = dict(counts or {})
    base_reasons = dict(reasons or {})
    if not ends:
        return OriginProposal(None, None, 0, 0, False, None, base_counts, base_reasons)

    hour_counts = _hour_counts(ends)
    complete = {hour for hour, count in hour_counts.items() if count == 2}
    if not complete:
        return OriginProposal(None, None, 0, 0, True, ends[-1], base_counts, base_reasons)

    latest = max(complete)
    first = latest
    while (first - _HOUR) in complete:
        first -= _HOUR
    origin = first
    through = latest + _HOUR
    interval_count = sum(1 for end in ends if origin + _HALF_HOUR <= end <= through)
    pending_after = ends[-1] if ends[-1] > through else None
    return OriginProposal(
        origin=origin,
        through=through,
        complete_hours=int((through - origin).total_seconds() // HOUR_SECONDS),
        interval_count=interval_count,
        trailing_partial=pending_after is not None,
        pending_after=pending_after,
        counts=base_counts,
        reasons=base_reasons,
    )


def build_selected_batch(
    readings: Iterable[IntervalReading],
    *,
    origin: datetime,
    requested_through: datetime,
) -> SelectedBatch:
    """Validate a complete requested horizon for one explicit, frozen origin.

    The returned ``readings`` are the contiguous complete prefix only: a trailing
    partial hour or a post-gap hour is reported via ``watermark``/``blocking_reason``
    and is never included as merge-ready data.
    """
    origin_utc = _require_hour_origin(origin, "origin")
    through_utc = _require_hour_origin(requested_through, "requested_through")
    if through_utc <= origin_utc:
        raise ValueError("requested_through must be after origin")

    selected = tuple(
        sorted(
            (
                r
                for r in readings
                if origin_utc <= _as_utc(r.end) - _HALF_HOUR and _as_utc(r.end) <= through_utc
            ),
            key=lambda r: _as_utc(r.end),
        )
    )
    ends = {_as_utc(r.end) for r in selected}
    hour_counts = _hour_counts(ends)
    boundary = _first_incomplete(hour_counts, origin_utc, through_utc)
    if boundary is None:
        watermark = through_utc
        complete = True
        blocking: str | None = None
    else:
        watermark = boundary[0]
        complete = False
        blocking = boundary[1]

    # Enforce the stop at the boundary, not merely report it: only the complete prefix
    # (END <= watermark) is selected output.
    visible = tuple(r for r in selected if _as_utc(r.end) <= watermark)
    complete_hours = sum(
        1
        for hour in hour_counts
        if origin_utc <= hour < watermark and hour_counts[hour] == 2
    )
    return SelectedBatch(
        origin=origin_utc,
        requested_through=through_utc,
        readings=visible,
        complete_hours=complete_hours,
        interval_count=len(visible),
        complete=complete,
        watermark=watermark,
        pending_partial=blocking == BOUNDARY_PARTIAL_HOUR,
        blocking_reason=blocking,
    )


# ----------------------------------------------------------------- refresh


def _known_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, str, float)):
        raise TypeError("known interval values must be Decimal/int/str/float")
    return Decimal(str(value).strip())


def validate_refresh(
    known: Mapping[datetime, object],
    result: NormalizationResult,
    *,
    origin: datetime,
) -> RefreshValidation:
    """Validate incoming data against known values under a frozen origin.

    Corrections to a known interval are reported as explicit conflicts and are never
    merged (no last-arrived-wins); unchanged values are stable no-ops and genuinely new
    unique intervals are admissible. A new gap stops extension at the first
    incomplete hour; the origin is never rebased or auto-jumped.

    Only the contiguous complete prefix is admissible: new intervals with END beyond the
    verified watermark (trailing partial or post-gap) are pending, not merge-ready. A
    non-strict (archive/proposal) result is rejected outright — it must be freshly
    revalidated strictly before it can cross this boundary.
    """
    origin_utc = _require_hour_origin(origin, "origin")
    reasons = dict(result.counts)
    if result.rejected:
        return RefreshValidation(
            origin=origin_utc,
            admissible=(),
            unchanged=(),
            conflicts=(),
            quarantined=result.quarantined,
            held_out_of_origin=0,
            extension_stopped_at=None,
            rejected=True,
            reasons=reasons,
        )
    if not result.strict:
        # Archive provenance is not a refresh candidate: it may have quarantined fatal
        # rows or dropped conflicting intervals instead of rejecting, so admitting it
        # here would bypass strict selection.
        reasons[REASON_ARCHIVE_PROVENANCE] = reasons.get(REASON_ARCHIVE_PROVENANCE, 0) + 1
        return RefreshValidation(
            origin=origin_utc,
            admissible=(),
            unchanged=(),
            conflicts=(),
            quarantined=result.quarantined,
            held_out_of_origin=0,
            extension_stopped_at=None,
            rejected=True,
            reasons=reasons,
        )

    known_map = {_as_utc(end): _known_decimal(value) for end, value in known.items()}

    admissible: list[IntervalReading] = []
    unchanged: list[IntervalReading] = []
    conflicts: list[Conflict] = []
    held = 0
    for reading in sorted(result.readings, key=lambda r: _as_utc(r.end)):
        end = _as_utc(reading.end)
        if end - _HALF_HOUR < origin_utc:
            held += 1
            continue
        known_value = known_map.get(end)
        if known_value is None:
            admissible.append(reading)
        elif known_value == reading.energy_kwh:
            unchanged.append(reading)
        else:
            conflicts.append(
                Conflict(end=end, known_kwh=known_value, incoming_kwh=reading.energy_kwh)
            )

    ends = set(known_map) | {_as_utc(r.end) for r in admissible}
    boundary = _first_incomplete(_hour_counts(ends), origin_utc, None)
    stop = boundary[0] if boundary is not None else None
    # Enforce the stop: post-gap / trailing partial new intervals are pending, not
    # admissible, so nothing exceeds the verified watermark.
    admissible_visible = (
        tuple(r for r in admissible if _as_utc(r.end) <= stop)
        if stop is not None
        else tuple(admissible)
    )
    return RefreshValidation(
        origin=origin_utc,
        admissible=admissible_visible,
        unchanged=tuple(unchanged),
        conflicts=tuple(conflicts),
        quarantined=result.quarantined,
        held_out_of_origin=held,
        extension_stopped_at=stop,
        rejected=False,
        reasons=reasons,
    )


__all__ = [
    "SOURCE_CONTRACT_ID",
    "DEFAULT_UNIT",
    "HEADER_DATE",
    "HEADER_VALUE",
    "HEADER_TYPE",
    "IMPORT_TYPE_KWH",
    "EXPORT_TYPE_KWH",
    "IMPORT_TYPE_KW",
    "STATUS_SELECTED",
    "STATUS_EXCLUDED",
    "STATUS_QUARANTINED",
    "STATUS_REJECTED",
    "REASON_OK",
    "REASON_RECOGNIZED_EXPORT",
    "REASON_RECOGNIZED_KW_PRODUCT",
    "REASON_DUPLICATE_EQUAL",
    "REASON_OUTSIDE_ORIGIN",
    "REASON_AMBIGUOUS",
    "REASON_NONEXISTENT",
    "REASON_AMBIGUOUS_OUTSIDE_ORIGIN",
    "REASON_NONEXISTENT_OUTSIDE_ORIGIN",
    "REASON_UNKNOWN_IMPORT_TYPE",
    "REASON_MALFORMED_DATE",
    "REASON_WRONG_GRAIN",
    "REASON_UNPARSEABLE_ENERGY",
    "REASON_NONFINITE_ENERGY",
    "REASON_NEGATIVE_ENERGY",
    "REASON_ENERGY_OUT_OF_RANGE",
    "REASON_CONFLICTING_DUPLICATE",
    "REASON_SCHEMA",
    "REASON_ARCHIVE_PROVENANCE",
    "BOUNDARY_PARTIAL_HOUR",
    "BOUNDARY_GAP",
    "IntervalReading",
    "RowDisposition",
    "Rejection",
    "SourceRows",
    "NormalizationResult",
    "SourceBinding",
    "OriginProposal",
    "SelectedBatch",
    "Conflict",
    "RefreshValidation",
    "NormalizationRejectedError",
    "parse_csv",
    "normalize",
    "resolve_local_end",
    "interval_start",
    "raise_if_rejected",
    "bind_source",
    "propose_maximal_suffix",
    "build_selected_batch",
    "validate_refresh",
]
