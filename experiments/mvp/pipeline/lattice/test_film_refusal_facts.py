from copy import deepcopy
import hashlib
import json

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_json
from experiments.mvp.pipeline.lattice import film_refusal_facts as facts
from experiments.mvp.pipeline.lattice import film_analysis
from experiments.mvp.pipeline.lattice.store import canonical_rule_set, entry_key
from stowage.conventions import SYNTHETIC_OWNER_CODES, make_container_id
from stowage.model import Instance
from stowage.model.fixtures import build_instance


DECK0 = dict(type='max_tiers', bay=None, zone='deck', max_tiers=0)
DECK1 = dict(type='max_tiers', bay=None, zone='deck', max_tiers=1)
DECK2 = dict(type='max_tiers', bay=None, zone='deck', max_tiers=2)
HOLD0 = dict(type='max_tiers', bay=None, zone='hold', max_tiers=0)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    # Thirteen footprint cells of synthetic cargo, sixteen physical cells.
    # No checker certificate is claimed by this geometry-only unit fixture.
    original = build_instance()
    extra = [original.containers[2].model_copy(update={'id': make_container_id(SYNTHETIC_OWNER_CODES[0], i),
                                                     'direct_delivery': True}) for i in range(10, 18)]
    instance = original.model_copy(update={'containers': list(original.containers)+extra,
        'load_list': list(original.load_list)+[box.id for box in extra]})
    instance = Instance.model_validate(instance.model_dump(mode='json'))
    here = tmp_path/'lattice'
    store = here/'store'
    store.mkdir(parents=True)
    monkeypatch.setattr(facts, 'HERE', here)
    monkeypatch.setattr(film_analysis, 'HERE', here)
    monkeypatch.setattr(facts, '_binding_source_matches', lambda row: row.get('binding_source_sha256') == 'a'*64)
    digest = sha256_json(instance.model_dump(mode='json'))
    catalogue = {}
    def save(rules, *, status='proved_refusal', allowed=None, kind='proved_full_load', stale=False, hero=None):
        canonical = canonical_rule_set(rules)
        body = instance.model_dump(mode='json')
        if hero:
            body['id'] = hero
        sha = sha256_json(body)
        key = entry_key(sha, canonical)
        row = dict(id='fixture-'+key[:8], key=key, status=status, mode='stored', instance=body,
                   instance_sha256=sha, canonical_rules=canonical, computed_utc='2026-11-02T00:00:00Z',
                   engine_version='0.13.0', engine_manifest_sha256='b'*64,
                   binding_source_sha256='stale' if stale else 'a'*64, binding_source_after_sha256='a'*64,
                   sentences=['Synthetic capacity request'],
                   binding_evidence=dict(refusal_kind=kind, reason='exact original necessary screen text'),
                   proof=dict(reason='stored necessary screen', arithmetic=dict(demand_cells=13, open_cells=allowed,
                              proven_impossible=True, equation=f'13 needed > {allowed} open')))
        path = store/(key+'.json')
        path.write_text(json.dumps(row, indent=2)+'\n')
        catalogue[key] = hashlib.sha256(path.read_bytes()).hexdigest()
        (store/'catalogue.json').write_text(json.dumps(catalogue))
        return row, path
    return dict(here=here, store=store, sha=digest, instance=instance, save=save)


def snapshot(data):
    return film_analysis.authenticated_snapshot(data['store'])


def test_pair_preserves_recorded_prefix_numbers_and_separates_combined_capacity(fixture):
    row, path = fixture['save']([DECK0, HOLD0], allowed=8)
    snap = snapshot(fixture)
    result = facts.build_impossible(snap, fixture['sha'])
    assert result['available'] is True and result['refusal_kind'] == 'proved_full_load'
    assert result['numbers']['cells_needed'] == 13 and result['numbers']['cells_allowed'] == 8
    proof = result['arithmetic_proof']
    assert proof['sufficient_rule_subset']['canonical_rules'] == canonical_rule_set([DECK0])
    assert proof['all_requested_rules']['cells_allowed'] == 0
    assert proof['recorded_arithmetic'] == row['proof']['arithmetic']
    assert result['source']['path'] == str(path)
    assert result['source']['raw_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result['source']['catalogue_sha256'] == snap['catalogue_sha256']
    assert result['number_source_fields']['cells_needed'] == '/proof/arithmetic/demand_cells'
    assert result['number_source_fields']['cells_allowed'] == '/proof/arithmetic/open_cells'
    assert result['source']['reason']['binding_evidence_reason'] == row['binding_evidence']['reason']
    assert result['checker_called'] is result['planner_called'] is result['hatch_allocator_called'] is False


def test_plain_global_deck_one_is_preferred_only_when_actually_stored_and_proved(fixture):
    fixture['save']([DECK0, HOLD0], allowed=8)
    row, _ = fixture['save']([DECK1], allowed=12)
    result = facts.build_impossible(snapshot(fixture), fixture['sha'])
    assert result['key'] == row['key'] and result['eligible_stored_refusals'] == 2
    assert result['canonical_rules'] == canonical_rule_set([DECK1])
    assert result['numbers']['cells_allowed'] == 12


@pytest.mark.parametrize('problem', ['budget_failed', 'unknown', 'stale_binding', 'changed_bytes', 'wrong_arithmetic', 'other_hero'])
def test_unproved_unknown_stale_or_foreign_sources_are_never_film_impossibility(fixture, problem):
    options = dict(allowed=8)
    if problem == 'budget_failed': options['status'] = 'budget_failed'
    elif problem == 'unknown': options['kind'] = 'solver_timeout'
    elif problem == 'stale_binding': options['stale'] = True
    elif problem == 'wrong_arithmetic': options['allowed'] = 7
    elif problem == 'other_hero': options['hero'] = 'foreign-original'
    row, path = fixture['save']([DECK0, HOLD0], **options)
    snap = snapshot(fixture)
    if problem == 'changed_bytes': path.write_bytes(path.read_bytes()+b' ')
    result = facts.build_impossible(snap, fixture['sha'])
    assert result['available'] is False and result['status'] == 'unavailable'


def test_queued_capacity_candidates_are_exact_static_facts_and_never_stored_refusals(fixture):
    fixture['save']([DECK0, HOLD0], allowed=8)
    rules = [canonical_rule_set([body]) for body in (DECK1, DECK2, {'type': 'one_port_per_hatch'})]
    specs = [dict(id=f'queued-{i}', key=entry_key(fixture['sha'], rule), rules=rule, sentences=['Queued only']) for i, rule in enumerate(rules)]
    manifest = dict(instance_sha256=fixture['sha'], instructions=specs)
    candidates = facts.coverage_candidates(snapshot(fixture), fixture['sha'], manifest)
    assert len(candidates) == 1 and candidates[0]['id'] == 'queued-0'
    assert candidates[0]['cells_needed'] == 13 and candidates[0]['cells_allowed'] == 12
    assert candidates[0]['stored'] is False and candidates[0]['status'] == 'queued_capacity_refusal_candidate'
    # Its static arithmetic cannot promote an unstored line to the filmed answer.
    assert facts.build_impossible(snapshot(fixture), fixture['sha'])['canonical_rules'] != specs[0]['rules']
    manifest['instructions'][0]['key'] = 'f'*64
    with pytest.raises(ValueError, match='key differs'):
        facts.coverage_candidates(snapshot(fixture), fixture['sha'], manifest)


def test_refresh_writes_pretty_source_bound_output_without_changing_inputs(fixture):
    fixture['save']([DECK0, HOLD0], allowed=8)
    hero = fixture['here']/'hero.json'
    hero.write_text(json.dumps({'hero': {'instance_sha256': fixture['sha']}}))
    before = {str(path): path.read_bytes() for path in fixture['here'].rglob('*.json')}
    output = fixture['here']/'hero-impossible.json'
    result = facts.refresh(output=output, store_dir=fixture['store'], hero_path=hero,
                           coverage_path=fixture['here']/'missing-coverage.json')
    assert result['available'] is True and result['queued_capacity_refusals'] == []
    assert output.read_text().startswith('{\n  ')
    assert all(__import__('pathlib').Path(path).read_bytes() == raw for path, raw in before.items())


def test_hatch_refusals_never_invoke_a_live_or_necessary_allocator(fixture, monkeypatch):
    fixture['save']([{'type': 'one_port_per_hatch'}], allowed=0)
    from experiments.mvp.pipeline.lattice import store
    monkeypatch.setattr(store, '_refusal_proof', lambda row: pytest.fail('film facts must not run a hatch allocator'))
    assert facts.build_impossible(snapshot(fixture), fixture['sha'])['available'] is False
