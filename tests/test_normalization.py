"""Focused synthetic tests for the bounded pure source normalizer.

Import boundary
---------------
``normalization.py`` must stay stdlib-only and must not import Home Assistant, so these
tests load it (and ``ledger.py``) directly by file path, the same way
``test_import_ledger.py`` does, instead of through ``custom_components.esb_smart_meter``
whose ``__init__`` imports HA. The ledger is loaded first so the normalizer reuses that
exact ``IntervalReading`` class (class identity is asserted below).

Every date/value here is synthetic. The frozen origin ``O`` and the Dublin transition
dates are the publicly recorded ones from the approval document, but no household value,
account/meter identity or download is used.
"""

from __future__ import annotations

import ast
import csv
import decimal
import importlib.util
import io
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

UTC = timezone.utc
DUBLIN = ZoneInfo("Europe/Dublin")
REPO = Path(__file__).resolve().parents[1]
LEDGER_PATH = REPO / "custom_components" / "esb_smart_meter" / "ledger.py"
NORMALIZATION_PATH = REPO / "custom_components" / "esb_smart_meter" / "normalization.py"

SOURCE_HEADER = "Read Date and End Time,Read Value,Read Type,MPRN,Meter Serial Number"
IMPORT = "Active Import Interval (kWh)"
EXPORT = "Active Export Interval (kWh)"
KW = "Active Import Interval (kW)"

# Frozen isolated origin for the selected source contract.
O = datetime(2026, 3, 29, 1, 0, tzinfo=UTC)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def led():
    """Reuse the first exact-file ledger, matching the pure normalizer loader."""
    for module in tuple(sys.modules.values()):
        filename = getattr(module, "__file__", None)
        if filename and Path(filename).resolve() == LEDGER_PATH.resolve():
            return module
    return _load("esb_smart_meter_ledger_under_test", LEDGER_PATH)


@pytest.fixture(scope="module")
def mod(led):
    """Ledger first, then the normalizer so the IntervalReading class is shared."""
    return _load("esb_smart_meter_normalization_under_test", NORMALIZATION_PATH)


def rows(*triples: tuple[str, str, str]) -> str:
    """Build CSV text from (date, value, type) triples with a fixed identity pair."""
    lines = [SOURCE_HEADER]
    for date, value, read_type in triples:
        lines.append(f"{date},{value},{read_type},99999999999,SN-SYNTHETIC")
    return "\n".join(lines)


def only(module, text, **kwargs):
    result = module.normalize(text, **kwargs)
    assert not result.rejected, [r.reason for r in result.rejections]
    return result


# --------------------------------------------------------------- local -> UTC


def test_unique_winter_summer_midnight_labels(mod):
    result = only(
        mod,
        rows(
            ("15-01-2026 01:00", "1.5", IMPORT),   # winter GMT == UTC
            ("15-07-2026 01:00", "2.5", IMPORT),   # summer IST +01
            ("15-06-2026 00:00", "3.5", IMPORT),   # midnight IST -> previous UTC day
        ),
    )
    ends = {r.end: r.energy_kwh for r in result.readings}
    assert ends == {
        datetime(2026, 1, 15, 1, 0, tzinfo=UTC): Decimal("1.5"),
        datetime(2026, 7, 15, 0, 0, tzinfo=UTC): Decimal("2.5"),
        datetime(2026, 6, 14, 23, 0, tzinfo=UTC): Decimal("3.5"),
    }
    assert all(r.end.utcoffset() == timedelta(0) for r in result.readings)


def test_spring_nonexistent_0130_is_quarantined(mod):
    result = mod.normalize(rows(("29-03-2026 01:30", "1.0", IMPORT)))
    assert not result.rejected
    assert result.readings == ()
    assert result.counts.get(mod.REASON_NONEXISTENT) == 1
    assert result.dispositions[0].status == mod.STATUS_QUARANTINED


def test_spring_0200_to_0100z_with_elapsed_start(mod):
    result = only(mod, rows(("29-03-2026 02:00", "0.5", IMPORT)))
    reading = result.readings[0]
    assert reading.end == datetime(2026, 3, 29, 1, 0, tzinfo=UTC)
    # Elapsed 30 minutes subtracted in UTC, never civil (which would invent 01:30).
    assert mod.interval_start(reading.end) == datetime(2026, 3, 29, 0, 30, tzinfo=UTC)
    assert result.dispositions[0].utc_start == datetime(2026, 3, 29, 0, 30, tzinfo=UTC)


def test_autumn_0130_ambiguous_even_equal_duplicates(mod):
    result = mod.normalize(
        rows(
            ("25-10-2026 01:30", "4.0", IMPORT),
            ("25-10-2026 01:30", "4.0", IMPORT),  # equal values do NOT disambiguate
        )
    )
    assert not result.rejected
    assert result.readings == ()
    assert result.counts.get(mod.REASON_AMBIGUOUS) == 2
    assert all(d.status == mod.STATUS_QUARANTINED for d in result.dispositions)


def test_ha_display_timezone_irrelevant(mod):
    text = rows(("29-03-2026 02:00", "0.5", IMPORT), ("15-07-2026 01:00", "2.0", IMPORT))
    baseline = [(r.end, r.energy_kwh) for r in only(mod, text).readings]
    old = os.environ.get("TZ")
    try:
        for zone in ("America/New_York", "Asia/Tokyo", "Australia/Sydney"):
            os.environ["TZ"] = zone
            time.tzset()
            assert [(r.end, r.energy_kwh) for r in only(mod, text).readings] == baseline
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def test_shuffled_inputs_deterministic(mod):
    triples = [
        ("15-01-2026 00:30", "1.0", IMPORT),
        ("15-01-2026 01:00", "1.25", IMPORT),
        ("29-03-2026 02:00", "0.5", IMPORT),
        ("25-10-2026 01:30", "9.0", IMPORT),
        ("01-07-2026 00:30", "2.0", KW),
        ("01-07-2026 00:30", "4.0", EXPORT),
    ]
    shuffled = list(triples)
    random.Random(1234).shuffle(shuffled)
    a = mod.normalize(rows(*triples))
    b = mod.normalize(rows(*shuffled))
    assert [(r.end, r.energy_kwh) for r in a.readings] == [
        (r.end, r.energy_kwh) for r in b.readings
    ]
    assert dict(a.counts) == dict(b.counts)


# ----------------------------------------------------------------- decimals


def test_decimal_ambient_precision_and_traps(mod):
    text = rows(("15-01-2026 01:00", "1.234567", IMPORT))
    with decimal.localcontext() as ctx:
        ctx.prec = 2
        ctx.traps[decimal.Inexact] = True
        ctx.traps[decimal.Rounded] = True
        result = only(mod, text)
    assert result.readings[0].energy_kwh == Decimal("1.234567")
    assert isinstance(result.readings[0].energy_kwh, Decimal)


def test_precision_uses_ledger_rounding(mod):
    # 7 dp rounds half-up to the frozen ledger's 6 dp micro-kWh policy.
    result = only(mod, rows(("15-01-2026 01:00", "0.0000005", IMPORT)))
    assert result.readings[0].energy_kwh == Decimal("0.000001")


@pytest.mark.parametrize(
    ("value", "read_type", "reason_attr"),
    [
        ("NaN", IMPORT, "REASON_NONFINITE_ENERGY"),
        ("Infinity", IMPORT, "REASON_NONFINITE_ENERGY"),
        ("-0.5", IMPORT, "REASON_NEGATIVE_ENERGY"),
        ("not-a-number", IMPORT, "REASON_UNPARSEABLE_ENERGY"),
        ("1.0", "Active Import Interval (MWh)", "REASON_UNKNOWN_IMPORT_TYPE"),
    ],
)
def test_fatal_values_and_unknown_units_reject_batch(mod, value, read_type, reason_attr):
    result = mod.normalize(rows(("15-01-2026 01:00", value, read_type)))
    assert result.rejected
    assert result.readings == ()
    assert getattr(mod, reason_attr) in [r.reason for r in result.rejections]
    mod.raise_if_rejected  # attribute exists
    with pytest.raises(mod.NormalizationRejectedError):
        mod.raise_if_rejected(result)


def test_schema_drift_rejects(mod):
    missing = "Read Date and End Time,Read Value\n15-01-2026 01:00,1.0"
    result = mod.normalize(missing)
    assert result.rejected
    assert result.schema_ok is False
    assert result.rejections[0].reason == mod.REASON_SCHEMA


def test_wrong_grain_rejected(mod):
    result = mod.normalize(rows(("15-01-2026 01:15", "1.0", IMPORT)))
    assert result.rejected
    assert result.rejections[0].reason == mod.REASON_WRONG_GRAIN


def test_malformed_date_rejected(mod):
    for label in ("15/01/2026 01:00", "31-02-2026 01:00", "2026-01-15 01:00"):
        result = mod.normalize(rows((label, "1.0", IMPORT)))
        assert result.rejected, label
        assert result.rejections[0].reason == mod.REASON_MALFORMED_DATE


def test_duplicate_conflict_rejects_atomically(mod):
    result = mod.normalize(
        rows(
            ("15-01-2026 01:00", "1.0", IMPORT),
            ("15-01-2026 01:00", "2.0", IMPORT),
        )
    )
    assert result.rejected
    assert result.readings == ()
    assert mod.REASON_CONFLICTING_DUPLICATE in [r.reason for r in result.rejections]


def test_duplicate_equal_is_noop(mod):
    result = only(
        mod,
        rows(
            ("15-01-2026 01:00", "1.0", IMPORT),
            ("15-01-2026 01:00", "1.0", IMPORT),
        ),
    )
    assert len(result.readings) == 1
    assert result.counts.get(mod.REASON_DUPLICATE_EQUAL) == 1


def test_recognized_export_and_kw_excluded_not_added(mod):
    result = mod.normalize(
        rows(
            ("01-07-2026 00:30", "4.0", EXPORT),
            ("01-07-2026 00:30", "2.0", KW),
        )
    )
    assert not result.rejected
    assert result.readings == ()
    assert result.counts.get(mod.REASON_RECOGNIZED_EXPORT) == 1
    assert result.counts.get(mod.REASON_RECOGNIZED_KW_PRODUCT) == 1


# ------------------------------------------------------------------- origin


def test_explicit_hour_origin_selection(mod):
    result = only(
        mod,
        rows(
            ("29-03-2026 02:00", "1.0", IMPORT),  # END 01:00Z, START 00:30Z < O -> excluded
            ("29-03-2026 02:30", "1.0", IMPORT),  # END 01:30Z, START 01:00Z == O -> selected
            ("29-03-2026 03:00", "1.0", IMPORT),  # END 02:00Z -> selected
        ),
        origin=O,
    )
    assert [r.end for r in result.readings] == [
        datetime(2026, 3, 29, 1, 30, tzinfo=UTC),
        datetime(2026, 3, 29, 2, 0, tzinfo=UTC),
    ]
    assert result.counts.get(mod.REASON_OUTSIDE_ORIGIN) == 1
    assert all(r.end - timedelta(seconds=1800) >= O for r in result.readings)


def test_origin_must_be_exact_hour_boundary(mod):
    with pytest.raises(ValueError):
        mod.normalize(rows(("15-01-2026 01:00", "1.0", IMPORT)), origin=O + timedelta(minutes=15))


def test_ambiguous_archive_outside_origin_excluded_from_selection(mod):
    result = mod.normalize(
        rows(
            ("26-10-2025 01:30", "1.0", IMPORT),  # earlier autumn (26 Oct), ambiguous, outside O
            ("29-03-2026 02:30", "1.0", IMPORT),  # inside O
        ),
        origin=O,
    )
    assert not result.rejected
    assert result.readings and result.readings[0].end == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)
    assert result.counts.get(mod.REASON_AMBIGUOUS_OUTSIDE_ORIGIN) == 1
    assert result.counts.get(mod.REASON_AMBIGUOUS, 0) == 0
    assert all(r.end >= O + timedelta(seconds=1800) for r in result.readings)


# -------------------------------------------------------- proposal / batches


def _readings(mod, ends):
    return tuple(mod.IntervalReading(end=e, energy_kwh=Decimal("1.0")) for e in ends)


H0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


def test_proposal_is_maximal_recent_contiguous_suffix(mod):
    ends = [
        H0 + timedelta(minutes=30), H0 + timedelta(hours=1),
        H0 + timedelta(hours=1, minutes=30), H0 + timedelta(hours=2),
        H0 + timedelta(hours=3, minutes=30), H0 + timedelta(hours=4),
    ]
    proposal = mod.propose_maximal_suffix(_readings(mod, ends))
    assert proposal.origin == H0 + timedelta(hours=3)
    assert proposal.through == H0 + timedelta(hours=4)
    assert proposal.complete_hours == 1
    assert proposal.interval_count == 2
    assert proposal.trailing_partial is False


def test_gap_splits_proposal_but_never_selected_complete(mod):
    ends = [
        H0 + timedelta(minutes=30), H0 + timedelta(hours=1),
        H0 + timedelta(hours=1, minutes=30), H0 + timedelta(hours=2),
        # gap: hour 02:00 missing
        H0 + timedelta(hours=3, minutes=30), H0 + timedelta(hours=4),
    ]
    proposal = mod.propose_maximal_suffix(_readings(mod, ends))
    assert proposal.origin == H0 + timedelta(hours=3)
    assert proposal.through == H0 + timedelta(hours=4)

    batch = mod.build_selected_batch(
        _readings(mod, ends), origin=H0, requested_through=H0 + timedelta(hours=4)
    )
    assert batch.complete is False
    assert batch.watermark == H0 + timedelta(hours=2)
    assert batch.blocking_reason == mod.BOUNDARY_GAP
    assert batch.pending_partial is False
    # Selected output is clipped to the verified complete prefix; the post-gap hour
    # is pending, not merge-ready.
    assert {r.end for r in batch.readings} == {
        H0 + timedelta(minutes=30),
        H0 + timedelta(hours=1),
        H0 + timedelta(hours=1, minutes=30),
        H0 + timedelta(hours=2),
    }
    assert batch.interval_count == 4
    assert all(r.end <= batch.watermark for r in batch.readings)


def test_partial_first_and_trailing_pending(mod):
    ends = [
        H0 + timedelta(minutes=30),                                        # hour 00 partial
        H0 + timedelta(hours=1, minutes=30), H0 + timedelta(hours=2),      # hour 01 complete
        H0 + timedelta(hours=3, minutes=30),                               # hour 03 partial trailing
    ]
    proposal = mod.propose_maximal_suffix(_readings(mod, ends))
    assert proposal.origin == H0 + timedelta(hours=1)
    assert proposal.through == H0 + timedelta(hours=2)
    assert proposal.trailing_partial is True
    assert proposal.pending_after == H0 + timedelta(hours=3, minutes=30)

    batch = mod.build_selected_batch(
        _readings(mod, ends), origin=H0, requested_through=H0 + timedelta(hours=4)
    )
    assert batch.complete is False
    assert batch.watermark == H0
    assert batch.blocking_reason == mod.BOUNDARY_PARTIAL_HOUR
    assert batch.pending_partial is True
    # A partial first hour means the complete prefix is empty; the trailing half-hours
    # must not leak into the selected output.
    assert batch.readings == ()
    assert batch.interval_count == 0
    assert all(r.end <= batch.watermark for r in batch.readings)


def test_complete_horizon_has_all_hours(mod):
    ends = []
    for hour in range(4):
        ends.append(H0 + timedelta(hours=hour, minutes=30))
        ends.append(H0 + timedelta(hours=hour + 1))
    batch = mod.build_selected_batch(
        _readings(mod, ends), origin=H0, requested_through=H0 + timedelta(hours=4)
    )
    assert batch.complete is True
    assert batch.watermark == H0 + timedelta(hours=4)
    assert batch.complete_hours == 4
    assert batch.interval_count == 8
    assert batch.blocking_reason is None


# ------------------------------------------------------------------ refresh


def test_frozen_origin_new_gap_stops_not_resets(mod):
    origin = H0
    known = {}
    for hour in range(3):
        known[H0 + timedelta(hours=hour, minutes=30)] = Decimal("1.0")
        known[H0 + timedelta(hours=hour + 1)] = Decimal("1.0")
    result = only(
        mod,
        rows(
            ("01-01-2026 04:30", "1.0", IMPORT),  # END 04:30Z -> hour 04
            ("01-01-2026 05:00", "1.0", IMPORT),  # END 05:00Z -> hour 04
        ),
    )
    validation = mod.validate_refresh(known, result, origin=origin)
    assert validation.origin == origin            # never rebased
    assert validation.extension_stopped_at == H0 + timedelta(hours=3)  # gap, not a jump
    # Post-gap readings are not admissible: only the contiguous complete prefix is.
    assert validation.admissible == ()
    assert validation.blocked is False


def test_same_known_value_stable_conflicting_quarantined(mod):
    origin = H0
    a = H0 + timedelta(minutes=30)
    b = H0 + timedelta(hours=1)
    known = {a: Decimal("1.0"), b: Decimal("2.0")}
    result = only(
        mod,
        rows(
            ("01-01-2026 00:30", "1.0", IMPORT),  # equal -> unchanged
            ("01-01-2026 01:00", "2.5", IMPORT),  # differs -> conflict
            ("01-01-2026 01:30", "3.0", IMPORT),  # new -> admissible
        ),
    )
    validation = mod.validate_refresh(known, result, origin=origin)
    assert [r.end for r in validation.unchanged] == [a]
    assert [c.end for c in validation.conflicts] == [b]
    assert validation.conflicts[0].known_kwh == Decimal("2.0")
    assert validation.conflicts[0].incoming_kwh == Decimal("2.5")
    # The new END 01:30Z is the first half of an incomplete hour, so it is pending and
    # is not admitted as a complete-prefix admissible reading.
    assert validation.admissible == ()
    assert validation.extension_stopped_at == H0 + timedelta(hours=1)
    assert validation.blocked is True


def test_refresh_holds_earlier_than_origin_and_rejected_is_atomic(mod):
    result = mod.normalize(
        rows(
            ("01-01-2026 00:30", "1.0", IMPORT),  # START 00:00Z < O(01:00Z) -> held
            ("01-01-2026 01:30", "1.0", IMPORT),  # START 01:00Z == O -> admissible
        ),
        origin=datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
    )
    o = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)
    validation = mod.validate_refresh({}, result, origin=o)
    # The lone new END 01:30Z is a trailing partial; nothing exceeds the watermark.
    assert validation.admissible == ()
    assert validation.extension_stopped_at == o

    rejected = mod.normalize(rows(("01-01-2026 00:30", "-1.0", IMPORT)))
    blocked = mod.validate_refresh({}, rejected, origin=H0)
    assert blocked.rejected is True
    assert blocked.admissible == ()


# ------------------------------------------------------- identity / boundary


def test_identity_captured_but_never_leaked_in_errors(mod):
    text = rows(
        ("01-01-2026 00:30", "1.0", IMPORT),
        ("01-01-2026 01:00", "-1.0", IMPORT),
    )
    result = mod.normalize(text, identity_columns=("MPRN", "Meter Serial Number"))
    assert result.identity == {"MPRN": "99999999999", "Meter Serial Number": "SN-SYNTHETIC"}
    assert result.rejected
    blob = " ".join(r.reason for r in result.rejections) + " " + " ".join(
        d.reason for d in result.dispositions
    )
    assert "99999999999" not in blob and "SN-SYNTHETIC" not in blob
    with pytest.raises(mod.NormalizationRejectedError) as excinfo:
        mod.raise_if_rejected(result)
    assert "99999999999" not in str(excinfo.value)

    binding = mod.bind_source(only(mod, rows(("01-01-2026 00:30", "1.0", IMPORT)), identity_columns=("MPRN",)), "stream-1")
    assert binding.stream_id == "stream-1"
    assert binding.contract_id == mod.SOURCE_CONTRACT_ID


def test_missing_declared_identity_column_is_schema_error(mod):
    result = mod.normalize(rows(("01-01-2026 00:30", "1.0", IMPORT)), identity_columns=("Account Number",))
    assert result.rejected and result.schema_ok is False


def test_readings_are_frozen_ledger_interval_reading(mod, led):
    assert mod.IntervalReading is led.IntervalReading
    result = only(mod, rows(("15-01-2026 01:00", "1.0", IMPORT)))
    reading = result.readings[0]
    assert isinstance(reading, led.IntervalReading)
    assert reading.end.utcoffset() == timedelta(0)
    assert isinstance(reading.energy_kwh, Decimal)
    assert mod.SOURCE_CONTRACT_ID == "esb-current-account-calculated-kwh-dublin-civil-end-unique/v1"


def test_module_stays_stdlib_only_without_homeassistant(mod):
    tree = ast.parse(NORMALIZATION_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(
        name == "homeassistant"
        or name.startswith("homeassistant.")
        or name == "custom_components"
        or name.startswith("custom_components.")
        for name in imported
    ), imported


def test_resolve_local_end_cardinalities(mod):
    def naive(y, m, d, hh, mm):
        return datetime(y, m, d, hh, mm)

    assert mod.resolve_local_end(naive(2026, 3, 29, 1, 30)) == ()            # nonexistent
    assert mod.resolve_local_end(naive(2026, 3, 29, 2, 0)) == (
        datetime(2026, 3, 29, 1, 0, tzinfo=UTC),
    )
    assert mod.resolve_local_end(naive(2026, 10, 25, 1, 30)) == (
        datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
        datetime(2026, 10, 25, 1, 30, tzinfo=UTC),
    )
    assert mod.resolve_local_end(naive(2026, 10, 25, 2, 0)) == (
        datetime(2026, 10, 25, 2, 0, tzinfo=UTC),
    )


def test_parse_csv_tolerates_bom_and_blank_lines(mod):
    text = "\ufeff" + SOURCE_HEADER + "\n15-01-2026 01:00,1.0,%s,1,S\n\n" % IMPORT
    parsed = mod.parse_csv(text)
    assert parsed.header[0] == mod.HEADER_DATE
    assert len(parsed.rows) == 1
    result = mod.normalize(text)
    assert len(result.readings) == 1


def test_proposal_returns_no_energy(mod):
    ends = [H0 + timedelta(minutes=30), H0 + timedelta(hours=1)]
    proposal = mod.propose_maximal_suffix(_readings(mod, ends))
    assert not hasattr(proposal, "energy_kwh")
    assert proposal.counts == {} and proposal.reasons == {}


# -------------------------------------- archive provenance / conflict safeguards


# The conflicting interval in these cases is END 01:30Z (label 29-03-2026 02:30),
# which is the second half of the O-anchored complete hour 01:00.
CONFLICT_END = datetime(2026, 3, 29, 1, 30, tzinfo=UTC)


def test_archive_conflicting_interval_never_a_winner_in_either_order(mod):
    """A conflicting unique interval must not yield a usable reading in archive mode."""
    outcomes = []
    for order in (("1", "9"), ("9", "1")):
        text = rows(
            ("29-03-2026 02:30", order[0], IMPORT),
            ("29-03-2026 03:00", "2", IMPORT),
            ("29-03-2026 02:30", order[1], IMPORT),
        )
        strict = mod.normalize(text, origin=O)
        assert strict.rejected and strict.readings == ()

        archive = mod.normalize(text, origin=O, strict=False)
        # Neither occurrence may win: the conflicted identity is simply unusable, and
        # that classification must not depend on row order.
        assert CONFLICT_END not in {r.end for r in archive.readings}
        assert archive.counts.get(mod.REASON_CONFLICTING_DUPLICATE) == 2
        assert archive.counts.get(mod.REASON_OK, 0) == 1  # only END 02:00Z remains

        # Archive provenance may not cross the refresh boundary without strict revalidation.
        refresh = mod.validate_refresh({}, archive, origin=O)
        assert refresh.rejected is True and refresh.admissible == ()

        outcomes.append(tuple((r.end, r.energy_kwh) for r in archive.readings))
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize(
    ("bad_label", "bad_value", "bad_type", "reason_attr"),
    [
        ("29-03-2026 02:30", "-1", IMPORT, "REASON_NEGATIVE_ENERGY"),
        ("29-03-2026 02:30", "1.0", "Active Import Interval (MWh)", "REASON_UNKNOWN_IMPORT_TYPE"),
        ("29-03-2026 02:3x", "1.0", IMPORT, "REASON_MALFORMED_DATE"),
    ],
)
def test_archive_fatal_row_quarantined_and_refresh_rejected(
    mod, bad_label, bad_value, bad_type, reason_attr
):
    """An archive batch with a fatal row must not become an unblocked refresh."""
    text = rows(
        ("29-03-2026 02:30", "1", IMPORT),
        ("29-03-2026 03:00", "2", IMPORT),
        (bad_label, bad_value, bad_type),
    )
    assert mod.normalize(text, origin=O).rejected

    archive = mod.normalize(text, origin=O, strict=False)
    assert archive.counts.get(getattr(mod, reason_attr)) == 1

    refresh = mod.validate_refresh({}, archive, origin=O)
    assert refresh.rejected is True
    assert refresh.admissible == ()

    batch = mod.build_selected_batch(
        refresh.admissible, origin=O, requested_through=O + timedelta(hours=1)
    )
    assert batch.complete is False
    assert batch.interval_count == 0


def test_strict_result_is_still_accepted_at_refresh_boundary(mod):
    """Guard: archive rejection must not block a freshly strictly validated result."""
    strict = only(
        mod,
        rows(("29-03-2026 02:30", "1", IMPORT), ("29-03-2026 03:00", "2", IMPORT)),
        origin=O,
    )
    refresh = mod.validate_refresh({}, strict, origin=O)
    assert refresh.rejected is False
    assert {r.end for r in refresh.admissible} == {
        datetime(2026, 3, 29, 1, 30, tzinfo=UTC),
        datetime(2026, 3, 29, 2, 0, tzinfo=UTC),
    }


# ------------------------------------------------- complete-prefix clipping


def test_selected_batch_clips_trailing_partial(mod):
    result = only(
        mod,
        rows(
            ("29-03-2026 02:30", "1", IMPORT),  # END 01:30Z, hour 01:00 complete half
            ("29-03-2026 03:00", "2", IMPORT),  # END 02:00Z, hour 01:00 complete half
            ("29-03-2026 03:30", "8", IMPORT),  # END 02:30Z, trailing partial
        ),
        origin=O,
    )
    batch = mod.build_selected_batch(result.readings, origin=O, requested_through=O + timedelta(hours=2))
    assert batch.complete is False
    assert batch.watermark == O + timedelta(hours=1)
    assert batch.pending_partial is True
    assert batch.complete_hours == 1
    assert batch.interval_count == 2
    assert all(r.end <= batch.watermark for r in batch.readings)
    assert sum((r.energy_kwh for r in batch.readings), Decimal(0)) == Decimal("3")


def test_selected_batch_clips_post_gap(mod):
    result = only(
        mod,
        rows(
            ("29-03-2026 02:30", "1", IMPORT),  # END 01:30Z, hour 01:00
            ("29-03-2026 03:00", "2", IMPORT),  # END 02:00Z, hour 01:00
            ("29-03-2026 04:30", "8", IMPORT),  # END 03:30Z, hour 03:00 (after a gap)
            ("29-03-2026 05:00", "9", IMPORT),  # END 04:00Z, hour 03:00
        ),
        origin=O,
    )
    batch = mod.build_selected_batch(result.readings, origin=O, requested_through=O + timedelta(hours=4))
    assert batch.complete is False
    assert batch.watermark == O + timedelta(hours=1)
    assert batch.blocking_reason == mod.BOUNDARY_GAP
    assert all(r.end <= batch.watermark for r in batch.readings)
    assert {r.end for r in batch.readings} == {
        datetime(2026, 3, 29, 1, 30, tzinfo=UTC),
        datetime(2026, 3, 29, 2, 0, tzinfo=UTC),
    }


def test_refresh_clips_post_gap_admissible(mod):
    known = {O + timedelta(minutes=30): Decimal("1"), O + timedelta(hours=1): Decimal("2")}
    incoming = only(
        mod,
        rows(
            ("29-03-2026 04:30", "8", IMPORT),  # END 03:30Z, post-gap
            ("29-03-2026 05:00", "9", IMPORT),  # END 04:00Z, post-gap
        ),
        origin=O,
    )
    validation = mod.validate_refresh(known, incoming, origin=O)
    assert validation.origin == O
    assert validation.extension_stopped_at == O + timedelta(hours=1)
    assert validation.admissible == ()
    assert validation.rejected is False


def test_refresh_admits_complete_new_hour(mod):
    known = {H0 + timedelta(minutes=30): Decimal("1"), H0 + timedelta(hours=1): Decimal("2")}
    result = only(
        mod,
        rows(("01-01-2026 01:30", "3", IMPORT), ("01-01-2026 02:00", "4", IMPORT)),
    )
    validation = mod.validate_refresh(known, result, origin=H0)
    assert validation.rejected is False
    assert {r.end for r in validation.admissible} == {
        H0 + timedelta(hours=1, minutes=30),
        H0 + timedelta(hours=2),
    }
