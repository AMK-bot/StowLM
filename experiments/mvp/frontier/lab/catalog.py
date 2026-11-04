"""Frozen validation/TRAIN selections and stored Frontier evidence.

Safety uses positive provenance; sealed lists themselves are never opened. Small
validation is audit30 + reserve25, which SPLIT.json defines disjoint from ablation15.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from functools import lru_cache

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SETS = HERE / 'sets'
VALSPLIT = ROOT / 'experiments/mvp/valsplit'
TRAIN_PREFIXES = (
    'experiments/mvp/strategy/blockrule/maps/train/',
    'experiments/mvp/strategy/fillsweep/maps/train/',
)


def _lines(path):
    return [line.strip() for line in Path(path).read_text().splitlines()
            if line.strip() and not line.lstrip().startswith('#')]


@lru_cache(maxsize=1)
def _validation_allowlist():
    return set().union(*(_lines(VALSPLIT / name) for name in
                        ('tiny_val48.txt', 'audit_small30.txt', 'reserve_small25.txt')))


@lru_cache(maxsize=1)
def _train_allowlist():
    cohort = json.loads((ROOT / 'experiments/mvp/strategy/blockrule/cohort-medium.json').read_text())
    paths = {row['input'] for row in cohort['maps'] if row.get('split') == 'train'}
    for summary in (ROOT / 'experiments/mvp/scale/runs/v6/maps').glob('*/summary.json'):
        job = json.loads(summary.read_text()).get('job', {})
        if job.get('split') == 'train' and job.get('input', '').startswith(TRAIN_PREFIXES):
            paths.add(job['input'])
    return frozenset(paths)


def ensure_safe(path):
    """Return repo-relative input path or raise ValueError before reading its bytes.

    Unknown paths fail closed. Even a syntactically TRAIN path must remain inside
    its allowed tree after resolving symlinks; evaluation-only/sealed trees fail.
    """
    raw = Path(path)
    absolute = (ROOT / raw).resolve() if not raw.is_absolute() else raw.resolve()
    try:
        rel = absolute.relative_to(ROOT).as_posix()
    except ValueError as error:
        raise ValueError(f'Input is outside this repository: {path}') from error
    if rel.endswith('.audit.json') or not rel.endswith('.json'):
        raise ValueError(f'Not an instance JSON: {rel}')
    if rel not in _validation_allowlist() and rel not in _train_allowlist():
        raise ValueError(f'Input lacks approved validation/TRAIN provenance (sealed or unknown): {rel}')
    return rel


assert_allowed = ensure_safe


def load_set(name_or_list):
    """Load a named set or list file and validate every input before any map read."""
    named = SETS / f'{name_or_list}.txt'
    path = named if str(name_or_list) in ('quick', 'quick_tiny', 'quick_small', 'quick_medium', 'medium12') else Path(name_or_list)
    if not path.is_absolute():
        path = ROOT / path
    # A caller cannot use a sealed list as an indirect way to read sealed paths.
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(ROOT).as_posix()
    except ValueError:
        relative = ''  # external, user-authored list files may contain approved maps
    forbidden = ('experiments/mvp/testv3/', 'experiments/mvp/refs_tools/small-v21-instances.txt',
                 'experiments/mvp/valsplit/ablation_small15.txt', 'experiments/mvp/frontier/runs/test-',
                 'experiments/mvp/frontier/runs/unseen15-', 'data/raw/rcsppsuite/')
    if any(relative.startswith(p) for p in forbidden):
        raise ValueError(f'Sealed list is not allowed: {relative}')
    return [ensure_safe(p) for p in _lines(resolved)]


resolve_set = load_set


def load_baseline():
    return json.loads((HERE / 'baseline.json').read_text())['maps']


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def select_sets():
    """Compute selections by the fixed rules; ordinal strides are one-indexed."""
    tiny = _lines(VALSPLIT / 'tiny_val48.txt')[5::6]
    small_rows = []
    for path in sorted((ROOT / 'experiments/mvp/frontier/runs/val-0.9').glob('*.json')):
        row = json.loads(path.read_text())
        if isinstance(row, dict) and row.get('instance_id') and '.small.' in row['instance_id']:
            small_rows.append(row)
    small_rows.sort(key=lambda r: r['instance_id'])
    small = [row['input'] for row in small_rows[4::5][:10]]
    cohort = json.loads((ROOT / 'experiments/mvp/strategy/blockrule/cohort-medium.json').read_text())
    assert cohort['split'] == 'train'
    medium12 = [row['input'] for row in cohort['maps']]
    assert all(row['split'] == 'train' for row in cohort['maps'])
    summaries = []
    for path in sorted((ROOT / 'experiments/mvp/scale/runs/v6/maps').glob('*/summary.json')):
        row = json.loads(path.read_text())
        summaries.append((path, row))
    certified = []
    evidence = {}
    for ship in ('feeder_500', 'feeder_800'):
        path, row = next((p, r) for p, r in summaries if r.get('checker_passed') is True
                         and r.get('full_checker_unmodified') is True and r['job']['ship'] == ship)
        assert row['job']['split'] == 'train'
        certified.append(row['job']['input'])
        evidence[row['job']['input']] = str(path.relative_to(ROOT))
    d185 = next(row['input'] for row in cohort['maps'] if row['seed'] == 185100000)
    quick_medium = certified + [d185]
    selections = dict(quick_tiny=tiny, quick_small=small, quick_medium=quick_medium, medium12=medium12,
                      quick=tiny + small + quick_medium)
    assert tuple(map(len, (tiny, small, quick_medium, medium12))) == (8, 10, 3, 12)
    for paths in selections.values():
        assert len(paths) == len(set(paths))
        for rel in paths:
            ensure_safe(rel)
    return selections, evidence


def build():
    """Regenerate manifest and baseline without solving or opening sealed material."""
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from stowage.moves import apply_plan

    selections, v6_evidence = select_sets()
    SETS.mkdir(exist_ok=True)
    rules = {
        'quick_tiny': 'One-indexed lines 6,12,...,48 of valsplit/tiny_val48.txt (Python [5::6]).',
        'quick_small': 'Sort val-0.9 instance ids; take one-indexed ranks 5,10,...,50 (Python [4::5][:10]).',
        'quick_medium': 'Lexicographically first full-checker-certified V6 summary for feeder_500, then feeder_800; then D185 seed 185100000.',
        'medium12': 'All 12 TRAIN maps in frozen cohort-medium.json, in cohort order.',
        'quick': 'Concatenate quick_tiny, quick_small, quick_medium in that order.',
    }
    manifest = dict(schema_version=1, rules=rules, safety={
        'sealed_lists_opened': False,
        'validation_sources': ['experiments/mvp/valsplit/tiny_val48.txt', 'experiments/mvp/valsplit/audit_small30.txt', 'experiments/mvp/valsplit/reserve_small25.txt'],
        'small_disjointness_evidence': 'experiments/mvp/valsplit/SPLIT.json defines audit and reserve as excluding sealed ablation15; tiny48 contains only val/tiny.',
        'train_prefixes': list(TRAIN_PREFIXES),
        'train_allowlist_sources': ['experiments/mvp/strategy/blockrule/cohort-medium.json', 'experiments/mvp/scale/runs/v6/maps/*/summary.json job split=train'],
        'assertion': 'Every selected resolved path has positive validation/TRAIN provenance; unknown paths fail closed.',
    }, sets=selections, maps={})
    stored_rows = {}
    audits = {}
    for directory in ('val-0.9', 'tiny48-0.9'):
        folder = ROOT / 'experiments/mvp/frontier/runs' / directory
        for path in sorted(folder.glob('*.json')):
            if path.name == 'AUDIT.json':
                for row in json.loads(path.read_text()):
                    audits[row['instance_id']] = row
                continue
            row = json.loads(path.read_text())
            if isinstance(row, dict) and row.get('instance_id'):
                stored_rows[row['input']] = (path, row)
    medium_old = ROOT / 'experiments/mvp/frontier/runs/medium12-0.8.2'
    baseline = dict(schema_version=1, version='0.9.0', notes=[
        'Passing tiny/small 0.9.0 rows retain full final plan JSON, actions and ballast. stored is their assignment; ordered_actions preserves the stored load order.',
        'Parts are taken from stored AUDIT.json where present, otherwise deterministic move replay of the stored final plan. Replay does not replace the stored full-checker verdict.',
        'No 0.9.0 stored row exists for these medium inputs in val-0.9 or tiny48-0.9; null means not run, not a failing 0.9.0 result. Earlier Frontier 0.8.2 and V6 evidence is recorded separately.',
    ], maps={})
    all_paths = list(dict.fromkeys(p for paths in selections.values() for p in paths))
    for rel in all_paths:
        ensure_safe(rel)
        path = ROOT / rel
        instance = load_instance(path)
        selected = [name for name, paths in selections.items() if rel in paths]
        manifest['maps'][instance.id] = dict(input=rel, input_sha256=_sha(path), sets=selected,
            size=instance.size_class, provenance='validation' if rel in _validation_allowlist() else 'TRAIN',
            selection=[dict(set=name, rule=rules[name], ordinal=selections[name].index(rel)+1) for name in selected])
        row = dict(instance_id=instance.id, input=rel, input_sha256=_sha(path), size=instance.size_class,
                   n_boxes=len(instance.load_list), passed=None, lifts=None, relocations=None,
                   parts=dict(relocations=None, hatch_now=None, hatch_later=None, restow_now=None, restow_later=None),
                   stored=None, ordered_actions=None, plan=None, source=None, evidence=[])
        if rel in stored_rows:
            source, old = stored_rows[rel]
            assert old['input_sha256'] == _sha(path), f'Stale baseline input: {rel}'
            row.update(passed=old.get('passed'), lifts=old.get('weighted'), source=str(source.relative_to(ROOT)),
                       source_sha256=_sha(source), wall_s=old.get('wall_s'), plan=old.get('plan'))
            plan = old.get('plan')
            if plan:
                actions = plan.get('actions') or [dict(op='P', box=m['container_id'], slot=m['dst'])
                                                  for m in plan['moves'] if m['kind'] == 'load']
                row['ordered_actions'] = actions
                row['stored'] = {a['box']: a['slot'] for a in actions if a.get('op', 'P') == 'P'}
                audit = audits.get(instance.id)
                if audit:
                    row['parts'] = {k: audit[k] for k in row['parts']}
                    row['parts_source'] = str(source.parent.relative_to(ROOT) / 'AUDIT.json')
                else:
                    replay = apply_plan(instance, MovePlan.model_validate(plan))
                    assert replay.legal, f'Stored plan replay failed: {instance.id}'
                    count = replay.count
                    row['parts'] = dict(relocations=count.relocations, hatch_now=count.hatch_cover_lifts,
                        hatch_later=sum(f.hatch_cover_lifts for f in count.future), restow_now=count.restow_lifts,
                        restow_later=sum(f.restow_lifts for f in count.future))
                    row['parts_source'] = 'deterministic apply_plan replay of stored final plan'
                row['relocations'] = row['parts']['relocations']
        else:
            earlier = medium_old / f'{instance.id}.json'
            if earlier.exists():
                old = json.loads(earlier.read_text())
                row['evidence'].append(dict(source=str(earlier.relative_to(ROOT)), version='0.8.2',
                    passed=old.get('passed'), lifts=old.get('weighted'), note='Earlier version only; not a 0.9.0 comparator.'))
            if rel in v6_evidence:
                old = json.loads((ROOT / v6_evidence[rel]).read_text())
                row['evidence'].append(dict(source=v6_evidence[rel], version='V6', passed=old.get('checker_passed'),
                    lifts=old.get('count', {}).get('weighted_nonproductive'), count=old.get('count'), note='V6 certificate used only for fixed set selection.'))
        baseline['maps'][instance.id] = row
    for name, paths in selections.items():
        (SETS / f'{name}.txt').write_text(''.join(p + '\n' for p in paths))
    (SETS / 'MANIFEST.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    (HERE / 'baseline.json').write_text(json.dumps(baseline, indent=2, sort_keys=True) + '\n')
    print(f'Wrote {len(all_paths)} maps, {sum(r["stored"] is not None for r in baseline["maps"].values())} stored plans.')


if __name__ == '__main__':
    build()
