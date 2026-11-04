import json
from pathlib import Path

import pytest

from . import backup_campaign as campaign


def test_adaptive_admissions_wait_and_reduce_without_killing():
    assert campaign.worker_limit(None) == 0
    assert campaign.worker_limit({'load1': 29.99}) == 8
    assert campaign.worker_limit({'load1': 30.}) == 4
    assert campaign.worker_limit({'load1': 45.}) == 4


def test_selected_standard_profile_has_independent_hard_deadline():
    profile = campaign.profile_snapshot()
    assert (profile['budget_s'], profile['finalize_s'], profile['engine_timeout_s']) == (300, 100, 1800)
    assert profile['selection_kind'] == 'explicit_partial_study_selection'
    assert Path(profile['calibration_source']).name == 'profile-selection.json'


def test_isolated_report_uses_only_own_manifest_and_paths(tmp_path):
    run_dir = tmp_path/'runs'
    run_dir.mkdir()
    manifest = dict(instance_sha256='backup-only', run_dir=str(run_dir),
        instructions=[dict(id='own', purpose='lattice', sentences=['Own request']),
                      dict(id='pending', purpose='lattice', sentences=['Pending request'])], keys=['own-key', 'pending-key'])
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    (tmp_path/'fixtures').mkdir()
    (tmp_path/'fixtures'/'base-source.json').write_text(json.dumps(dict(
        plan_producer='lattice.recover_candidate', plan={'id': 'base.lattice-block-sequence'},
        gate={'gauges': {'n_frames': 263, 'passed': True, 'all_frames': True}})))
    (run_dir/'own-key.json').write_text(json.dumps(dict(id='own', status='checked_plan', lifts=117,
        computed_utc='recorded-date', engine_wall_s=301, purpose='lattice', sentences=['Own request'])))
    data = campaign.update_report(tmp_path)
    assert data['instance_sha256'] == 'backup-only'
    assert data['instructions_stored'] == 1
    assert data['base_frame_provenance']['frames_checked'] == 263
    assert data['base_frame_provenance']['all_frames_passed'] is True
    assert all(r['frame_provenance']['base_sequence']['repaired'] for r in data['rows'])
    assert [r['id'] for r in data['rows']] == ['own', 'pending']
    text = (tmp_path/'README.md').read_text()
    assert 'Own request' in text and 'recorded-date' in text and 'Pending request' in text
    assert 'ETA projection' in text


def test_worker_forwards_isolation_and_does_not_add_primary_campaign_control(tmp_path, monkeypatch):
    calibration = tmp_path/'profile.json'
    calibration.write_text('{}')
    profile = {'pinned': True}
    manifest = dict(profile=profile, profile_selection_path=str(calibration), profile_selection_sha256=campaign.sha256_file(calibration))
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(campaign, 'profile_snapshot', lambda: profile)
    monkeypatch.setattr(campaign.batch, 'compute', lambda job: job)
    # compute installs a process-local report hook in its one-job worker.
    # Register the original before exercising it in pytest's shared process.
    monkeypatch.setattr(campaign.batch, 'refresh_progress', campaign.batch.refresh_progress)
    job = dict(run_dir=str(tmp_path/'runs'), store_dir=str(tmp_path/'store'), fixtures_dir=str(tmp_path/'fixtures'), spec={'id': 'own'})
    assert campaign.compute(job) == job
    assert 'campaign_id' not in job and 'campaign_control_path' not in job


def test_worker_rejects_profile_drift_before_solver(tmp_path, monkeypatch):
    calibration = tmp_path/'profile.json'
    calibration.write_text('{}')
    (tmp_path/'manifest.json').write_text(json.dumps(dict(profile={}, profile_selection_path=str(calibration), profile_selection_sha256='stale')))
    monkeypatch.setattr(campaign.batch, 'compute', lambda job: pytest.fail('must not solve'))
    with pytest.raises(ValueError, match='profile changed'):
        campaign.compute(dict(run_dir=str(tmp_path/'runs')))


def test_prepared_manifest_contains_twelve_distinct_keys_and_isolated_outputs():
    manifest = json.loads((campaign.DIRECTORY/'manifest.json').read_text())
    assert len(manifest['instructions']) == len(set(manifest['keys'])) == 12
    assert manifest['instance_sha256'].startswith('084ffe943b997966')
    for key in ('run_dir', 'store_dir', 'fixtures_dir'):
        assert Path(manifest[key]).parent == campaign.DIRECTORY
    assert 'campaign_id' not in manifest
