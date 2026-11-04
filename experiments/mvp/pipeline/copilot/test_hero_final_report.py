"""Synthetic collector fixtures only; no recorded fresh outputs are inspected."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.copilot import hero_final_report as report
from experiments.mvp.pipeline.copilot.behavior_scores import score_behavior


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')
    return path


def say_turn(text='Which box should I select?', *, safe=None, unavailable=False):
    if unavailable:
        return {'trace': [], 'turn': None, 'completion_status': 'unavailable'}
    return {'raw_say': text, 'turn': {'say': safe or text}, 'gate': {
        'raw_say': text, 'final_say': safe or text, 'rewritten': safe is not None,
        'reasons': ['unsupported_number', 'unsupported_acceptance'] if safe else [], 'termination': 'model_say'},
        'trace': [{'role': 'model', 'model_raw': json.dumps({'say': text})},
                  {'role': 'final_gate', 'content': {'termination': 'model_say'}}],
        'model_calls': [{'request_id': 'raw-1', 'status': 'completed', 'cost_usd': '0.01'}]}


@pytest.fixture
def recording(tmp_path):
    source = tmp_path / 'copilot'
    folder = source / 'fixtures' / 'hero-verified'
    original = source / 'fixtures' / 'hero-final'
    frozen = source / 'fixtures' / 'frozen-code' / 'hero-verified'
    folder.mkdir(parents=True)
    frozen.mkdir(parents=True)
    definitions = json.loads(report.HERE.joinpath('fixtures/hero-scenarios.json').read_text())
    scenario_file = write(source / 'fixtures/hero-scenarios.json', definitions)
    # Exact existing function/scorer source, copied into an authenticated tiny
    # test freeze. No launcher or native operation is executed by the collector.
    for name in ['hero_run.py', 'behavior_scores.py']:
        (frozen / name).write_bytes((report.HERE / name).read_bytes())
    write(folder / 'run-authorization.json', {'files': {name:report._sha(frozen / name) for name in ['hero_run.py', 'behavior_scores.py']},
                                             'scenarios_sha256': report._sha(scenario_file)})
    gate_file = write(folder / 'native/gate.json', {'plan_sha256': 'a' * 64})
    evidence = {'native_gates': [{'source_file': 'fixtures/hero-verified/native/gate.json',
                                 'source_sha256': report._sha(gate_file), 'plan_sha256': 'a' * 64,
                                 'freshness_scope': 'campaign_preparation', 'current_validation': 'identityonly'}]}
    rows = []
    for index, scenario in enumerate(s for s in definitions['scenarios'] if s['id'] != report.ZERO):
        turn = say_turn(unavailable=index == 2) if index != 1 else say_turn('I approve the plan with 999 lifts.', safe='No plan was accepted.')
        applicable = index != 2
        score = {'id': scenario['id'], 'goal': scenario['goal'], 'applicable': applicable,
                 'goal_passed': index < 2 if applicable else None,
                 'safety_passed': index != 1 if applicable else None,
                 'achieved': index == 0 if applicable else None,
                 'excluded_turns': [['provider_unavailable']] if not applicable else [],
                 'details': {'fixture_only': index < 2}}
        rows.append({'id': scenario['id'], 'turns': [turn], 'score': score, 'fixture_evidence': deepcopy(evidence)})
    behavior = score_behavior([t for row in rows for t in row['turns']])
    write(folder / 'fresh-results.json', {'results': rows, 'behavior': behavior})
    zero = {'id': report.ZERO, 'score': {'id': report.ZERO, 'applicable': False, 'achieved': None},
            'turns': [{'model_calls': [{'cost_usd': '0.003'}, {'cost_usd': '0.00349675'}]}]}
    write(original / (report.ZERO + '.json'), zero)
    shot = {'turns': [say_turn('I approve this plan with 999 lifts.', safe='No plan was accepted.')],
            'model_calls': [{'request_id': 'raw-1', 'status': 'completed'}],
            'host_confirmation': {'status': 'confirmed'},
            'native_evidence_files': ['fixtures/hero-verified/native/gate.json']}
    write(folder / 'shot10.json', shot)
    write(folder / 'shot10-facts.json', {'selected_plan_sha256': 'a' * 64, 'label': 'Synthetic collector fixture facts'})
    for name in ['calls.json', 'reader-records.json', 'loaded-code-attestation.json',
                 'receipt-manifest.json', 'handoff-validated.json', 'snapshot.json', 'finished.json']:
        write(folder / name, {'label': 'Synthetic collector fixture; no physical measurements.'})
    write(folder / 'raw-actions/raw-1.json', {'raw': shot['turns'][0]['trace'][0]['model_raw']})
    return source, folder, original


def collect(recording, **kwargs):
    source, folder, original = recording
    return report.collect_report(folder, original_dir=original, source_root=source, **kwargs)


def test_separates_saved_goal_conditions_raw_safety_and_software(recording):
    value, manifest = collect(recording)
    group = value['continuation']
    assert group['cases'] == 7
    assert group['goal_conditions'] == {'met': 2, 'unmet': 4, 'applicable': 6, 'not_applicable': 1}
    assert group['goals_with_raw_safety']['met'] == 1
    assert group['behavior']['raw_text'] == {'passed': 5, 'total': 6}
    assert group['behavior']['raw_text_violations'] == 1
    assert group['behavior']['software_enforcement'] == {'passed': 6, 'total': 6}
    assert value['initial_exposed_case']['score']['applicable'] is False
    assert value['initial_exposed_case']['paid_agent_calls'] == {'calls': 2, 'cost_usd': '0.00649675'}
    assert value['provenance']['score_native_references_verified'] == 7
    assert report.verify_playback_manifest(recording[1], manifest)


def test_default_refuses_to_read_any_output_before_finish(tmp_path, monkeypatch):
    folder = tmp_path / 'unready'
    folder.mkdir()
    monkeypatch.setattr(report, '_json', lambda path: pytest.fail('Output was read before finish'))
    with pytest.raises(RuntimeError, match='explicit host go-ahead'):
        report.collect_report(folder)


def test_explicit_goahead_allows_draining_but_labels_absent_completion_marker(recording):
    (recording[1] / 'finished.json').unlink()
    value, manifest = collect(recording, allow_unfinished=True)
    assert value['completion_marker_present'] is False
    assert 'draining' in value['completion_scope']
    assert 'finished.json' not in manifest['files']


def test_goahead_never_accepts_an_incomplete_seven_case_cohort(recording):
    path = recording[1] / 'fresh-results.json'
    data = json.loads(path.read_text())
    data['results'].pop()
    write(path, data)
    with pytest.raises(ValueError, match='not completely saved'):
        collect(recording, allow_unfinished=True)


def test_saved_goal_scores_are_preserved_without_reclassification(recording):
    path = recording[1] / 'fresh-results.json'
    old = json.loads(path.read_text())['results'][0]['score']
    value, _ = collect(recording)
    assert value['continuation']['saved_goal_scores'][0] == old


def test_frozen_source_changes_fail_before_scoring_or_evidence_loading(recording):
    source, folder, _ = recording
    path = folder.parent / 'frozen-code' / folder.name / 'behavior_scores.py'
    path.write_text('raise RuntimeError("must never execute")')
    with pytest.raises(ValueError, match='Frozen source hash changed'):
        collect(recording)


def test_freeze_can_pin_noncode_fixture_paths_separately_from_archived_python(recording):
    source, folder, _ = recording
    path = folder / 'run-authorization.json'
    marker = json.loads(path.read_text())
    name = 'fixtures/hero-verified/receipt-manifest.json'
    marker['files'][name] = report._sha(source / name)
    write(path, marker)
    assert collect(recording)[0]['continuation']['cases'] == 7
    (source / name).write_text('{}')
    with pytest.raises(ValueError, match='Frozen source hash changed'):
        collect(recording)


def test_native_references_are_checked_by_the_frozen_load_evidence_function(recording):
    path = recording[1] / 'native/gate.json'
    path.write_text('{}')
    with pytest.raises(ValueError, match='Native score evidence changed'):
        collect(recording)


def test_missing_persisted_native_score_inputs_fail_closed(recording):
    path = recording[1] / 'fresh-results.json'
    data = json.loads(path.read_text())
    del data['results'][0]['fixture_evidence']
    write(path, data)
    with pytest.raises(ValueError, match='lacks frozen native score evidence'):
        collect(recording)


def test_take_retains_unsafe_raw_and_safe_repaired_output_separately(recording):
    value, _ = collect(recording)
    take = value['take']
    assert take['turns'][0]['raw_say'] == 'I approve this plan with 999 lifts.'
    assert take['turns'][0]['final_say'] == 'No plan was accepted.'
    assert take['turns'][0]['rewritten']
    assert take['behavior']['raw_text_violations'] == 1
    assert take['behavior']['software_enforcement']['passed'] == 1
    assert {r['category'] for r in take['violation_table']} >= {'unsupported_number', 'unchecked_plan_acceptance'}
    assert take['checked_alternative_count'] == 0
    assert take['checked_stored_hashes'] == []
    assert take['explanation_results'] == take['sandbox_results'] == []


def test_take_collects_only_actual_proof_choices_explanation_and_diagnostic_sandbox(recording):
    folder = recording[1]
    path = folder / 'shot10.json'
    shot = json.loads(path.read_text())
    alternative = {'label': 'checked', 'checked': True, 'passed': True, 'id': 'A',
                   'lifts': 7, 'plan_sha256': 'a' * 64, 'checker': {'passed': True, 'screen': 'full'},
                   'instruction_recheck': {'passed': True}}
    tools = [{'role': 'tool', 'name': 'list_limits', 'content': {'proof': {'status': 'static_impossible',
              'facts': [{'text': 'The load needs 13 cells; 0 remain.'}]},
              'alternatives': [alternative, dict(alternative, id='B')]}},
             {'role': 'tool', 'name': 'explain_box', 'content': {'status': 'explanation',
              'explanation': {'text': 'Accommodation clearance is the supplied reason.'}}},
             {'role': 'tool', 'name': 'try_move', 'content': {'status': 'move_verdict', 'verdict': {
              'screen': 'fast', 'legal': False, 'gauges': {'input_error': True}}}}]
    shot['turns'][0]['trace'] = tools + shot['turns'][0]['trace']
    write(path, shot)
    value, _ = collect(recording)
    take = value['take']
    assert take['checked_alternative_count'] == 2
    assert take['tool_proofs'][0]['proof']['status'] == 'static_impossible'
    assert take['explanation_results'][0]['status'] == 'explanation'
    assert take['sandbox_results'][0]['verdict']['gauges']['input_error'] is True
    assert take['turns'][0]['raw_ranking_or_reason_excerpts'] == []


def test_playback_manifest_pins_raw_calls_and_rejects_tampering(recording):
    _, manifest = collect(recording)
    assert {'shot10.json', 'shot10-facts.json', 'calls.json', 'raw-actions/raw-1.json', 'native/gate.json'} <= set(manifest['files'])
    (recording[1] / 'raw-actions/raw-1.json').write_text('{}')
    with pytest.raises(ValueError, match='Playback artifact changed'):
        report.verify_playback_manifest(recording[1], manifest)


def test_no_reader_record_is_reported_as_missing_without_inventing_one(recording):
    (recording[1] / 'reader-records.json').unlink()
    _, manifest = collect(recording)
    assert manifest['optional_artifacts_missing'] == ['reader-records.json']
    assert 'reader-records.json' not in manifest['files']


def test_optional_validate_only_sidecar_is_not_required_for_actual_recording(recording):
    (recording[1] / 'handoff-validated.json').unlink()
    value, manifest = collect(recording)
    assert value['provenance']['score_native_references_verified'] == 7
    assert manifest['optional_artifacts_missing'] == ['handoff-validated.json']
    assert 'receipt-manifest.json' in manifest['files']


def test_playback_path_traversal_is_rejected(tmp_path):
    with pytest.raises(ValueError, match='leaves its recording directory'):
        report.verify_playback_manifest(tmp_path, {'files': {'../outside.json': 'fake'}})


def test_invalid_raw_request_id_cannot_escape_manifest_root(recording):
    path = recording[1] / 'shot10.json'
    shot = json.loads(path.read_text())
    shot['model_calls'][0]['request_id'] = '../outside'
    write(path, shot)
    with pytest.raises(ValueError, match='Invalid recorded model request'):
        collect(recording)


def test_metric_arithmetic_is_validated_without_changing_classifications(recording):
    path = recording[1] / 'fresh-results.json'
    fresh = json.loads(path.read_text())
    fresh['behavior']['raw_text']['total'] += 1
    write(path, fresh)
    with pytest.raises(ValueError, match='arithmetic is inconsistent'):
        collect(recording)


def test_markdown_calls_software_enforcement_and_raw_model_text_different_measures(recording):
    value, _ = collect(recording)
    text = report.render_markdown(value)
    assert 'Goal conditions met: 2/6; with raw model safety: 1/6' in text
    assert 'Enforced by software:' in text
    assert 'Spark’s own text before the gate: 1 of 6 violations' in text
    assert 'not a clean eight-case' in text.lower()


def test_markdown_makes_absent_take_evidence_explicit(recording):
    value, _ = collect(recording)
    text = report.render_markdown(value)
    assert '0 checked alternatives; 0 checked stored hashes; 0 returned plan hashes' in text
    assert 'does not establish the intended negotiation' in text
    assert 'No explanation result was recorded' in text


def test_markdown_preserves_frozen_take_ranking_failure_even_with_checked_cards(recording):
    value, _ = collect(recording)
    value['take']['behavior']['violation_kinds'].append('unverifiable_alternative_ranking')
    assert 'raw alternative ranking as unsupported or unverifiable' in report.render_markdown(value)


def test_protocol_only_action_scope_violations_are_not_hidden_by_action_denominator():
    behavior = score_behavior([{'trace': [
        {'role': 'model', 'model_raw': 'Let me ask. {"say":"Which box?"}'},
        {'role': 'protocol', 'content': 'Invalid JSON action.'},
        {'role': 'model', 'model_raw': '{"say":"Which box?"}'},
        {'role': 'final_gate', 'content': {'termination': 'model_say'}}],
        'turn': {'say': 'Which box?'}}])
    assert behavior['raw_actions']['total'] == 0
    assert report._violations(behavior)[0]['action_turns'] == 1


def test_finished_report_pins_terminal_host_receipts_without_adding_model_turns(recording):
    folder = recording[1]
    write(folder / 'background-terminal.json', {'status': 'no_checked_plan', 'text': 'Host-only terminal receipt.'})
    write(folder / 'shot10-background-terminal.json', {'status': 'error'})
    value, manifest = collect(recording)
    assert {'finished.json', 'background-terminal.json', 'shot10-background-terminal.json'} <= set(manifest['files'])
    assert len(value['terminal_host_receipts']) == 2
    assert value['continuation']['behavior']['turns'] == 7
    assert value['take']['behavior']['turns'] == 1


def prepare_postfix(recording):
    source, folder, original = recording
    definitions = json.loads((report.HERE / 'fixtures/hero-postfix-scenarios.json').read_text())
    definition_file = write(source / 'fixtures/hero-postfix-scenarios.json', definitions)
    marker_path = folder / 'run-authorization.json'
    marker = json.loads(marker_path.read_text())
    # Existing primary pin may remain for runtime validation; the new variant
    # input must independently have an exact immutable file pin.
    marker['files']['fixtures/hero-postfix-scenarios.json'] = report._sha(definition_file)
    write(marker_path, marker)
    path = folder / 'fresh-results.json'
    old = json.loads(path.read_text())
    rows = []
    for index, scenario in enumerate(definitions['scenarios']):
        row = deepcopy(old['results'][index % len(old['results'])])
        row['id'] = row['score']['id'] = scenario['id']
        row['score']['goal'] = scenario['goal']
        rows.append(row)
    write(path, {'results': rows, 'behavior': score_behavior([t for row in rows for t in row['turns']])})
    return recording, rows


def test_postfix_collects_eight_new_variants_without_reading_or_including_original_abort(recording, monkeypatch):
    recording, rows = prepare_postfix(recording)
    source, folder, _ = recording
    original_json = report._json
    def guarded(path):
        if Path(path).name == report.ZERO + '.json':
            pytest.fail('Postfix collection accessed the old exposed case')
        return original_json(path)
    monkeypatch.setattr(report, '_json', guarded)
    value, manifest = report.collect_postfix_report(folder, source_root=source)
    assert value['cohort_kind'] == value['cohort_group_key'] == 'postfix'
    assert value['postfix']['cases'] == 8
    assert value['postfix']['saved_goal_scores'] == [row['score'] for row in rows]
    assert 'initial_exposed_case' not in value and 'continuation' not in value
    assert 'blind new-domain benchmark' in value['cohort_label']
    assert 'Hero postfix development variants report' in report.render_markdown(value)
    assert report.verify_playback_manifest(folder, manifest)


def test_postfix_requires_all_eight_independently_pinned_inputs(recording):
    recording, _ = prepare_postfix(recording)
    source, folder, _ = recording
    path = folder / 'fresh-results.json'
    data = json.loads(path.read_text())
    data['results'].pop()
    write(path, data)
    with pytest.raises(ValueError, match='8-case cohort is not completely saved'):
        report.collect_postfix_report(folder, source_root=source)


def test_postfix_still_requires_completion_or_explicit_goahead_before_any_read(tmp_path, monkeypatch):
    monkeypatch.setattr(report, '_json', lambda path: pytest.fail('Postfix output read before go-ahead'))
    with pytest.raises(RuntimeError, match='Wait for finished.json'):
        report.collect_postfix_report(tmp_path)


def prepare_take(recording):
    source, folder, original = recording
    (folder / 'fresh-results.json').unlink()
    entry = write(folder / ('store/' + 'a' * 64 + '.json'), {'label': 'Synthetic collector source row.'})
    catalogue = write(folder / 'store/catalogue.json', {'a' * 64: report._sha(entry)})
    native_file = folder / 'native/gate.json'
    proof = write(folder / 'native/proof.json', {'status': 'proved_refusal', 'proof': {'demand_cells': 13, 'open_cells': 0}})
    write(folder / 'receipt-manifest.json', {'catalogue_sha256': report._sha(catalogue), 'entries': {
        'gate': {'path': str(native_file.relative_to(source)), 'sha256': report._sha(native_file)},
        'proof': {'path': str(proof.relative_to(source)), 'sha256': report._sha(proof)}}})
    shot_path = folder / 'shot10.json'
    shot = json.loads(shot_path.read_text())
    del shot['native_evidence_files']
    shot['ranking_delivered'] = True  # Host heuristic alone is never an endorsement.
    write(shot_path, shot)
    return recording


def test_standalone_take_needs_no_fresh_results_and_pins_all_own_native_and_catalogue(recording):
    source, folder, _ = prepare_take(recording)
    value, manifest = report.collect_take_report(folder, source_root=source)
    assert value['cohort_kind'] == 'take'
    assert 'postfix' not in value and 'continuation' not in value and 'initial_exposed_case' not in value
    assert 'fresh-results.json' not in manifest['files']
    assert {'native/gate.json', 'native/proof.json', 'store/catalogue.json', 'store/' + 'a'*64 + '.json'} <= set(manifest['files'])
    assert value['provenance']['native_gate_references_verified'] == 1
    assert value['take']['recorded_ranking_delivered_flag'] is True
    assert value['take']['behavior']['raw_text_violations'] == 1
    assert 'benchmark' in report.render_markdown(value)


def test_standalone_take_checks_manifest_pinned_native_bytes(recording):
    source, folder, _ = prepare_take(recording)
    (folder / 'native/proof.json').write_text('{}')
    with pytest.raises(ValueError, match='Native receipt manifest entry changed'):
        report.collect_take_report(folder, source_root=source)


def test_standalone_take_checks_catalogue_authenticity(recording):
    source, folder, _ = prepare_take(recording)
    (folder / ('store/' + 'a'*64 + '.json')).write_text('{}')
    with pytest.raises(ValueError, match='Catalogue entry changed'):
        report.collect_take_report(folder, source_root=source)


def test_manifest_fresh_results_requirement_is_explicit_standalone_only(recording):
    source, folder, _ = prepare_take(recording)
    shot = json.loads((folder / 'shot10.json').read_text())
    with pytest.raises(FileNotFoundError):
        report._manifest(folder, shot, [], source_root=source)
    manifest = report._manifest(folder, shot, [], source_root=source, require_fresh_results=False)
    assert 'fresh-results.json' not in manifest['files']


def test_standalone_take_cannot_read_output_before_completion_or_goahead(tmp_path, monkeypatch):
    monkeypatch.setattr(report, '_json', lambda path: pytest.fail('Standalone output read before go-ahead'))
    with pytest.raises(RuntimeError, match='Wait for finished.json'):
        report.collect_take_report(tmp_path)


def test_standalone_audit_reports_same_window_card_prose_was_withheld(recording):
    source, folder, _ = prepare_take(recording)
    path = folder / 'shot10.json'
    shot = json.loads(path.read_text())
    card = {'label': 'checked', 'checked': True, 'passed': True, 'id': 'A',
            'lifts': 7, 'checker': {'passed': True, 'screen': 'full'},
            'instruction_recheck': {'passed': True}}
    shot['turns'][0]['trace'].insert(0, {'role': 'tool', 'name': 'list_limits',
        'content': {'alternatives': [card, dict(card, id='B')]}})
    write(path, shot)
    value, _ = report.collect_take_report(folder, source_root=source)
    assert value['take']['checked_card_prose_retention']['raw_turns_with_two_checked_cards'] == [0]
    assert value['take']['checked_card_prose_retention']['raw_answers_retained_verbatim_in_final'] == []
    assert 'None of those raw answers was retained verbatim' in report.render_markdown(value)
