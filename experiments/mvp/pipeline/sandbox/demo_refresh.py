"""Refresh the hero drag demo without altering any vessel or rule limit."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from time import perf_counter

from .benchmark import HERE, admission, read_fixture

FIX = HERE / 'fixtures'
ROOT = HERE.parents[3]
HERO = ROOT / 'experiments/mvp/pipeline/lattice/fixtures/hero-base.json'
SOURCE = FIX / 'demo_source_hero.json'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def source():
    from stowage.model import Instance, MovePlan
    from stowage.rules.imdg.dgl import lookup_dgl
    original = read_fixture(HERO)
    instance = deepcopy(original['instance'])
    plan = deepcopy(original['plan'])
    box = next(b for b in instance['containers'] if b['id'] == 'QZXU0000014')
    before = deepcopy(box['dg'])
    entry = lookup_dgl(1689, 'I')
    box['dg'].update(un_number='1689', imdg_class='6.1', packing_group='I',
        stowage_category='B', subsidiary_risks=[], stowage_codes=list(entry.stowage_codes),
        segregation_codes=list(entry.segregation_codes),
        shipment_provenance={'created_by': 'sandbox.synthetic_demo', 'parents': ['imdg-42-24:UN1689-I']})
    modifications = [{'field': 'instance.containers[QZXU0000014].dg', 'before': before,
        'after': deepcopy(box['dg']), 'source': 'Curated IMDG DGL UN1689-I; synthetic shipment declaration',
        'plain_words': 'QZXU0000014: synthetic UN1263 paint, class 3, packing group III becomes UN1689 sodium cyanide, class 6.1, packing group I. Its mass and dimensions stay unchanged. The existing hydrochloric acid QZXU0000349 (UN1789-II) stays unchanged.'}]
    instance['id'] += '.sandbox-synthetic-dg'
    plan['instance_id'] = instance['id']
    plan['id'] += '.sandbox-synthetic-dg'
    Instance.model_validate(instance)
    MovePlan.model_validate(plan)
    fixture = {'synthetic': True, 'mode': 'recorded',
        'label': 'Demo cargo and limits tightened for this challenge',
        'label_disclosure': 'Cargo declaration changed as listed; no vessel, trim, stack, securing, DG or other limit was tightened.',
        'instance': instance, 'plan': plan, 'source_result': str(HERO.relative_to(ROOT)),
        'derived_from': str(HERO.relative_to(ROOT)), 'derived_from_sha256': digest(HERO),
        'hero_instance_sha256': original['instance_sha256'], 'hero_plan_id': original['plan']['id'],
        'modifications': modifications, 'limit_changes': [], 'baseline_passed': None,
        'scope_note': "This edits one box and keeps the crane order and ballast. It doesn't replan.",
        'quality_note': "Legal isn't the same as good.",
        'created_utc': datetime.now(timezone.utc).isoformat()}
    save(SOURCE, fixture)
    return fixture


def baseline():
    from stowage.checker import check
    from stowage.checker.full import report_matches_inputs
    from stowage.model import Instance, MovePlan
    f = read_fixture(SOURCE)
    i, p = Instance.model_validate(f['instance']), MovePlan.model_validate(f['plan'])
    admitted = admission()
    start = perf_counter()
    r = check(i, p)
    data = {'source_fixture': str(SOURCE.relative_to(ROOT)), 'source_sha256': digest(SOURCE),
        'baseline_passed': r.passed, 'input_bound': report_matches_inputs(r, i, p),
        'baseline_report': r.model_dump(mode='json'), 'admission': admitted,
        'wall_s': perf_counter()-start, 'created_utc': datetime.now(timezone.utc).isoformat()}
    save(FIX/'demo_hero_baseline.json', data)
    print(json.dumps({'baseline_passed': r.passed, 'wall_s': data['wall_s'],
        'rules': [v.rule_id for v in r.violations]}), flush=True)
    if not r.passed or not data['input_bound']:
        raise ValueError('The synthetic hero cargo baseline did not pass the full input-bound checker')




def discover():
    from stowage.model import Instance, MovePlan
    from .benchmark import candidates
    from .moves import prepare_move, _counts, fast_screen
    from .physics import fast_check
    f = read_fixture(SOURCE)
    i, p = Instance.model_validate(f['instance']), MovePlan.model_validate(f['plan'])
    admission()
    choices = []
    for index, (box, target) in enumerate(candidates(i, p, 8)):
        if index >= 100:
            break
        pm = prepare_move(i, p, box, target)
        if not pm.replay.legal or pm.replay.executed != len(pm.plan.moves):
            continue
        b, _ = fast_check(i, pm.ship)
        if any(v.get('severity', 'hard') == 'hard' for v in b):
            continue
        counts = _counts(pm)
        choices.append({'box_id': box, 'target_slot': target,
            'projected_later_delta': counts['lift_counts']['projected_later_delta']})
    choices.sort(key=lambda r: (-r['projected_later_delta'], r['box_id'], r['target_slot']))
    legal = None
    for req in choices[:12]:
        admission()
        v = fast_screen(i, p, req['box_id'], req['target_slot'])
        print(json.dumps({'candidate': req, 'fast_clear': v.legal}), flush=True)
        if v.legal:
            legal = req
            break
    if legal is None:
        raise ValueError('No showcase pass selected')
    selected = {'legal': legal,
        'crane_order': {'box_id': 'QZXU0000610', 'target_slot': '0210182'},
        'securing': {'box_id': 'QZXU0000478', 'target_slot': '0100688'},
        'segregation': {'box_id': 'QZXU0000014', 'target_slot': '0220602'}}
    out = {'synthetic': True, 'source_fixture': str(SOURCE.relative_to(ROOT)),
        'source_sha256': digest(SOURCE), 'cases': selected, 'legal_candidates': choices,
        'missing': [], 'label': 'Prepared candidates; full checks pending',
        'created_utc': datetime.now(timezone.utc).isoformat()}
    save(FIX/'demo_hero_selected.json', out)
    print(json.dumps({'selected': selected}), flush=True)


def record():
    from experiments.mvp.pipeline.contract.schema import Mode
    from stowage.model import Instance, MovePlan
    from .moves import try_move
    from .explain import explain_verdict
    f = read_fixture(SOURCE)
    i, p = Instance.model_validate(f['instance']), MovePlan.model_validate(f['plan'])
    base = read_fixture(FIX/'demo_hero_baseline.json')
    if not base['baseline_passed'] or base['source_sha256'] != digest(SOURCE):
        raise ValueError('Fresh full baseline does not bind to the frozen demo source')
    proposed = read_fixture(FIX/'demo_hero_selected.json')
    if proposed['source_sha256'] != digest(SOURCE):
        raise ValueError('Prepared requests do not bind to the frozen demo source')
    data = {'synthetic': True, 'mode': 'recorded', 'label': f['label'],
        'source_fixture': str(SOURCE.relative_to(ROOT)), 'source_sha256': digest(SOURCE),
        'baseline_passed': True, 'baseline_report': base['baseline_report'],
        'baseline_admission': base['admission'], 'source_baseline_passed': True,
        'created_utc': datetime.now(timezone.utc).isoformat(), 'modifications': f['modifications'],
        'limit_changes': [], 'historical_limit_changes_removed': [
            {'field': 'iscode.loading.trim.maximum', 'old_demo_m': -0.15567719231265897,
             'current_hero_window_m': [-1.078, 1.078],
             'note': 'The fitted negative trim limit and its case were removed. These are the unchanged limits of the new feeder500 hero, not a tightened window.'},
            {'field': 'stack00904D ratings', 'old_demo_kg': 18256.402964792054,
             'current_hero_kg': {'max_mass_kg': 30000, 'max_mass_20_kg': 45000, 'max_mass_40_kg': 30000},
             'note': 'The historical feeder800 stack cap was removed. The feeder500 hero uses its original ratings; its different vessel rating is not a demo edit.'}],
        'scope_note': f['scope_note'], 'quality_note': f['quality_note'],
        'label_disclosure': f['label_disclosure'],
        'note': 'Four independent drags from the checked hero with one disclosed synthetic DG declaration. No limit edits, no search, no replan. Fresh offline template explanations; historical Spark selections do not describe these new facts.',
        'case_id_migration': {'legal': 'legal', 'stability': 'removed: invented negative trim limit',
            'stack_weight': 'replaced by securing at unchanged limits', 'segregation': 'segregation',
            'new': 'crane_order'},
        'fixture_shape': 'Same top-level and per-case playback fields; kind values now legal, crane_order, segregation, securing. New metadata fields are additive.',
        'fast_frames_note': 'Fast frames captured through on_fast during the same invocation as each recorded full verdict. All phases are stored playback; actual per-phase timings are preserved.',
        'cases': [], 'complete': False, 'missing': []}
    for kind in ('legal', 'crane_order', 'segregation', 'securing'):
        req = proposed['cases'][kind]
        admitted = admission()
        fast = []
        v = try_move(i, p, req['box_id'], req['target_slot'], on_fast=fast.append)
        expected = {'legal': 'passes_as_edited', 'crane_order': 'stow_fine_crane_order_failed',
                    'securing': 'stow_breaks_rule', 'segregation': 'stow_breaks_rule'}[kind]
        outcome = v.gauges.get('outcome')
        confirmed = outcome == expected
        if kind == 'crane_order':
            confirmed = confirmed and v.gauges.get('stow_assessment', {}).get('passed') is True
        if kind in ('securing', 'segregation'):
            prefix = 'iacs.member_irs.container_securing' if kind == 'securing' else 'imdg.segregation.'
            confirmed = confirmed and any(b['rule_id'].startswith(prefix) for b in v.broken)
        row = {'kind': kind, 'confirmed': confirmed, 'box_id': req['box_id'],
            'target_slot': req['target_slot'], 'admission': admitted, 'verdict': v.model_dump(mode='json'),
            'fast': fast[0].model_dump(mode='json'),
            'explanation': explain_verdict(v).model_copy(update={'mode': Mode.recorded}).model_dump(mode='json'),
            'fast_explanation': explain_verdict(fast[0]).model_copy(update={'mode': Mode.recorded}).model_dump(mode='json'),
            'explanation_provenance': 'offline deterministic template recorded from this verdict'}
        row['fast_recording'] = {'created_utc': datetime.now(timezone.utc).isoformat(),
            'admission': admitted,
            'note': 'Fast frame captured by on_fast in the same invocation as this full verdict; this timestamp records serialization after that invocation.'}
        row['template_explanation'] = deepcopy(row['explanation'])
        row['spark_selection_ids'] = []
        data['cases'].append(row)
        data['missing'] = [c['kind'] for c in data['cases'] if not c['confirmed']]
        data['complete'] = len(data['cases']) == 4 and not data['missing']
        save(FIX/'demo_hero_prepared.json', data)
        if data['complete']:
            save(FIX/'demo.json', data)
        print(json.dumps({'kind': kind, 'confirmed': confirmed, 'outcome': outcome,
            'wall_s': v.wall_s, 'deciding_cause': v.gauges.get('deciding_cause'),
            'lifts': v.gauges.get('lift_counts')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['source', 'baseline', 'discover', 'record'])
    args = parser.parse_args()
    globals()[args.action]()
