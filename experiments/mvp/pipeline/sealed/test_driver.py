"""Offline control-plane tests. Every input is synthetic temporary data."""
from __future__ import annotations

from pathlib import Path
import json
import pytest

from experiments.mvp.pipeline.sealed import common, driver


@pytest.fixture
def harness(tmp_path, monkeypatch):
    here = tmp_path / 'sealed'
    here.mkdir()
    source = tmp_path / 'runtime.py'
    source.write_text('value = 1\n')
    prereg = here / 'preregistration.json'
    prereg.write_text(json.dumps(dict(required_arms=['pipeline', 'twostep'],
                                     metrics=['L0', 'lifts'], instruction_selection={'fixed': True})))
    (here / 'PREREG.md').write_text('Protocol fixed before test inputs.\n')
    map_path = tmp_path / 'development.json'
    map_path.write_text('{"synthetic":true}\n')
    monkeypatch.setattr(driver, 'HERE', here)
    monkeypatch.setattr(driver, 'GLOBAL_CLAIM', here / 'SEALED-CLAIM.json')
    monkeypatch.setattr(driver, 'source_inventory', lambda: [source])
    config = dict(mode='dry-run', workers=1, preregistration=str(prereg), stubbed=['planner'],
        maps=[dict(map_id='development', path=str(map_path), sha256=common.sha256(map_path), cohort='tiny15')])
    return here, source, config


def test_admission_waits_for_missing_bad_stale_and_capacity(tmp_path):
    path = tmp_path / 'capacity.json'
    states = [{}, dict(mask=3, load1=1, t=100, other_fleet_work=[]),
              dict(mask=255, load1=5, t=100, other_fleet_work=[]),
              dict(mask=255, load1=1, t=-30, other_fleet_work=[]),
              dict(mask=255, load1=4, t=100, other_fleet_work=[])]
    waits = []
    def advance(seconds):
        waits.append(seconds)
        path.write_text(json.dumps(states.pop(0)))
    snapshot = common.wait_for_admission(path, wait=advance, now=lambda: 100, poll_s=1)
    assert snapshot['cores'] == 8
    assert len(waits) == 5


@pytest.mark.parametrize('row', [dict(mask=True, load1=1, t=100), dict(mask=255, load1=-1, t=100),
    dict(mask=255, load1=1, t=101), dict(mask=255, load1=1, t=-20)])
def test_bad_admission_returns_none(tmp_path, row):
    path = tmp_path / 'state.json'
    path.write_text(json.dumps(row))
    assert common.admission(path, now=100) is None


def test_freeze_and_dirty_source_refusal(harness):
    here, source, config = harness
    out = here / 'runs' / 'test'
    frozen = driver.freeze(config, out)
    assert driver.verify_freeze(out) == frozen
    source.write_text('value = 2\n')
    with pytest.raises(common.HarnessError, match='Dirty frozen code'):
        driver.verify_freeze(out)


def test_inventory_addition_is_refused(harness, monkeypatch):
    here, source, config = harness
    out = here / 'runs' / 'test'
    driver.freeze(config, out)
    monkeypatch.setattr(driver, 'source_inventory', lambda: [source, source.with_name('new.py')])
    with pytest.raises(common.HarnessError, match='inventory changed'):
        driver.verify_freeze(out)


def test_preregistration_change_is_refused(harness):
    here, _, config = harness
    out = here / 'runs' / 'test'
    driver.freeze(config, out)
    (here / 'PREREG.md').write_text('post hoc change')
    with pytest.raises(common.HarnessError, match='Dirty frozen metadata'):
        driver.verify_freeze(out)


def test_config_tampering_is_refused(harness):
    here, _, config = harness
    out = here / 'runs' / 'test'
    driver.freeze(config, out)
    common.atomic_json(out / 'config.json', dict(config, workers=2))
    with pytest.raises(common.HarnessError, match='Frozen config changed'):
        driver.verify_freeze(out)


def test_exclusive_claim_never_replaced(tmp_path):
    path = tmp_path / 'claim.json'
    common.exclusive_json(path, dict(run='first'))
    with pytest.raises(common.HarnessError, match='claim already exists'):
        common.exclusive_json(path, dict(run='second'))
    assert common.read_json(path) == dict(run='first')


def test_no_sealed_payload_read_during_preparation(monkeypatch, tmp_path):
    sealed = tmp_path / 'corpus'
    monkeypatch.setattr(driver, 'V3', sealed)
    with pytest.raises(common.HarnessError, match='cannot open sealed'):
        driver.public_path(sealed / 'tiny' / 'map.json')
    assert driver.public_path(sealed / 'manifest.json') == sealed / 'manifest.json'


def test_dry_refuses_sealed_symlink(harness, monkeypatch, tmp_path):
    here, _, config = harness
    sealed = tmp_path / 'corpus'
    sealed.mkdir()
    monkeypatch.setattr(driver, 'V3', sealed)
    link = tmp_path / 'dev-link.json'
    link.symlink_to(sealed / 'unopened.json')
    config['maps'][0]['path'] = str(link)
    with pytest.raises(common.HarnessError, match='cannot open sealed'):
        driver.freeze(config, here / 'runs' / 'test')


@pytest.mark.parametrize('workers', [0, 25, True])
def test_worker_limit(harness, workers):
    here, _, config = harness
    config['workers'] = workers
    with pytest.raises(common.HarnessError, match='workers'):
        driver.freeze(config, here / 'runs' / 'test')


def test_incomplete_and_completed_run_refuse_before_dispatch(harness):
    here, _, config = harness
    out = here / 'runs' / 'test'
    driver.freeze(config, out)
    common.exclusive_json(out / 'STARTED.json', dict(t=1))
    def forbidden(*args, **kwargs):
        pytest.fail('Rerun must not dispatch or wait for capacity')
    with pytest.raises(common.HarnessError, match='completed or incomplete reruns'):
        driver.run(out, worker=forbidden, admission_wait=forbidden)


def test_missing_manifest_never_reads_maps(harness, monkeypatch):
    here, _, config = harness
    config.update(mode='sealed', stubbed=[], pipeline_manifest=str(here / 'not-landed.json'))
    prereg=common.read_json(config['preregistration'])
    prereg['amendment']={'sha256':common.sha256(driver.PROTOCOL_HOME/'AMENDMENT.md')}
    common.atomic_json(config['preregistration'], prereg)
    monkeypatch.setattr(driver, 'declared_sealed_maps', lambda: pytest.fail('premature map metadata load'))
    with pytest.raises(common.HarnessError, match='verification failed'):
        driver.freeze(config, here / 'runs' / 'test')


def test_runtime_inventory_excludes_sealed_payloads():
    paths = driver.source_inventory()
    assert paths
    assert not any(driver.is_sealed(p) for p in paths)
    assert all('runs' not in p.parts for p in paths)


def test_dry_injected_run_writes_full_report_and_refuses_rerun(harness):
    here, _, config = harness
    out = here / 'runs' / 'test'
    driver.freeze(config, out)
    def stub(job, target, parser=None):
        return dict(map_id=job['map_id'], cohort=job['cohort'], arm=job['arm'],
            map_sha256=job['sha256'], status='no_checked_plan', passed=False,
            level=None, instruction_recheck=None, wall_s=0.0, fixture_surrogate=True)
    report = driver.run(out, worker=stub, admission_wait=lambda path: dict(mask=255, load1=0, t=1))
    assert isinstance(report, dict)
    assert (out / 'COMPLETE.json').is_file()
    assert (out / 'report.json').is_file()
    assert len(common.read_json(out / 'rows.json')) == 2
    with pytest.raises(common.HarnessError, match='reruns'):
        driver.run(out, worker=stub, admission_wait=lambda path: pytest.fail('must not dispatch'))


def test_inventory_resolves_file_symlinks_and_never_opens_sealed(tmp_path, monkeypatch):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    source = runtime / 'module.py'
    source.write_text('pass\n')
    sealed = tmp_path / 'private'
    sealed.mkdir()
    (runtime / 'sealed_link.py').symlink_to(sealed / 'unopened.py')
    (runtime / 'alias.py').symlink_to(source)
    monkeypatch.setattr(driver, 'ROOT', tmp_path)
    monkeypatch.setattr(driver, 'V3', sealed)
    monkeypatch.setattr(driver, 'ENVELOPE', tmp_path / 'envelope')
    monkeypatch.setattr(driver, 'SOURCE_ROOTS', ('runtime',))
    assert driver.source_inventory() == [source]
