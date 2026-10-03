"""Durable ownership storage tests for ``energy_runtime`` (approved first phase).

Scope: the atomic durable owner-record target-file protocol only -- write a
complete record to a private sibling tempfile, fsync it, ``os.replace`` onto the
target, fsync the directory, then REOPEN the target and compare. The reopened
target is the only durable success result; every write/fsync/replace/readback
failure propagates and leaves the previous target intact.

No HA, no Recorder, no ledger, no network: plain files under ``tmp_path``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from custom_components.esb_smart_meter import energy_runtime as er

SLUG = "energyruntime01"
ENTRY = "energy-runtime-test-entry"
MPRN = "10000000001"
ORIGIN = datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc)


def make_binding(mprn: str = MPRN) -> er.SourceBinding:
    return er.SourceBinding.synthetic(mprn=mprn)


def make_record(**overrides) -> er.OwnerRecord:
    kwargs = dict(
        entry_id=ENTRY,
        slug=SLUG,
        statistic_id=f"esb_smart_meter:{SLUG}_grid_import",
        source="esb_smart_meter",
        product=er.PRODUCT_INTERVAL_KWH,
        source_binding=make_binding(),
        ledger_contract="esb-halfhour-interval-end-utc/v1",
        unit="kWh",
        unit_class="energy",
        mean_type=0,
        has_sum=True,
        origin=ORIGIN,
        submitted_generation=1,
        submitted_rows=((ORIGIN, 1.0, 1.0),),
        verified_length=1,
        verified_generation=1,
    )
    kwargs.update(overrides)
    return er.OwnerRecord(**kwargs)


def make_store(tmp_path, slug: str = SLUG) -> er.DurableOwnerStore:
    return er.DurableOwnerStore(er.owner_record_path(tmp_path, slug))


# --------------------------------------------------- RED: durable target write/reopen


def test_owner_record_is_durable_across_a_reopen(tmp_path):
    """Save must land on the TARGET path and reopen byte-identically."""
    path = er.owner_record_path(tmp_path, SLUG)
    record = make_record()

    make_store(tmp_path).save(record)

    assert path.exists(), path
    assert list(tmp_path.glob("*.tmp")) == []  # no temp residue beside the target

    # A brand new store (a fresh interpreter would do exactly this) reopens it.
    reopened = er.DurableOwnerStore(path).load()
    assert reopened == record
    assert reopened.source_binding == record.source_binding
    assert reopened.submitted_rows == record.submitted_rows


def test_absent_target_reads_as_none_not_corrupt(tmp_path):
    assert make_store(tmp_path).load() is None


def test_corrupt_target_is_a_typed_error(tmp_path):
    path = er.owner_record_path(tmp_path, SLUG)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(er.OwnerRecordCorruptError):
        er.DurableOwnerStore(path).load()


def test_wrong_shape_target_is_a_typed_error(tmp_path):
    path = er.owner_record_path(tmp_path, SLUG)
    path.write_text('{"version": 1}', encoding="utf-8")  # valid JSON, wrong shape
    with pytest.raises(er.OwnerRecordCorruptError):
        er.DurableOwnerStore(path).load()


def test_failed_replace_leaves_the_previous_target_intact(tmp_path, monkeypatch):
    path = er.owner_record_path(tmp_path, SLUG)
    store = make_store(tmp_path)
    original = make_record()
    store.save(original)

    def boom(*_args, **_kwargs):
        raise OSError("injected replace failure")

    monkeypatch.setattr(er, "_replace_file", boom)
    with pytest.raises(er.OwnerRecordWriteError):
        store.save(make_record(submitted_generation=2))

    # The prior durable record is unchanged and still reopens.
    assert er.DurableOwnerStore(path).load() == original
    assert list(tmp_path.glob("*.tmp")) == []


def test_failed_fsync_propagates_and_keeps_the_previous_target(tmp_path, monkeypatch):
    path = er.owner_record_path(tmp_path, SLUG)
    store = make_store(tmp_path)
    original = make_record()
    store.save(original)

    def boom(*_args, **_kwargs):
        raise OSError("injected fsync failure")

    monkeypatch.setattr(er, "_fsync_fd", boom)
    with pytest.raises(er.OwnerRecordWriteError):
        store.save(make_record(submitted_generation=2))

    assert er.DurableOwnerStore(path).load() == original
    assert list(tmp_path.glob("*.tmp")) == []


def test_owner_store_rejects_binding_prefix_and_marker_regression(tmp_path):
    from dataclasses import replace
    store = make_store(tmp_path)
    record = make_record()
    store.save(record)
    for changed in (replace(record, entry_id='foreign'), replace(record, submitted_rows=((ORIGIN, 2.0, 2.0),)), replace(record, verified_length=0, verified_generation=0)):
        with pytest.raises(er.OwnerRecordWriteError):
            store.save(changed)
        assert store.load() == record


@pytest.mark.parametrize('stage', ['directory', 'readback'])
def test_post_replace_failure_is_uncertain_not_success(tmp_path, monkeypatch, stage):
    store = make_store(tmp_path)
    original = make_record()
    store.save(original)
    def boom(*args):
        raise OSError('injected directory failure')
    if stage == 'directory':
        monkeypatch.setattr(er, '_fsync_dir', boom)
    else:
        load = er.DurableOwnerStore.load
        calls = 0
        def readback(self):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise er.OwnerRecordCorruptError('injected target reopen failure')
            return load(self)
        monkeypatch.setattr(er.DurableOwnerStore, 'load', readback)
    with pytest.raises(er.OwnerRecordWriteError):
        store.save(make_record(submitted_generation=2))
