"""Collect completed hero artifacts offline; never call a model or native tool.

Fresh goal scores remain exactly those saved by the frozen runner. The take's
raw behavior is evaluated only with its hash-pinned frozen behavior scorer.
"""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import types

HERE = Path(__file__).resolve().parent
ZERO = 'hero_zero_capacity_choices'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json(path):
    return json.loads(Path(path).read_text())


def _inside(root, name):
    path = Path(root) / name
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('Artifact leaves its recording directory: ' + str(name))
    return path


def _frozen_sources(folder, source_root):
    marker = _json(folder / 'run-authorization.json')
    frozen = folder.parent / 'frozen-code' / folder.name
    for name, expected in marker['files'].items():
        path = _inside(source_root if name.startswith('fixtures/') else frozen, name)
        if _sha(path) != expected:
            raise ValueError('Frozen source hash changed: ' + name)
    return marker, frozen


def _frozen_evidence_loader(frozen):
    # Execute only this hash-verified function's original AST, not the runner's
    # imports or launcher. Its dependencies are JSON, path operations and hashes.
    path = frozen / 'hero_run.py'
    nodes = [node for node in ast.parse(path.read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name == 'load_evidence']
    if len(nodes) != 1:
        raise ValueError('Frozen runner lacks its evidence loader')
    namespace = {'HERE': HERE, 'sha256_file': _sha, 'json': json}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['load_evidence']


def _frozen_behavior(frozen):
    path = frozen / 'behavior_scores.py'
    module = types.ModuleType('_frozen_hero_report_behavior')
    module.__file__ = str(path)
    exec(compile(path.read_text(), str(path), 'exec'), module.__dict__)
    return module


def _counts(scores, key):
    applicable = [row for row in scores if row.get('applicable') is True]
    return {'met': sum(row.get(key) is True for row in applicable),
            'unmet': sum(row.get(key) is False for row in applicable),
            'applicable': len(applicable), 'not_applicable': len(scores) - len(applicable)}


def _violations(behavior):
    table = {}
    for row in behavior.get('results', []):
        if row.get('applicable') is not True:
            continue
        for kind in {v['kind'] for v in row.get('violations', [])}:
            entry = table.setdefault(kind, {'category': kind, 'text_turns': 0, 'action_turns': 0})
            scopes = {v.get('scope') for v in row.get('violations', []) if v.get('kind') == kind}
            entry['text_turns'] += 'text' in scopes
            entry['action_turns'] += 'action' in scopes
    return [table[key] for key in sorted(table)]


def _behavior_consistent(behavior):
    """Check recorded arithmetic, preserving every frozen classification."""
    rows = behavior.get('results', [])
    text = [r for r in rows if r.get('raw_text_applicable') is True]
    action = [r for r in rows if r.get('raw_actions_applicable') is True]
    enforced = [r for r in rows if r.get('enforced_by_software') is not None]
    if (behavior.get('raw_text') != {'passed': sum(r['raw_text_passed'] for r in text), 'total': len(text)}
            or behavior.get('raw_actions') != {'passed': sum(r['raw_actions_passed'] for r in action), 'total': len(action)}
            or behavior.get('software_enforcement') != {'passed': sum(r['enforced_by_software'] for r in enforced), 'total': len(enforced)}):
        raise ValueError('Saved raw/software metric arithmetic is inconsistent')
    return True


def _call_cost(turns):
    calls = [call for row in turns for call in row.get('model_calls', [])]
    return {'calls': len(calls), 'cost_usd': str(sum((Decimal(str(c.get('cost_usd', 0))) for c in calls), Decimal(0)))}


def _checked(result):
    checker, audit = result.get('checker') or {}, result.get('instruction_recheck') or {}
    return (result.get('passed') is True and checker.get('passed') is True
            and checker.get('screen') == 'full' and audit.get('passed') is True)


def _take_audit(shot, behavior):
    turns, proofs, alternatives, stored_hashes, returned, explain, sandbox = [], [], {}, [], [], [], []
    for index, row in enumerate(shot.get('turns', [])):
        gate = row.get('gate') or next((e.get('content', {}) for e in reversed(row.get('trace', []))
                                      if e.get('role') == 'final_gate'), {})
        raw = row.get('raw_say', gate.get('raw_say'))
        output = row.get('turn') or {}
        final = output.get('say', gate.get('final_say'))
        excerpts = [part.strip() for part in re.split(r'(?<=[.!?])\s+|\n+', raw or '')
                    if re.search(r'\b(?:recommend|prefer|cheaper|fewer|same|equal|because|trade.off)\b', part, re.I)]
        turns.append({'turn_index': index, 'raw_say': raw, 'final_say': final,
                      'rewritten': gate.get('rewritten'), 'gate_reasons': gate.get('reasons', []),
                      'termination': gate.get('termination'), 'raw_ranking_or_reason_excerpts': excerpts,
                      'label': 'Verbatim observed Spark prose and software output; excerpts are not truth endorsements.'})
        if output.get('plan_sha256'):
            returned.append({'turn_index': index, 'plan_sha256': output['plan_sha256']})
        for event_index, event in enumerate(row.get('trace', [])):
            if event.get('role') != 'tool' or not isinstance(event.get('content'), dict):
                continue
            name, result = event.get('name'), event['content']
            pointer = f'/turns/{index}/trace/{event_index}/content'
            proof = result.get('proof', result.get('refusal'))
            if isinstance(proof, dict):
                proofs.append({'source_pointer': pointer, 'proof': deepcopy(proof),
                               'label': 'Actual tool proof; arithmetic is not inferred from the scenario title.'})
            for alternative in result.get('alternatives', []):
                if isinstance(alternative, dict) and alternative.get('checked') is True and alternative.get('label') == 'checked' and _checked(alternative):
                    identifier = alternative.get('id')
                    alternatives[str(identifier)] = {'source_pointer': pointer, **{key:deepcopy(alternative.get(key)) for key in
                        ('id', 'label', 'checked', 'passed', 'plan_sha256', 'lifts', 'lifts_label', 'cost_reference', 'sentence', 'gauges')}}
            if name == 'plan_lookup' and _checked(result) and result.get('plan_sha256'):
                stored_hashes.append({'source_pointer': pointer, 'plan_sha256': result['plan_sha256'],
                                      'mode': result.get('mode'), 'computed_utc': result.get('computed_utc')})
            if name == 'explain_box':
                explain.append({'source_pointer': pointer, 'status': result.get('status'),
                                'explanation': deepcopy(result.get('explanation')), 'text': result.get('text'),
                                'checker': deepcopy(result.get('checker')), 'instruction_recheck': deepcopy(result.get('instruction_recheck'))})
            if name == 'try_move':
                sandbox.append({'source_pointer': pointer, 'status': result.get('status'),
                                'box': result.get('box'), 'slot': result.get('slot'),
                                'verdict': deepcopy(result.get('verdict')), 'text': result.get('text'),
                                'label': 'Actual returned sandbox result; invalid-input and screen-only results are not native full passes.'})
    return {'host_confirmation': deepcopy(shot.get('host_confirmation')),
            'recorded_ranking_delivered_flag': shot.get('ranking_delivered'),
            'ranking_flag_scope': 'Recorded host heuristic only; inspect actual raw/final prose and checked cards independently.',
            'tool_proofs': proofs, 'checked_alternative_count': len(alternatives),
            'checked_alternatives': list(alternatives.values()), 'checked_stored_hashes': stored_hashes,
            'returned_plan_hashes': returned, 'explanation_results': explain,
            'sandbox_results': sandbox, 'turns': turns, 'behavior': behavior,
            'violation_table': _violations(behavior),
            'label': 'Observation audit of the separately recorded take; not a rerun of a fresh evaluation case.'}


def _catalogue_files(folder):
    path = _inside(folder, 'store/catalogue.json')
    expected = _json(folder / 'receipt-manifest.json').get('catalogue_sha256')
    if _sha(path) != expected:
        raise ValueError('Source catalogue differs from the pinned receipt manifest')
    names = {'store/catalogue.json'}
    for key, digest in _json(path).items():
        if not re.fullmatch(r'[0-9a-f]{64}', key):
            raise ValueError('Invalid source catalogue key')
        name = 'store/' + key + '.json'
        if _sha(_inside(folder, name)) != digest:
            raise ValueError('Catalogue entry changed: ' + key)
        names.add(name)
    return names


def _manifest(folder, shot, evidence_rows, *, source_root, require_fresh_results=True):
    names = {'shot10.json', 'shot10-facts.json', 'calls.json',
             'run-authorization.json', 'loaded-code-attestation.json', 'receipt-manifest.json',
             'snapshot.json'}
    if require_fresh_results:
        names.add('fresh-results.json')
    else:
        names.update(str(path.relative_to(folder)) for path in (folder / 'native').glob('*.json'))
        names.update(_catalogue_files(folder))
    optional_missing = []
    for name in ('reader-records.json', 'handoff-validated.json'):
        if (folder / name).exists():
            names.add(name)
        else:
            optional_missing.append(name)
    if (folder / 'finished.json').exists():
        names.add('finished.json')
    names.update(path.name for path in folder.glob('*terminal.json'))
    for call in shot.get('model_calls', []):
        if call.get('status') != 'completed':
            continue
        identifier = call.get('request_id')
        if not isinstance(identifier, str) or not re.fullmatch(r'[\w.-]+', identifier):
            raise ValueError('Invalid recorded model request identifier')
        names.add('raw-actions/' + identifier + '.json')
    for name in shot.get('native_evidence_files', []):
        path = source_root / name
        if not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError('Take native evidence is outside its recording directory')
        names.add(str(path.relative_to(folder)))
    for row in evidence_rows:
        for event in row.get('native_gates', []):
            path = source_root / event['source_file']
            if not path.resolve().is_relative_to(folder.resolve()):
                raise ValueError('Goal native evidence is outside its recording directory')
            names.add(str(path.relative_to(folder)))
    files = {name:_sha(_inside(folder, name)) for name in sorted(names)}
    return {'schema_version': 'stowlm.copilot-p22-playback/1', 'artifact_root': str(folder),
            'files': files, 'optional_artifacts_missing': optional_missing,
            'conversation': 'shot10.json', 'facts': 'shot10-facts.json',
            'calls': 'calls.json', 'mode': 'recorded', 'synthetic': True,
            'label': 'Offline playback of recorded Spark and real-tool evidence; no new checker, model call or plan installation.'}


def verify_playback_manifest(folder, manifest):
    for name, expected in manifest['files'].items():
        if _sha(_inside(folder, name)) != expected:
            raise ValueError('Playback artifact changed: ' + name)
    return True


def collect_report(verified_dir, *, original_dir=HERE / 'fixtures' / 'hero-final',
                   source_root=HERE, allow_unfinished=False,
                   cohort_kind='continuation', definitions_file=None):
    """Collect only after completion, or an explicit host go-ahead during drain.

    ``allow_unfinished`` still requires the complete frozen cohort and take.
    It never starts or polls a job. Postfix variants are collected separately,
    without reading or including the exposed original case.
    """
    if cohort_kind not in ('continuation', 'postfix'):
        raise ValueError('Unknown hero report cohort')
    postfix = cohort_kind == 'postfix'
    case_count = 8 if postfix else 7
    group_key = 'postfix' if postfix else 'continuation'
    folder, original_dir, source_root = Path(verified_dir).resolve(), Path(original_dir).resolve(), Path(source_root).resolve()
    completed = (folder / 'finished.json').exists()
    if not completed and not allow_unfinished:
        raise RuntimeError('Wait for finished.json or an explicit host go-ahead before reading outputs')
    marker, frozen = _frozen_sources(folder, source_root)
    fresh = _json(folder / 'fresh-results.json')
    definitions_file = (Path(definitions_file).resolve() if definitions_file is not None else
                        source_root / 'fixtures' / ('hero-postfix-scenarios.json' if postfix else 'hero-scenarios.json'))
    definition_name = str(definitions_file.relative_to(source_root))
    definition_sha = _sha(definitions_file)
    definitions = _json(definitions_file)
    if definition_sha not in (marker.get('scenarios_sha256'), marker['files'].get(definition_name)):
        raise ValueError('Scenario definitions differ from the recording freeze')
    expected = {row['id'] for row in definitions['scenarios']} - (set() if postfix else {ZERO})
    if postfix and ZERO in expected:
        raise ValueError('The postfix cohort must contain new variants, not the exposed original case')
    rows = fresh['results']
    if len(rows) != case_count or {row['id'] for row in rows} != expected or len(expected) != case_count:
        raise ValueError('The frozen ' + str(case_count) + '-case cohort is not completely saved')
    loader = _frozen_evidence_loader(frozen)
    evidence = []
    for row in rows:
        if row['score']['id'] != row['id']:
            raise ValueError('Case and saved score identity differ')
        supplied = row.get('fixture_evidence')
        if not isinstance(supplied, dict):
            raise ValueError('Saved case lacks frozen native score evidence')
        loader(supplied, root=source_root)
        evidence.append(supplied)
    exposed = None
    if not postfix:
        old_path = original_dir / (ZERO + '.json')
        exposed = _json(old_path)
        if exposed['score'].get('applicable') is not False:
            raise ValueError('The exposed initial case no longer has its preserved N/A score')
    behavior = deepcopy(fresh['behavior'])
    _behavior_consistent(behavior)
    shot, facts = _json(folder / 'shot10.json'), _json(folder / 'shot10-facts.json')
    scorer = _frozen_behavior(frozen)
    take_behavior = scorer.score_behavior(shot.get('turns', []))
    _behavior_consistent(take_behavior)
    scores = [deepcopy(row['score']) for row in rows]
    report = {'schema_version': 'stowlm.copilot-hero-final-report/1',
              'cohort_kind': cohort_kind, 'cohort_group_key': group_key,
              'cohort_label': ('Eight newly frozen first-attempt development variants after the infrastructure fix. '
                  'This is not a blind new-domain benchmark. The original exposed N/A case and failed seven-case cohort remain separate and are not included.' if postfix else
                  'Seven provider-first continuation cases after the loaded-runtime correction; one exposed initial case remains N/A. Not a clean eight-case campaign. Later infrastructure fixes and variant cohorts are separate.'),
              'completion_marker_present': completed,
              'completion_scope': 'Finished recording' if completed else f'Explicit host go-ahead: all {case_count} cases and take saved; native worker may still be draining.',
              'completion_record': _json(folder / 'finished.json') if completed else None,
              'terminal_host_receipts': [{'source_file': path.name, 'source_sha256': _sha(path),
                  'receipt': _json(path), 'label': 'Host-only terminal receipt; never added to model goal or raw-text scoring.'}
                  for path in sorted(folder.glob('*terminal.json'))],
              group_key: {'cases': case_count, 'goal_conditions': _counts(scores, 'goal_passed'),
                  'goals_with_raw_safety': _counts(scores, 'achieved'),
                  'raw_safety_of_completed_cases': _counts(scores, 'safety_passed'),
                  'saved_goal_scores': scores, 'behavior': behavior,
                  'violation_table': _violations(behavior)},
              'take': _take_audit(shot, take_behavior),
              'recorded_native_facts': facts,
              'provenance': {'run_authorization_sha256': _sha(folder / 'run-authorization.json'),
                  'frozen_evidence_loader_sha256': marker['files']['hero_run.py'],
                  'frozen_behavior_scorer_sha256': marker['files']['behavior_scores.py'],
                  'scenario_definitions_file': definition_name, 'scenario_definitions_sha256': definition_sha,
                  'loaded_code_attestation': _json(folder / 'loaded-code-attestation.json'),
                  'native_receipt_manifest': _json(folder / 'receipt-manifest.json'),
                  'score_native_references_verified': len(rows),
                  'label': 'Saved goal scores preserved; frozen loader verifies native references. Take safety uses exact frozen criteria offline.'}}
    if exposed is not None:
        report['initial_exposed_case'] = {'id': ZERO, 'source_file': str(old_path), 'source_sha256': _sha(old_path),
            'score': deepcopy(exposed['score']), 'paid_agent_calls': _call_cost(exposed.get('turns', [])),
            'label': 'Preserved technical abort/N/A; no fresh rerun.'}
    manifest = _manifest(folder, shot, evidence, source_root=source_root)
    verify_playback_manifest(folder, manifest)
    return report, manifest


def collect_postfix_report(postfix_dir, *, source_root=HERE, allow_unfinished=False, definitions_file=None):
    """Collect only the separately frozen eight development variants and take."""
    return collect_report(postfix_dir, source_root=source_root, allow_unfinished=allow_unfinished,
                          cohort_kind='postfix', definitions_file=definitions_file)


def _take_windows(shot, scorer):
    windows = []
    for ti, turn in enumerate(shot.get('turns', [])):
        evidence = scorer._Evidence()
        for ei, entry in enumerate(turn.get('trace', [])):
            if entry.get('role') == 'tool' and isinstance(entry.get('content'), dict):
                evidence.add(entry.get('name'), entry['content'])
            elif entry.get('role') == 'model':
                try:
                    action = scorer._parse(entry.get('model_raw'))
                except (ValueError, TypeError):
                    continue
                if 'say' not in action:
                    continue
                cards = {a['id']: {key:deepcopy(a.get(key)) for key in
                         ('id', 'label', 'checked', 'passed', 'lifts', 'lifts_label', 'plan_sha256', 'cost_reference')}
                         for result in evidence.results for a in result.get('alternatives', [])
                         if isinstance(a, dict) and isinstance(a.get('id'), str)
                         and a.get('label') == 'checked' and a.get('checked') is True and _checked(a)}
                raw = action['say']
                final = (turn.get('turn') or {}).get('say', '')
                windows.append({'turn_index': ti, 'model_event_index': ei,
                    'current_checked_cards': list(cards.values()),
                    'current_statuses': [row.get('status') for row in evidence.results],
                    'raw_say': raw, 'raw_say_retained_in_final': bool(raw.strip() and raw.strip() in final),
                    'frozen_text_violation_kinds': scorer._text_violations(raw, evidence),
                    'label': 'Exact frozen-scorer evidence window; card presence and host heuristic do not alone establish a valid ranking.'})
    return windows


def collect_take_report(take_dir, *, source_root=HERE, allow_unfinished=False):
    """Collect a separate scripted-planner demonstration, with no cohort data."""
    folder, source_root = Path(take_dir).resolve(), Path(source_root).resolve()
    completed = (folder / 'finished.json').exists()
    if not completed and not allow_unfinished:
        raise RuntimeError('Wait for finished.json or an explicit host go-ahead before reading outputs')
    marker, frozen = _frozen_sources(folder, source_root)
    receipt_manifest = _json(folder / 'receipt-manifest.json')
    listed = set()
    for entry in receipt_manifest.get('entries', {}).values():
        path = _inside(source_root, entry['path'])
        if not path.resolve().is_relative_to(folder):
            raise ValueError('Native receipt manifest leaves this take directory')
        if _sha(path) != entry['sha256']:
            raise ValueError('Native receipt manifest entry changed: ' + entry['path'])
        listed.add(path.resolve())
    native, sources = [], []
    for path in sorted((folder / 'native').glob('*.json')):
        raw = _json(path)
        gate = raw.get('gate', raw)
        digest = gate.get('plan_sha256')
        source = {'source_file': str(path.relative_to(source_root)), 'source_sha256': _sha(path),
                  'listed_in_host_manifest': path.resolve() in listed,
                  'label': 'Recorded campaign preparation artifact; no numerical check is executed by this collector.'}
        if isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest):
            native.append(dict(source, plan_sha256=digest))
        sources.append(source)
    _frozen_evidence_loader(frozen)({'native_gates': native}, root=source_root)
    shot, facts = _json(folder / 'shot10.json'), _json(folder / 'shot10-facts.json')
    scorer = _frozen_behavior(frozen)
    behavior = scorer.score_behavior(shot.get('turns', []))
    _behavior_consistent(behavior)
    take = _take_audit(shot, behavior)
    take['raw_say_tool_windows'] = _take_windows(shot, scorer)
    card_windows = [row for row in take['raw_say_tool_windows']
                    if len(row['current_checked_cards']) >= 2]
    take['checked_card_prose_retention'] = {
        'raw_turns_with_two_checked_cards': [row['turn_index'] for row in card_windows],
        'raw_answers_retained_verbatim_in_final': [row['turn_index'] for row in card_windows
                                                  if row['raw_say_retained_in_final']],
        'label': 'Literal prose-retention observations, not a ranking-quality score. Inspect paired raw/final prose.'}
    take['scripted_planner_messages'] = [row.get('message') for row in shot.get('turns', [])]
    report = {'schema_version': 'stowlm.copilot-hero-final-report/1', 'cohort_kind': 'take',
        'cohort_label': 'Separate recorded demonstration with scripted planner clarifications and actual model/tool decisions. No benchmark case or cohort is rerun or included.',
        'completion_marker_present': completed, 'completion_record': _json(folder / 'finished.json') if completed else None,
        'take': take, 'recorded_native_facts': facts,
        'terminal_host_receipts': [{'source_file': path.name, 'source_sha256': _sha(path),
            'receipt': _json(path), 'label': 'Host-only terminal receipt, outside model scoring.'}
            for path in sorted(folder.glob('*terminal.json'))],
        'provenance': {'run_authorization_sha256': _sha(folder / 'run-authorization.json'),
            'frozen_evidence_loader_sha256': marker['files']['hero_run.py'],
            'frozen_behavior_scorer_sha256': marker['files']['behavior_scores.py'],
            'loaded_code_attestation': _json(folder / 'loaded-code-attestation.json'),
            'native_receipt_manifest': receipt_manifest, 'native_sources': sources,
            'native_gate_references_verified': len(native),
            'label': 'No scenario grades. Exact frozen raw-behavior checks and immutable native source references only.'}}
    manifest = _manifest(folder, shot, [], source_root=source_root, require_fresh_results=False)
    verify_playback_manifest(folder, manifest)
    return report, manifest


def render_markdown(report):
    if report.get('cohort_kind') == 'take':
        group = report['take']
        lines = ['# Hero standalone recorded take report', '', report['cohort_label'], '']
    else:
        key = report.get('cohort_group_key', 'continuation')
        group = report[key]
        goals, safe = group['goal_conditions'], group['goals_with_raw_safety']
        lines = ['# Hero ' + ('postfix development variants' if key == 'postfix' else 'continuation') + ' report', '', report['cohort_label'], '',
                 f"Goal conditions met: {goals['met']}/{goals['applicable']}; with raw model safety: {safe['met']}/{safe['applicable']}. {goals['not_applicable']} {key} cases N/A.", '',
                 *group['behavior'].get('report_lines', []), '']
        if 'initial_exposed_case' in report:
            lines.extend(['The exposed initial zero-capacity case remains N/A and is reported separately.', ''])
    lines.extend(['| Recorded raw violation category | Text-scope turns | Action-scope turns |', '|---|---:|---:|'])
    lines.extend(f"| {r['category']} | {r['text_turns']} | {r['action_turns']} |" for r in group['violation_table'])
    if not group['violation_table']:
        lines.append('| None recorded | 0 | 0 |')
    take = report['take']
    stored = {row['plan_sha256'] for row in take['checked_stored_hashes']}
    returned = {row['plan_sha256'] for row in take['returned_plan_hashes']}
    lines.extend(['', f"Separate recorded take: {take['checked_alternative_count']} checked alternatives; {len(stored)} checked stored hashes; {len(returned)} returned plan hashes.", ''])
    retention = take.get('checked_card_prose_retention', {})
    if retention.get('raw_turns_with_two_checked_cards') and not retention.get('raw_answers_retained_verbatim_in_final'):
        lines.extend(['None of those raw answers was retained verbatim in the final output. Inspect paired raw/final prose before claiming a delivered comparison.', ''])
    if take['checked_alternative_count'] < 2 or not stored or not returned:
        lines.extend(['This take does not establish the intended negotiation and stored-plan workflow.', ''])
    if any('ranking' in kind for kind in take['behavior'].get('violation_kinds', [])):
        lines.extend(['The frozen scorer flags this take’s raw alternative ranking as unsupported or unverifiable. Checked tool cards do not erase that model-text failure; raw and repaired prose remain separate.', ''])
    explain_statuses = [str(row.get('status')) for row in take['explanation_results']]
    sandbox_statuses = [str(row.get('status')) for row in take['sandbox_results']]
    lines.append('Explanation tool statuses: ' + ', '.join(explain_statuses) + '.' if explain_statuses else 'No explanation result was recorded.')
    lines.append('Sandbox tool statuses: ' + ', '.join(sandbox_statuses) + '.' if sandbox_statuses else 'No sandbox result was recorded.')
    for row in take['tool_proofs']:
        proof = row['proof']
        if proof.get('status') == 'static_impossible':
            facts = [fact['text'] for fact in proof.get('facts', []) if isinstance(fact.get('text'), str)]
            if facts:
                lines.extend(['', 'Tool proof: ' + ' '.join(facts)])
                break
    lines.extend(['', *take['behavior'].get('report_lines', []), '',
                  'The take audit retains raw Spark prose, software output, gate reasons, tool proof, checked choices, returned hashes, explanation and sandbox receipts separately in the JSON report.', '',
                  'Native freshness means actual same-campaign preparation. Cross-process handoff and identity validation do not constitute a new numerical run or model agency.', ''])
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verified-dir', '--recording-dir', dest='recording_dir', type=Path)
    parser.add_argument('--cohort', choices=('continuation', 'postfix', 'take'), default='continuation')
    parser.add_argument('--definitions-file', type=Path)
    parser.add_argument('--original-dir', type=Path, default=HERE / 'fixtures' / 'hero-final')
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--allow-unfinished', action='store_true', help='Only after explicit host go-ahead while native work drains')
    args = parser.parse_args(argv)
    defaults = {'take': 'hero-shot10', 'postfix': 'hero-postfix', 'continuation': 'hero-verified'}
    folder = args.recording_dir or HERE / 'fixtures' / defaults[args.cohort]
    if args.cohort == 'take':
        report, manifest = collect_take_report(folder, allow_unfinished=args.allow_unfinished)
    else:
        report, manifest = collect_report(folder, original_dir=args.original_dir, allow_unfinished=args.allow_unfinished,
                                          cohort_kind=args.cohort, definitions_file=args.definitions_file)
    if args.output_dir is not None:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        report_name = {'take': 'hero-shot10-report.json', 'postfix': 'hero-postfix-report.json', 'continuation': 'hero-final-report.json'}[args.cohort]
        for name, value in [(report_name, report), ('p22-playback-manifest.json', manifest)]:
            (args.output_dir / name).write_text(json.dumps(value, indent=2) + '\n')
        markdown_name = {'take': 'HERO-SHOT10-REPORT.md', 'postfix': 'HERO-POSTFIX-REPORT.md', 'continuation': 'HERO-FINAL-REPORT.md'}[args.cohort]
        (args.output_dir / markdown_name).write_text(render_markdown(report))
    print(render_markdown(report))


if __name__ == '__main__':
    main()
