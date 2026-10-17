"""Offline original-CSV coordinator acceptance with genuine Recorder."""
import csv
from io import StringIO
from types import SimpleNamespace
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from tests.test_compatible_publisher import (
    genuine_recorder, read_state_sum_rows, read_changes, read_metadata,
)
from custom_components.esb_smart_meter import energy_runtime as er
from custom_components.esb_smart_meter import compatible_publisher as cp
from custom_components.esb_smart_meter.coordinator import ESBDataUpdateCoordinator
from custom_components.esb_smart_meter.models import ESBData

@pytest.fixture(autouse=True)
def mock_asyncio_sleep():
    yield


def raw_csv(values=("0.5", "0.5")):
    rows = ["Read Date and End Time,Read Value,Read Type,MPRN"]
    for i, value in enumerate(values):
        end = er.BOUNDED_ORIGIN + timedelta(minutes=30 * (i + 1), hours=1)
        rows.append(f'{end:%d-%m-%Y %H:%M},{value},Active Import Interval (kWh),synthetic-mprn')
    return '\n'.join(rows)


def coordinator_for(hass, tmp_path, entry_id, raw):
    entry = SimpleNamespace(entry_id=entry_id)
    runtime = er.create_bounded_runtime(hass, entry_id=entry_id,
        mprn="synthetic-mprn", storage_dir=tmp_path)
    api = SimpleNamespace(fetch_with_raw=AsyncMock(return_value=SimpleNamespace(
        raw_text=raw, data=ESBData(data=list(csv.DictReader(StringIO(raw)))))))
    api.fetch = AsyncMock(return_value=api.fetch_with_raw.return_value.data)
    coordinator = ESBDataUpdateCoordinator(hass, api, "synthetic-mprn", entry,
        energy_runtime=runtime)
    return coordinator, runtime, api


@pytest.mark.parametrize("persistent_database", [True])
async def test_original_old_csv_empty_presentation_reaches_genuine_recorder(
    genuine_recorder, hass, tmp_path, monkeypatch
):
    c, runtime, api = coordinator_for(hass, tmp_path, "raw-first-hour", raw_csv())
    enqueue = runtime.publisher._port.enqueue
    async def checked_enqueue(payload):
        reopened = er.DurableOwnerStore(runtime.owner_path).load()
        assert reopened.submitted_value_map() == payload.value_map()
        await enqueue(payload)
    monkeypatch.setattr(runtime.publisher._port, "enqueue", checked_enqueue)
    presentation = await c._async_update_data()
    assert presentation._data == []
    assert c.update_interval == timedelta(hours=24)
    assert c.energy_status.publication_status == cp.STATUS_PUBLISHED
    sid = runtime.publisher.identity.statistic_id
    assert await read_state_sum_rows(hass, sid, er.BOUNDED_ORIGIN,
        er.BOUNDED_ORIGIN + timedelta(hours=1)) == {er.BOUNDED_ORIGIN: (1.0, 1.0)}
    assert await read_changes(hass, sid, er.BOUNDED_ORIGIN,
        er.BOUNDED_ORIGIN + timedelta(hours=1)) == [(er.BOUNDED_ORIGIN, 1.0)]
    api.fetch_with_raw.assert_awaited_once()
    await runtime.async_unload()

@pytest.mark.parametrize("gate", [False, None, 1, "true", True])
async def test_actual_setup_gate_and_failure_cleanup(hass, tmp_path, monkeypatch, gate):
    import custom_components.esb_smart_meter as integration
    entry = SimpleNamespace(entry_id=f"gate-{gate!r}", data={"username":"synthetic", "password":"synthetic", "mprn":"synthetic-mprn"}, options={"enable_bounded_energy":gate})
    session = SimpleNamespace(closed=False, close=AsyncMock())
    monkeypatch.setattr(integration, "create_esb_session", AsyncMock(return_value=session))
    monkeypatch.setattr(integration, "ESBDataApi", lambda **kwargs: object())
    attached = []
    async def fail_refresh(coordinator):
        attached.append(coordinator.energy_runtime)
        raise RuntimeError("synthetic setup failure")
    monkeypatch.setattr(ESBDataUpdateCoordinator, "async_config_entry_first_refresh", fail_refresh)
    original_factory = er.create_bounded_runtime
    def factory(hass, **kwargs):
        return original_factory(hass, storage_dir=tmp_path, **kwargs)
    monkeypatch.setattr(integration, "create_bounded_runtime", factory)
    with pytest.raises(RuntimeError, match="synthetic setup failure"):
        await integration.async_setup_entry(hass, entry)
    assert (attached[0] is not None) is (gate is True)
    session.close.assert_awaited_once()
    assert entry.entry_id not in hass.data["esb_smart_meter"]
    if gate is True:
        assert not attached[0].publisher.admitting
        with pytest.raises(cp.DuplicateOwnerError):
            factory(hass, entry_id=entry.entry_id, mprn="synthetic-mprn")

@pytest.mark.parametrize("persistent_database", [True])
async def test_sequential_raw_refresh_duplicate_truncated_and_public_discovery(
    genuine_recorder, hass, tmp_path, monkeypatch
):
    from homeassistant.components.recorder.statistics import list_statistic_ids
    c, runtime, api = coordinator_for(hass, tmp_path, "raw-sequence", raw_csv())
    enqueued = []
    original = runtime.publisher._port.enqueue
    async def observe(payload):
        enqueued.append(payload)
        await original(payload)
    monkeypatch.setattr(runtime.publisher._port, "enqueue", observe)
    values = ("0.5", "0.5", "1", "1", "1.5", "1.5", "2", "2")
    for hours in range(1, 5):
        raw = raw_csv(values[:2*hours])
        api.fetch_with_raw.return_value = SimpleNamespace(raw_text=raw, data=ESBData(data=list(csv.DictReader(StringIO(raw)))))
        await c._async_update_data()
        assert c.energy_status.publication_status == cp.STATUS_PUBLISHED
        state = await runtime.operations.async_read_stream_state(runtime.publisher.identity.stream_id)
        assert state.generation == hours and state.rows == hours*2 and not state.dirty
        assert len(enqueued) == hours
    prior_owner = er.DurableOwnerStore(runtime.owner_path).load()
    for raw in (raw_csv(values), raw_csv()):
        api.fetch_with_raw.return_value = SimpleNamespace(raw_text=raw, data=ESBData(data=[]))
        await c._async_update_data()
        assert c.energy_status.publication_status == cp.STATUS_NOTHING_NEW
        assert c.energy_status.complete
        assert len(enqueued) == 4
        assert er.DurableOwnerStore(runtime.owner_path).load() == prior_owner
        assert await runtime.operations.async_read_stream_state(runtime.publisher.identity.stream_id) == state
    sid = runtime.publisher.identity.statistic_id
    expected = {er.BOUNDED_ORIGIN+timedelta(hours=i):(value,value) for i,value in enumerate((1.,3.,6.,10.))}
    assert await read_state_sum_rows(hass, sid, er.BOUNDED_ORIGIN, er.BOUNDED_ORIGIN+timedelta(hours=4)) == expected
    assert [v for _,v in await read_changes(hass,sid,er.BOUNDED_ORIGIN,er.BOUNDED_ORIGIN+timedelta(hours=4))] == [1.,2.,3.,4.]
    metadata = await read_metadata(hass, sid)
    assert metadata["source"] == "esb_smart_meter"
    assert metadata["unit_of_measurement"] == "kWh"
    assert metadata["unit_class"] == "energy" and metadata["has_sum"] is True
    assert int(metadata["mean_type"]) == 0
    # HA 2026.9 makes exact IDs and statistic_type mutually exclusive.
    # Prove both exact lookup and the sum catalog used for Energy discovery.
    discovered = await genuine_recorder.async_add_executor_job(list_statistic_ids, hass, {sid})
    sum_catalog = await genuine_recorder.async_add_executor_job(list_statistic_ids, hass, None, "sum")
    for catalog in (discovered, sum_catalog):
        matches = [row for row in catalog if row["statistic_id"] == sid]
        assert len(matches) == 1
        assert matches[0]["has_sum"] is True
        assert matches[0]["source"] == "esb_smart_meter"
        assert matches[0]["statistics_unit_of_measurement"] == "kWh"
    await runtime.async_unload()


@pytest.mark.parametrize("persistent_database", [True])
@pytest.mark.parametrize("case", ["mixed", "equal_total_conflict", "partial", "gap", "autumn", "unknown_type", "missing_identity", "older_ambiguity"])
async def test_raw_rejections_and_incomplete_keep_verified_prefix(
    genuine_recorder, hass, tmp_path, case, monkeypatch
):
    c,runtime,api = coordinator_for(hass,tmp_path,f"raw-{case}",raw_csv())
    await c._async_update_data()
    assert c.energy_status.publication_status == cp.STATUS_PUBLISHED
    sid = runtime.publisher.identity.statistic_id
    before = await runtime.operations.async_read_stream_state(sid.split(':')[1].removesuffix('_grid_import'))
    owner = er.DurableOwnerStore(runtime.owner_path).load()
    raws = {
        "mixed": raw_csv().replace('0.5,Active Import Interval (kWh),synthetic-mprn', '0.5,Active Import Interval (kWh),other', 1),
        "equal_total_conflict": raw_csv(("0.4","0.6")),
        "partial": raw_csv(("0.5","0.5","1")),
        "gap": raw_csv()+"\n29-03-2026 04:30,1,Active Import Interval (kWh),synthetic-mprn\n29-03-2026 05:00,1,Active Import Interval (kWh),synthetic-mprn",
        "autumn": raw_csv()+"\n25-10-2026 01:30,1,Active Import Interval (kWh),synthetic-mprn",
        "unknown_type": raw_csv().replace('Active Import Interval (kWh)','unknown',1),
        "missing_identity": raw_csv().replace(',synthetic-mprn','',1),
        "older_ambiguity": raw_csv()+"\n26-10-2025 01:30,1,Active Import Interval (kWh),synthetic-mprn",
    }
    enqueue = AsyncMock(wraps=runtime.publisher._port.enqueue)
    monkeypatch.setattr(runtime.publisher._port,'enqueue',enqueue)
    api.fetch_with_raw.return_value = SimpleNamespace(raw_text=raws[case],data=ESBData(data=[]))
    await c._async_update_data()
    enqueue.assert_not_awaited()
    assert er.DurableOwnerStore(runtime.owner_path).load() == owner
    assert await runtime.operations.async_read_stream_state(runtime.publisher.identity.stream_id) == before
    assert c.energy_status.verified_through == er.BOUNDED_ORIGIN+timedelta(hours=1)
    if case in ('mixed','equal_total_conflict','unknown_type','missing_identity'):
        assert c.energy_status.publication_status == 'blocked_source'
    elif case == 'older_ambiguity':
        assert c.energy_status.complete
    else:
        assert not c.energy_status.complete
        assert c.energy_status.pending_partial or c.energy_status.gap_count or c.energy_status.quarantine_count
    assert await read_state_sum_rows(hass,sid,er.BOUNDED_ORIGIN,er.BOUNDED_ORIGIN+timedelta(hours=4)) == {er.BOUNDED_ORIGIN:(1.,1.)}
    await runtime.async_unload()


@pytest.mark.parametrize("persistent_database", [True])
async def test_mixed_second_row_never_establishes_binding(genuine_recorder,hass,tmp_path):
    raw=raw_csv().rsplit('synthetic-mprn',1)[0]+'other'
    c,runtime,api=coordinator_for(hass,tmp_path,'mixed-second-first-use',raw)
    await c._async_update_data()
    assert c.energy_status.reason == 'source_mismatch'
    assert er.DurableOwnerStore(runtime.owner_path).load() is None
    assert not runtime.operations.ledger_exists
    assert runtime.publisher.submitted_hours == 0
    await runtime.async_unload()


@pytest.mark.parametrize("persistent_database", [True])
async def test_actual_enabled_setup_unload_during_raw_callback_retains_claim(
    genuine_recorder,hass,tmp_path,monkeypatch
):
    import asyncio
    import custom_components.esb_smart_meter as integration
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from homeassistant.config_entries import ConfigEntryState
    entry=MockConfigEntry(domain='esb_smart_meter',entry_id='setup-unload-raw', data={'username':'synthetic','password':'synthetic','mprn':'synthetic-mprn'},options={'enable_bounded_energy':True})
    entry.add_to_hass(hass)
    entry._async_set_state(hass, ConfigEntryState.SETUP_IN_PROGRESS, None)
    session=SimpleNamespace(closed=False,close=AsyncMock())
    api=SimpleNamespace(fetch_with_raw=AsyncMock(return_value=SimpleNamespace(raw_text=raw_csv(),data=ESBData(data=[]))))
    monkeypatch.setattr(integration,'create_esb_session',AsyncMock(return_value=session))
    monkeypatch.setattr(integration,'ESBDataApi',lambda **kwargs:api)
    factory=er.create_bounded_runtime
    monkeypatch.setattr(integration,'create_bounded_runtime',lambda hass,**kwargs:factory(hass,storage_dir=tmp_path,**kwargs))
    monkeypatch.setattr(hass.config_entries,'async_forward_entry_setups',AsyncMock())
    monkeypatch.setattr(hass.config_entries,'async_unload_platforms',AsyncMock(return_value=True))
    assert await integration.async_setup_entry(hass,entry)
    c=hass.data['esb_smart_meter'][entry.entry_id]['coordinator']
    runtime=c.energy_runtime
    assert c.energy_status.publication_status == cp.STATUS_PUBLISHED
    original=runtime.operations.async_read_stream_state
    before=await original(runtime.publisher.identity.stream_id)
    reached=asyncio.Event(); release=asyncio.Event()
    async def held(stream):
        reached.set()
        await release.wait()
        return await original(stream)
    monkeypatch.setattr(runtime.operations,'async_read_stream_state',held)
    api.fetch_with_raw.return_value=SimpleNamespace(raw_text=raw_csv(('0.5','0.5','1','1')),data=ESBData(data=[]))
    task=asyncio.create_task(c._async_update_data())
    await asyncio.wait_for(reached.wait(),5)
    assert await integration.async_unload_entry(hass,entry)
    assert not runtime.publisher.admitting
    release.set()
    await asyncio.wait_for(task,5)
    assert c.energy_status.publication_status == 'fenced'
    assert await original(runtime.publisher.identity.stream_id) == before
    session.close.assert_awaited_once()
    assert er.DurableOwnerStore(runtime.owner_path).load().verified_length == 1
    with pytest.raises(cp.DuplicateOwnerError):
        factory(hass,entry_id=entry.entry_id,mprn='synthetic-mprn',storage_dir=tmp_path)

async def test_default_disabled_coordinator_only_uses_legacy_fetch(hass):
    presentation=ESBData(data=[])
    api=SimpleNamespace(fetch=AsyncMock(return_value=presentation),fetch_with_raw=AsyncMock())
    c=ESBDataUpdateCoordinator(hass,api,'synthetic-mprn',SimpleNamespace(entry_id='disabled'))
    assert await c._async_update_data() is presentation
    api.fetch.assert_awaited_once()
    api.fetch_with_raw.assert_not_awaited()
    assert c.energy_runtime is None and c.energy_status is None
    assert c.update_interval == timedelta(hours=24)


async def test_archive_provenance_blocks_raw_before_binding(hass,tmp_path,monkeypatch):
    from dataclasses import replace
    original=er._normalization.normalize
    def archive(*args,**kwargs):
        return replace(original(*args,**kwargs),strict=False)
    monkeypatch.setattr(er._normalization,'normalize',archive)
    c,runtime,api=coordinator_for(hass,tmp_path,'archive-provenance',raw_csv())
    await c._async_update_data()
    assert c.energy_status.publication_status=='blocked_source'
    assert not runtime.operations.ledger_exists
    assert er.DurableOwnerStore(runtime.owner_path).load() is None
    await runtime.async_unload()
