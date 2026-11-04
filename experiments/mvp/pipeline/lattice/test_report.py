import json

from . import report


def test_engine_seconds_fall_back_to_recorded_wall_time_without_inventing_unknowns(tmp_path):
    base = dict(id='base', status='checked_plan', purpose='lattice', wall_s=900,
                sequence_repair_wall_s=45.5)
    source = tmp_path/'base.json'
    source.write_text(json.dumps(base))
    data = report.progress(run_dir=tmp_path)
    assert data['rows'][0]['engine_wall_s'] == 900
    assert data['rows'][0]['sequence_repair_wall_s'] == 45.5
    assert '900.00' in report.render_table(data)
    assert report.collect({'wall_s': 900, 'engine_wall_s': None})['engine_wall_s'] is None
    assert report.collect({})['engine_wall_s'] is None
    assert json.loads(source.read_text()) == base


def test_retry_gate_cost_is_reported_without_replacing_original_measured_time(tmp_path):
    source = tmp_path/'retry.json'
    recorded = dict(id='retry', status='checked_plan', purpose='lattice',
                    engine_wall_s=900.0, total_wall_s=1413.94, release_wall_s=511.25)
    source.write_text(json.dumps(recorded))
    data = report.progress(run_dir=tmp_path)
    table = report.render_table(data)
    assert '| Original total seconds | Retry gate seconds |' in table
    assert '| 900.00 | 1413.94 | 511.25 |' in table
    assert json.loads(source.read_text()) == recorded


def test_historical_timeout_allowance_is_not_a_measured_search_duration():
    row = dict(id='base', purpose='baseline', wall_s=900, native_search_wall_s=900,
               budget_status='timeout_with_checked_incumbent',
               profile={'engine_timeout_s': 900})
    measured = report.collect(row)
    assert measured['engine_wall_s'] is None
    assert measured['engine_allowance_s'] == 900
    assert report.collect(dict(row, engine_wall_s=902.3))['engine_wall_s'] == 902.3


def test_historical_pool_retry_with_mixed_clock_scope_is_unknown():
    row = dict(plan_producer='lattice.standard_plan_pool', release_wall_s=130,
               admission={'waited_s': 120}, gate={'wall_s': 7})
    measured = report.collect(row)
    assert measured['release_wall_s'] is None
    assert measured['historical_release_including_admission_s'] == 130
    assert measured['native_gate_wall_s'] == 7


def test_active_campaign_supplements_are_pending_without_rewriting_original_manifest(tmp_path,monkeypatch):
    here=tmp_path/'pipeline'/'lattice';here.mkdir(parents=True)
    monkeypatch.setattr(report,'HERE',here)
    (here/'runs').mkdir()
    original=dict(instructions=[dict(id='original',purpose='lattice')])
    (here/'manifest.json').write_text(json.dumps(original))
    original_bytes=(here/'manifest.json').read_bytes()
    (here/'campaign-control.json').write_text(json.dumps(dict(active_campaign='current-v5')))
    active=here/'screening/current-v5/manifest.json';active.parent.mkdir(parents=True)
    active.write_text(json.dumps(dict(campaign_id='current-v5',instructions=original['instructions']+
                                    [dict(id='supplement',purpose='lattice')])) )
    assert report.active_manifest_path()==active
    data=report.progress()
    assert [(r['id'],r['status']) for r in data['rows']]==[('original','pending'),('supplement','pending')]
    assert (here/'manifest.json').read_bytes()==original_bytes
    active.write_text(json.dumps(dict(campaign_id='wrong')))
    assert report.active_manifest_path()==here/'manifest.json'


def test_readme_rule_table_updates_one_marker_with_counts_dates_and_evidence(tmp_path):
    runs=tmp_path/'runs';runs.mkdir()
    source=runs/'result.json'
    recorded=dict(id='rule',sentences=['Keep DG cargo away.'],status='checked_plan',purpose='lattice',
                  lifts=70,engine_wall_s=900,computed_utc='2026-11-02T04:00:00+00:00')
    source.write_text(json.dumps(recorded))
    readme=tmp_path/'README.md';readme.write_text('# Existing\n\nKeep this text.\n')
    report.update(run_dir=runs,output_dir=tmp_path)
    first=readme.read_text()
    assert '1 stored instructions, 0 stored bases, 0 proved refusals' in first
    assert '| Computed UTC |' in first and recorded['computed_utc'] in first
    assert '| 70.00 |' in first and '| 900.00 |' in first
    recorded['status']='gate_failed';source.write_text(json.dumps(recorded))
    report.update(run_dir=runs,output_dir=tmp_path)
    second=readme.read_text()
    assert second.count('<!-- RULE_STATUS_START -->')==second.count('<!-- RULE_STATUS_END -->')==1
    assert second.count('| Instruction | Status |')==1
    assert '0 stored instructions' in second and '| gate_failed |' in second
    assert '| checked_plan |' not in second and second.startswith('# Existing\n\nKeep this text.')
    assert 'Computed UTC' in (tmp_path/'RULES.md').read_text()
    assert json.loads(source.read_text())==recorded


def test_frame_claims_carry_selected_and_baseline_repair_labels(tmp_path):
    row = dict(id='repaired', status='checked_plan', plan_producer='lattice.sequence',
               base_plan={'id': 'base.lattice-block-sequence'},
               gate={'gauges': {'n_frames': 146, 'passed': True, 'all_frames': True}})
    collected = report.collect(row)
    assert collected['frame_provenance']['native_engine_output'] is False
    table = report.render_table({'rows': [collected]})
    assert 'Crane frames walked on this plan' in table
    assert '| crane sequence repaired | crane sequence repaired | 146 |' in table


def test_hero_and_backup_marker_explicitly_label_repaired_crane_sequences(tmp_path):
    readme = tmp_path/'README.md'
    readme.write_text('<!-- HERO_STATUS_START -->old<!-- HERO_STATUS_END -->')
    hero = dict(instance_id='hero', boxes=87, lifts=66, frames_checked=144,
                computed_utc='recorded', path='source', why='Synthetic', plan_producer='lattice.sequence')
    backup = dict(hero, instance_id='backup', boxes=163, lifts=115, frames_checked=263,
                  plan_producer='lattice.recover_candidate')
    (tmp_path/'hero.json').write_text(json.dumps(dict(hero=hero, backup=backup)))
    report._refresh_hero_readme(tmp_path)
    assert readme.read_text().count('crane sequence repaired') == 2
    assert '144 crane frames walked on this plan and passed' in readme.read_text()
    assert '263 crane frames walked on this plan and passed' in readme.read_text()
