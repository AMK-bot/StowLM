"""Measured settings require complete paired results and a binding checked plan."""
from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from . import profiles, profile_finalize
from .profile_study import study_profile


def grid():
    rows = []
    for label, lifts, engine in [('60', 80., 120.), ('120', 72., 180.),
                                  ('300', 71., 420.), ('default', 70., 600.)]:
        profile = study_profile(label)
        kwargs = {k: profile[k] for k in ('budget_s', 'finalize_s', 'seed', 'config')}
        for mode in ('none', 'one_port_per_hatch'):
            rows.append(dict(job_id=label+'-'+mode, map='synthetic-medium', map_id='synthetic-medium',
                size_class='medium', rule_mode=mode, budget_label=label,
                profile=profile, actual_kwargs=kwargs, status='checked_plan', full_pass=True,
                lifts=lifts, engine_s=engine, total_s=engine+1., first_legal_s=engine/2.,
                baseline_audit_status='checked' if mode != 'none' else None,
                baseline_violates_rule=mode != 'none',
                start=dict(load1=25., enabled_cores=8), end=dict(load1=28., enabled_cores=8)))
    return rows


def study(tmp_path, monkeypatch, rows):
    monkeypatch.setattr(profile_finalize, 'HERE', tmp_path)
    root = tmp_path / 'study'
    (root / 'rows').mkdir(parents=True)
    all_jobs = [{'job_id': r['job_id']} for r in grid()]
    (root / 'jobs.json').write_text(json.dumps({'jobs': all_jobs}))
    for row in rows:
        (root / 'rows' / (row['job_id']+'.json')).write_text(json.dumps(row))
    (root / 'measurements.json').write_text(json.dumps({'rows': rows}))
    return root


def test_publish_chooses_pass_preserving_live_and_common_lift_standard(tmp_path, monkeypatch):
    root = study(tmp_path, monkeypatch, grid())
    target = tmp_path / 'selection.json'
    result = profile_finalize.finalize(root, target=target,
        identity=dict(passed=True, engine_version='0.13.0', manifest_sha256='a'*64))
    assert result['status'] == 'published'
    data = json.loads(target.read_text())
    assert data['profiles']['live']['budget_label'] == '60'
    assert data['profiles']['live']['engine_timeout_s'] == 150.
    assert data['profiles']['standard']['budget_label'] == 'default'
    assert data['profiles']['standard']['engine_kwargs']['config'] is None
    assert data['binding_evidence']['demonstrated_binding_passes'] == 4
    assert len(data['row_files']) == 8 and data['data_sha256']


@pytest.mark.parametrize('issue', ['unfinished', 'censored', 'no_binding_pass'])
def test_incomplete_or_unproven_study_cannot_replace_profiles(tmp_path, monkeypatch, issue):
    rows = grid()
    if issue == 'unfinished':
        rows.pop()
    elif issue == 'censored':
        rows[-1].update(status='interrupted', full_pass=False, censored=True)
    else:
        for row in rows:
            row['baseline_violates_rule'] = False
    root = study(tmp_path, monkeypatch, rows)
    target = tmp_path / 'selection.json'
    target.write_text('preserved existing selection')
    result = profile_finalize.finalize(root, target=target)
    assert result['status'] == 'pending'
    assert target.read_text() == 'preserved existing selection'


def test_failed_engine_manifest_cannot_publish(tmp_path, monkeypatch):
    root = study(tmp_path, monkeypatch, grid())
    with pytest.raises(ValueError, match='manifest'):
        profile_finalize.finalize(root, target=tmp_path / 'selection.json',
            identity=dict(passed=False, engine_version='0.13.0', manifest_sha256='a'*64))


def test_get_profile_preserves_measured_settings_except_explicit_unmeasured_live_ladder_floor(tmp_path, monkeypatch):
    from experiments.mvp.pipeline.binding import adapter
    root = study(tmp_path, monkeypatch, grid())
    target = tmp_path / 'selection.json'
    profile_finalize.finalize(root, target=target,
        identity=dict(passed=True, engine_version=adapter.VERSION, manifest_sha256='a'*64))
    monkeypatch.setattr(profiles, 'CALIBRATION', target)
    monkeypatch.setattr(adapter, 'verify_manifest', lambda: dict(passed=True, manifest_sha256='a'*64))
    selection_bytes = target.read_bytes()
    live = profiles.get_profile('live')
    assert live['pending_measurement'] is True and live['budget_s'] == 60.
    assert live['measured_base_profile'] is True
    assert live['engine_timeout_s'] == 150. and live['data_sha256']
    expected = dict(study_profile('60')['config'], peel_proposal_s=70.)
    assert asdict(live['config']) == expected
    assert live['peel_budget_choice']['previous_proposal_s'] == 20.
    assert live['peel_budget_choice']['measurement'] == 'projection'
    assert target.read_bytes() == selection_bytes
    assert study_profile('60')['config']['peel_proposal_s'] == 20.
    assert profiles.get_profile('standard')['config'] is None


def test_changed_engine_or_partial_selection_cannot_be_used(tmp_path, monkeypatch):
    from experiments.mvp.pipeline.binding import adapter
    root = study(tmp_path, monkeypatch, grid())
    target = tmp_path / 'selection.json'
    profile_finalize.finalize(root, target=target,
        identity=dict(passed=True, engine_version=adapter.VERSION, manifest_sha256='a'*64))
    monkeypatch.setattr(profiles, 'CALIBRATION', target)
    monkeypatch.setattr(adapter, 'verify_manifest', lambda: dict(passed=True, manifest_sha256='b'*64))
    with pytest.raises(ValueError, match='pinned engine'):
        profiles.get_profile('live')
    data = json.loads(target.read_text())
    data['completeness']['complete'] = False
    target.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='incomplete'):
        profiles.get_profile('live')
