"""Source-bound stored capacity-refusal facts for filming; never plans or gates."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from itertools import combinations
import json
import math
import os
from pathlib import Path
import tempfile

from experiments.mvp.pipeline.contract.schema import canonical_json, sha256_file, sha256_json
from .film_analysis import authenticated_snapshot
from .store import _binding_source_matches, canonical_rule_set, entry_key

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def _inside(path):
    path = Path(path).resolve()
    if not path.is_relative_to(HERE.resolve()):
        raise ValueError('film refusal paths must remain inside lattice')
    return path


def _authenticated_source(snapshot, source, hero_sha):
    row = source['record']
    raw = Path(source['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != source['raw_sha256'] or json.loads(raw) != row:
        raise ValueError('source bytes differ from the authenticated snapshot')
    if row.get('instance_sha256') != hero_sha or sha256_json(row.get('instance')) != hero_sha:
        raise ValueError('source does not bind the original hero instance')
    rules = canonical_rule_set(row['canonical_rules'])
    if row.get('key', source['key']) != source['key'] or entry_key(hero_sha, rules) != source['key']:
        raise ValueError('source canonical rule key differs')
    if not snapshot.get('catalogue_sha256') or not _binding_source_matches(row):
        raise ValueError('source catalogue or binding runtime provenance is unavailable or stale')
    return row, rules


def _capacity(instance, rules):
    """Cheap public footprint/cell arithmetic only; hatch allocation is excluded."""
    from experiments.mvp.reader.refusal_evidence import build_static_proof
    from experiments.mvp.reader.schema import context_from_instance, validate_answer, OnePortPerHatch
    from llm.instructions.apply import apply
    context = context_from_instance(instance)
    answers = [validate_answer(canonical_json(rule), context) for rule in rules]
    if not answers or any(isinstance(answer, OnePortPerHatch) for answer in answers):
        raise ValueError('capacity film facts do not run hatch allocators')
    if len(answers) == 1:
        proof = build_static_proof(instance, answers[0])
        needed, allowed = proof['demand_cells'], proof['open_cells']
        public_proof = proof
    else:
        compiled = None
        for answer in answers:
            compiled = apply(instance, answer, base=compiled)
        active = set(instance.load_list)
        needed = sum(1 if box.length_ft == 20 else 2 for box in instance.containers if box.id in active)
        allowed = len(compiled.open_cells())
        public_proof = dict(scope='union of universal instruction cell closures; necessary capacity only',
                            demand_cells=needed, open_cells=allowed, proven_impossible=needed > allowed)
    return dict(cells_needed=needed, cells_allowed=allowed, shortfall_cells=max(0, needed-allowed),
                equation=f'{needed:g} needed > {allowed:g} allowed' if needed > allowed else f'{needed:g} needed <= {allowed:g} allowed',
                capacity_shortfall=needed > allowed, public_proof=public_proof)


def _recorded_numbers(row):
    arithmetic = (row.get('proof') or {}).get('arithmetic')
    if not isinstance(arithmetic, dict) or arithmetic.get('proven_impossible') is not True:
        raise ValueError('stored refusal lacks proved arithmetic')
    needed, allowed = arithmetic.get('demand_cells'), arithmetic.get('open_cells')
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
           for value in (needed, allowed)) or not 0 <= allowed < needed:
        raise ValueError('stored refusal is not a finite positive capacity shortfall')
    return needed, allowed, arithmetic


def _rank(rules):
    if len(rules) == 1:
        body = rules[0]['constraint']
        if body['type'] == 'max_tiers':
            preferred = body['zone'] == 'deck' and body['bay'] is None and body['max_tiers'] == 1
            return (0 if preferred else 1, body['bay'] is not None, body['zone'] != 'deck', body['max_tiers'], canonical_json(rules))
        return (2, False, False, 0, canonical_json(rules))
    return (3, len(rules), False, 0, canonical_json(rules))


def _source_fields(snapshot, source, row):
    return dict(path=source['path'], raw_sha256=source['raw_sha256'], key=source['key'],
                catalogue_path=str(Path(snapshot['path'])/'catalogue.json'), catalogue_sha256=snapshot['catalogue_sha256'],
                computed_utc=row.get('computed_utc'), status=row.get('status'),
                instance_sha256=row['instance_sha256'], engine_version=row.get('engine_version'),
                engine_manifest_sha256=row.get('engine_manifest_sha256'),
                binding_source_sha256=row.get('binding_source_sha256'),
                binding_source_after_sha256=row.get('binding_source_after_sha256'),
                reason=dict(proof_reason=(row.get('proof') or {}).get('reason'),
                            binding_evidence_reason=(row.get('binding_evidence') or {}).get('reason')))


def build_impossible(snapshot, hero_sha):
    """Choose a stored proved refusal, rechecking its exact sufficient arithmetic."""
    from stowage.model import Instance
    accepted, rejected = [], []
    for source in snapshot.get('rows', []):
        row = source['record']
        if row.get('instance_sha256') != hero_sha or row.get('status') != 'proved_refusal':
            continue
        try:
            row, rules = _authenticated_source(snapshot, source, hero_sha)
            kind = (row.get('binding_evidence') or {}).get('refusal_kind')
            if kind not in (None, 'proved_full_load'):
                raise ValueError('stored result reports an unknown/timeout/nonproof refusal kind')
            needed, allowed, recorded = _recorded_numbers(row)
            instance = Instance.model_validate(row['instance'])
            all_rules = _capacity(instance, rules)
            sufficient = None
            for count in range(1, len(rules)+1):
                for subset in combinations(rules, count):
                    proof = _capacity(instance, list(subset))
                    if proof['capacity_shortfall'] and proof['cells_needed'] == needed and proof['cells_allowed'] == allowed:
                        sufficient = dict(canonical_rules=list(subset), **proof)
                        break
                if sufficient is not None:
                    break
            if sufficient is None:
                raise ValueError('recorded capacity arithmetic does not match any sufficient requested-rule subset')
            accepted.append((_rank(rules), dict(available=True, status='stored_proved_refusal', mode='stored',
                instance_sha256=hero_sha, id=row.get('id'), key=source['key'], canonical_rules=rules,
                sentences=row.get('sentences', []), sentence='; '.join(row.get('sentences', [])),
                refusal_kind='proved_full_load', stored_refusal_kind=kind,
                numbers=dict(cells_needed=needed, cells_allowed=allowed, shortfall_cells=needed-allowed,
                             units='physical footprint cells (20-foot equivalents)'),
                number_source_fields=dict(cells_needed='/proof/arithmetic/demand_cells',
                    cells_allowed='/proof/arithmetic/open_cells', refusal_kind='/binding_evidence/refusal_kind',
                    sentence='/sentences'),
                arithmetic_proof=dict(recorded_arithmetic=deepcopy(recorded), arithmetic_rechecked=True,
                    sufficient_rule_subset=sufficient, all_requested_rules=all_rules,
                    scope=('Recorded numbers apply to the sufficient subset, not necessarily every requested rule combined. '
                           'An impossible subset also proves the full request impossible.')),
                source=_source_fields(snapshot, source, row), checker_called=False, planner_called=False,
                hatch_allocator_called=False, fresh_native_verification=False)))
        except (OSError, ValueError, TypeError, KeyError) as error:
            rejected.append(dict(key=source.get('key'), reason=f'{type(error).__name__}: {error}'))
    if not accepted:
        return dict(schema_version='stowlm.hero-impossible/1', available=False, status='unavailable',
                    instance_sha256=hero_sha, rejected=rejected,
                    reason='No authenticated stored capacity refusal passed its current necessary arithmetic screen.',
                    checker_called=False, planner_called=False, hatch_allocator_called=False)
    accepted.sort(key=lambda item: (item[0], item[1]['key']))
    return dict(accepted[0][1], schema_version='stowlm.hero-impossible/1', rejected=rejected,
                eligible_stored_refusals=len(accepted))


def coverage_candidates(snapshot, hero_sha, manifest):
    """List unstored queued requests with a necessary capacity shortfall, not plans."""
    from stowage.model import Instance
    if manifest.get('instance_sha256') != hero_sha:
        raise ValueError('coverage manifest is for another hero')
    sources = [s for s in snapshot.get('rows', []) if s['record'].get('instance_sha256') == hero_sha]
    base_source = None
    for source in sorted(sources, key=lambda s: bool(s['record'].get('canonical_rules'))):
        try:
            _authenticated_source(snapshot, source, hero_sha)
            base_source = source
            break
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if base_source is None:
        raise ValueError('no authenticated original hero instance is available for candidate arithmetic')
    instance = Instance.model_validate(base_source['record']['instance'])
    existing = {source['key'] for source in snapshot['rows']}
    candidates = []
    for spec in manifest.get('instructions', []):
        rules = canonical_rule_set(spec['rules'])
        key = entry_key(hero_sha, rules)
        if spec.get('key') != key:
            raise ValueError('queued instruction key differs from canonical rules')
        if key in existing or len(rules) != 1 or rules[0]['constraint']['type'] == 'one_port_per_hatch':
            continue
        proof = _capacity(instance, rules)
        if not proof['capacity_shortfall']:
            continue
        candidates.append(dict(id=spec['id'], key=key, canonical_rules=rules, sentences=spec.get('sentences', []),
            sentence='; '.join(spec.get('sentences', [])), stored=False, planning_status='not inferred from this static screen',
            status='queued_capacity_refusal_candidate', **proof,
            instance_source=_source_fields(snapshot, base_source, base_source['record']),
            notice='Potential capacity-refusal film line only; it is not an authenticated stored refusal until published.'))
    return sorted(candidates, key=lambda row: (_rank(row['canonical_rules']), row['key']))


def refresh(*, output=HERE/'hero-impossible.json', store_dir=HERE/'store', hero_path=HERE/'hero.json',
            coverage_path=HERE/'hero-coverage/manifest.json'):
    hero_raw = _inside(hero_path).read_bytes()
    hero = json.loads(hero_raw)
    hero_sha = hero['hero']['instance_sha256']
    snapshot = authenticated_snapshot(_inside(store_dir))
    facts = build_impossible(snapshot, hero_sha)
    facts.update(refreshed_utc=datetime.now(timezone.utc).isoformat(),
        hero_source=dict(path=str(hero_path), raw_sha256=hashlib.sha256(hero_raw).hexdigest()),
        implementation_sha256=sha256_file(Path(__file__)))
    coverage_path = _inside(coverage_path)
    facts['queued_capacity_refusals'] = []
    if coverage_path.exists():
        raw = coverage_path.read_bytes()
        facts['coverage_manifest_source'] = dict(path=str(coverage_path), raw_sha256=hashlib.sha256(raw).hexdigest())
        facts['queued_capacity_refusals'] = coverage_candidates(snapshot, hero_sha, json.loads(raw))
    output = _inside(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.'+output.name+'.', dir=output.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(facts, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return facts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=HERE/'hero-impossible.json')
    parser.add_argument('--store-dir', type=Path, default=HERE/'store')
    parser.add_argument('--hero', type=Path, default=HERE/'hero.json')
    parser.add_argument('--coverage', type=Path, default=HERE/'hero-coverage/manifest.json')
    args = parser.parse_args()
    facts = refresh(output=args.output, store_dir=args.store_dir, hero_path=args.hero, coverage_path=args.coverage)
    print(json.dumps(dict(available=facts['available'], id=facts.get('id'), numbers=facts.get('numbers'),
                          queued_capacity_refusals=len(facts['queued_capacity_refusals']))))


if __name__ == '__main__':
    main()
