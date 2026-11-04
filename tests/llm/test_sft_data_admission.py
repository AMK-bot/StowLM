"""Deterministic boundary regressions; synthetic fixtures are never real gold."""
import pytest

from llm.stowlm.format import load_tokenizer
from llm.stowlm.sft_data import build_example, plan_actions, verify_replay
from stowage.actions import get_action_space
from stowage.checker import check  # registers the frozen checker interface
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.frozen import StubTaintError, require_clean, stub_override
from stowage.model import MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.provenance import Provenance
from stowage.versions import component_versions


@pytest.fixture
def pair():
    instance = build_instance(layover=False)
    plan = MovePlan(id='synthetic-not-gold', instance_id=instance.id, call_index=instance.call_index,
                    actions=[], action_space=f'{get_action_space().name}@{get_action_space().version}')
    return instance, plan


@pytest.mark.parametrize('field,value', [('instance_id', 'other-instance'), ('call_index', 1)])
def test_serializer_rejects_foreign_plan_before_encoding(pair, field, value):
    instance, plan = pair
    foreign = plan.model_copy(update={field: value})
    with pytest.raises(ValueError, match=field):
        build_example(instance, foreign, split='train')
    with pytest.raises(ValueError, match=field):
        plan_actions(instance, foreign)


def test_active_stub_refused_by_clean_builder_and_retained_by_diagnostic(pair):
    instance, plan = pair
    with stub_override('feasibility.make_engine', lambda *a, **kw: None, reason='boundary regression'):
        with pytest.raises(StubTaintError):
            build_example(instance, plan, split='train')
        example = build_example(instance, plan, split='train', allow_stubs=True)
    assert example.stubs == {'feasibility.make_engine': 'boundary regression'}
    with pytest.raises(StubTaintError):
        require_clean(example, what='SFT consumer')


def test_clean_serializer_is_explicitly_not_gold(pair):
    instance, plan = pair
    example = build_example(instance, plan, split='train')
    assert example.meta['gold']['published'] is False


def test_replay_preserves_active_stub_and_injected_engine_taint(pair):
    instance, plan = pair
    example = build_example(instance, plan, split='train')
    def diagnostic_engine(inst, space):
        return make_feasibility_engine(inst, space, config=FeasibilityConfig(extra={'global_screen': 0}))
    with stub_override('checker.check', lambda *a, **kw: None, reason='active replay regression'):
        report = verify_replay(instance, example, make_engine=diagnostic_engine)
    assert report.stubs['checker.check'] == 'active replay regression'
    assert 'sft.replay.make_engine' in report.stubs
    assert not report.production
    assert example.stubs == report.stubs
    with pytest.raises(StubTaintError):
        require_clean(report, what='SFT consumer')


def test_default_replay_refuses_actual_c2_unavailable(pair):
    instance, plan = pair
    report = verify_replay(instance, build_example(instance, plan, split='train'))
    assert not report.ok
    assert 'C2' in report.first_failure
    assert report.global_screen != 'on'
    assert not report.production


def test_replay_rejects_other_instance(pair):
    instance, plan = pair
    example = build_example(instance, plan, split='train')
    example.instance_id = 'other-instance'
    report = verify_replay(instance, example)
    assert not report.ok and 'instance_id' in report.first_failure


def test_replay_checks_prompt_boundary_and_action_count():
    from tests.llm.test_sft_data_fixtures import deterministic_case, diagnostic_engine
    instance, plan = deterministic_case(1)
    example = build_example(instance, plan, split='train')
    example.input_ids[0] += 1
    assert 'prompt' in verify_replay(instance, example, make_engine=diagnostic_engine).first_failure
    example = build_example(instance, plan, split='train')
    example.n_actions += 1
    assert 'n_actions' in verify_replay(instance, example, make_engine=diagnostic_engine).first_failure


def synthetic_publication(split='train', n_boxes=1, member_index=0, instance_versions=None):
    """Synthetic policy evidence only: these reports do not certify physical gold."""
    from copy import deepcopy
    from hashlib import sha256
    import json
    from eval.splits import load_inventory, spec_key
    from stowage.model import (Instance, BallastCondition, CheckReport, PartReport, MoveCount,
                               VoyageConditionIdentity, VoyageSpec, VoyageTankPolicy)
    from llm.stowlm.sft_data import current_gold_component_versions
    from stowage.vessel.fleet_voyage import build_voyage_policy
    from tests.llm.test_sft_data_fixtures import deterministic_case
    inventory = load_inventory()
    frozen = [e for e in inventory.manifest['entries'] if e['split'] == split][member_index]
    spec = deepcopy(frozen['spec'])
    instance, plan = deterministic_case(n_boxes, hull=True)
    instance.seed = spec['seed']
    instance.family = {key: ('true' if value is True else 'false' if value is False else str(value))
                       for key, value in spec.items() if value is not None}
    instance.family.update(size=spec['rung'], requested_n_boxes='auto')
    instance.provenance.created_by = {'simulated': 'generate.instances', 'random_yard': 'generate.random_yard'}[spec['generator']]
    from stowage.versions import CARGO_GENERATOR_VERSION, RANDOM_YARD_VERSION, SIMULATED_INSTANCES_VERSION
    from stowage.generate.instances_v2_random import RANDOM_V2_VERSION
    from stowage.vessel.fleet_conditions import CONDITION_VERSION
    instance.provenance.versions.update(dict(instances=SIMULATED_INSTANCES_VERSION,
        random_yard=RANDOM_YARD_VERSION, random_yard_v2=RANDOM_V2_VERSION,
        cargo_generator=CARGO_GENERATOR_VERSION, slice_condition=CONDITION_VERSION))
    if instance_versions:
        instance.provenance.versions.update(instance_versions)
    instance.vessel.voyage_policy = build_voyage_policy(instance.vessel.tanks,
        parameter_source='synthetic SFT policy test', vessel_id=instance.vessel.id)
    instance.voyage = VoyageSpec(**{phase: VoyageConditionIdentity(
        id=f'synthetic.{phase}', phase=phase, call_index=i, locode=instance.rotation.calls[i].locode,
        environment_id='synthetic-policy', density_id='synthetic-water', water_density_kg_m3=1025.)
        for i, phase in enumerate(('departure', 'arrival'))})
    instance = Instance.model_validate(instance.model_dump())
    plan.ballast = BallastCondition(tank_masses_kg={}, source='planner')
    # Registry pins define the current epoch; retain the producer's extra solver keys.
    versions = {**current_gold_component_versions(), **component_versions()}
    names = ['moves', 'rules', 'stability', 'strength', 'visibility', 'lashing', 'constraints', 'voyage']
    names += [f'condition:{identity.id}:{part}' for identity in (instance.voyage.departure, instance.voyage.arrival)
              for part in ('stability', 'strength')]
    parents = [f'checker.{kind}.sha256:' + sha256(json.dumps(value.model_dump(mode='json'), sort_keys=True,
               separators=(',', ':'), allow_nan=False).encode()).hexdigest()
               for kind, value in [('instance', instance), ('plan', plan)]]
    report = CheckReport(passed=True, count=MoveCount(), parts={n: PartReport(name=n) for n in names},
                         provenance=Provenance(parents=parents, versions=versions))
    raw_input = instance.model_dump_json().encode()
    pins = dict(split_version=inventory.version, split_entries_sha256=inventory.entries_sha256)
    candidate = dict(accepted=True, shippable=True, mask_verified=True, global_screen='on',
                     plan=plan.model_dump(mode='json'), report=report.model_dump(mode='json'))
    record = dict(format='tiny-gold@1.0', accepted=True, split=split, instance_id=instance.id,
        spec_key=spec_key(spec), split_spec=spec, input_sha256=sha256(raw_input).hexdigest(),
        relevant_source_sha256='a' * 64, component_versions=versions, gold_source='exact', exact=candidate,
        plan=candidate['plan'], check_report=candidate['report'], shippable=True, mask_verified=True,
        c2='on/default', actions_schema=plan.action_space, **pins)
    def publish():
        raw = json.dumps(record, sort_keys=True).encode()
        entry = dict(path=f'plans/{spec_key(spec)}.json', sha256=sha256(raw).hexdigest(),
                     input_sha256=record['input_sha256'], instance_id=instance.id, split=split,
                     spec_key=record['spec_key'], checker_passed=True, shippable=True, mask_verified=True, **pins)
        return dict(format='tiny-gold-manifest@1.0', shards=[entry], entry=entry, record_bytes=raw,
                    input_bytes=raw_input, record_sha256=entry['sha256'], component_versions=versions,
                    relevant_source_sha256='a' * 64, **pins)
    return instance, plan, record, publish


def synthetic_production_replay(instance, example, **kwargs):
    """Explicit test oracle for policy plumbing; never physical C2 qualification."""
    from llm.stowlm.mask import MASK_VERSION, MaskConfig
    from llm.stowlm.sft_data import ReplayReport
    from dataclasses import asdict
    return ReplayReport(True, example.n_actions, example.completion_len, global_screen='on',
                        production=True, mask_version=MASK_VERSION, mask_config=asdict(MaskConfig()))


def test_synthetic_published_gold_policy_admission(monkeypatch):
    from llm.stowlm import sft_data
    instance, plan, record, publish = synthetic_publication()
    monkeypatch.setattr(sft_data, 'verify_replay', synthetic_production_replay)
    monkeypatch.setattr(sft_data, 'current_gold_source_sha256', lambda: 'a' * 64)
    example = sft_data.build_gold_example(instance, plan, split='train', gold_record=record, publication=publish())
    assert example.meta['gold']['published'] is True
    assert len(example.meta['gold']['condition_parts']) == 4
    assert example.meta['gold']['voyage'] == instance.voyage.model_dump(mode='json')
    assert example.meta['replay']['production']
    assert example.meta['replay']['example_sha256'] == sft_data.example_sha256(example)
    assert example.meta['gold']['checker_version'] == component_versions()['checker']


@pytest.mark.parametrize('component', ['instances', 'random_yard', 'slice_condition'])
def test_gold_admission_rejects_stale_instances_own_generator_versions(monkeypatch, component):
    from llm.stowlm import sft_data
    from eval.splits import load_inventory
    members = [row for row in load_inventory().manifest['entries'] if row['split']=='train']
    generator = 'random_yard' if component=='random_yard' else 'simulated'
    index = next(i for i, row in enumerate(members) if row['spec']['generator']==generator)
    instance, plan, record, publish = synthetic_publication(member_index=index,
        instance_versions={component: 'old-distribution'})
    monkeypatch.setattr(sft_data, 'verify_replay', synthetic_production_replay)
    monkeypatch.setattr(sft_data, 'current_gold_source_sha256', lambda: 'a' * 64)
    with pytest.raises(ValueError, match='instance.*generator|instance.*condition'):
        sft_data.build_gold_example(instance, plan, split='train', gold_record=record, publication=publish())


@pytest.mark.parametrize('component', ['checker', 'feasibility', 'schema'])
def test_gold_rejects_matching_stale_component_epoch(monkeypatch, component):
    """A self-consistent old record/manifest must still fail current-pin admission."""
    from llm.stowlm import sft_data
    instance, plan, record, publish = synthetic_publication()
    record['component_versions'] = {**record['component_versions'],
                                    component: 'stale-' + component_versions()[component]}
    publication = publish()
    publication['component_versions'] = dict(record['component_versions'])
    monkeypatch.setattr(sft_data, 'verify_replay', lambda *a, **kw: pytest.fail('stale epoch reached replay'))
    with pytest.raises(ValueError, match='gold checker/component epoch mismatch'):
        sft_data.build_gold_example(instance, plan, split='train', gold_record=record, publication=publication)


@pytest.mark.parametrize('defect,match', [
    ('unpublished', 'published'), ('record_bytes', 'checksum'), ('input_bytes', 'input bytes'),
    ('stale_pin', 'release pin'), ('false_split', 'split'), ('missing_binding', 'bound'),
    ('missing_arrival', 'D50'), ('failed_checker', 'checker'), ('tainted_report', 'stub-tainted'),
    ('c2_off', 'C2'), ('stale_checker', 'epoch'), ('actions_mismatch', 'candidate'),
])
def test_gold_admission_refuses_invalid_published_evidence(monkeypatch, defect, match):
    from llm.stowlm import sft_data
    instance, plan, record, publish = synthetic_publication()
    monkeypatch.setattr(sft_data, 'verify_replay', synthetic_production_replay)
    monkeypatch.setattr(sft_data, 'current_gold_source_sha256', lambda: 'a' * 64)
    if defect == 'missing_binding':
        record['check_report']['provenance']['parents'] = []
    elif defect == 'missing_arrival':
        del record['check_report']['parts'][f'condition:{instance.voyage.arrival.id}:strength']
    elif defect == 'failed_checker': record['check_report']['passed'] = False
    elif defect == 'tainted_report': record['check_report']['provenance']['stubs'] = {'oracle': 'diagnostic only'}
    elif defect == 'stale_checker':
        record['check_report']['provenance']['versions']['checker'] = 'stale-' + component_versions()['checker']
    elif defect == 'c2_off': record['exact']['global_screen'] = 'off/diagnostic'
    elif defect == 'actions_mismatch': record['plan']['id'] = 'other-plan'
    if defect in ('missing_binding', 'missing_arrival', 'failed_checker', 'tainted_report', 'stale_checker'):
        record['exact']['report'] = record['check_report']
    publication = publish()
    if defect == 'unpublished': publication['shards'] = []
    elif defect == 'record_bytes': publication['record_bytes'] = b'{}'
    elif defect == 'input_bytes': publication['input_bytes'] = b'{}'
    elif defect == 'stale_pin': publication['split_entries_sha256'] = 'b' * 64
    elif defect == 'false_split': publication['entry']['split'] = 'val'
    with pytest.raises((ValueError, StubTaintError), match=match):
        sft_data.build_gold_example(instance, plan, split='train', gold_record=record, publication=publication)


def test_gold_requires_actual_production_replay(monkeypatch):
    from llm.stowlm import sft_data
    monkeypatch.setattr(sft_data, 'current_gold_source_sha256', lambda: 'a' * 64)
    instance, plan, record, publish = synthetic_publication()
    with pytest.raises(ValueError, match='production replay failed'):
        sft_data.build_gold_example(instance, plan, split='train', gold_record=record, publication=publish())


def test_gold_rejects_matching_stale_source_epoch(monkeypatch):
    from llm.stowlm import sft_data
    instance, plan, record, publish = synthetic_publication()
    monkeypatch.setattr(sft_data, 'current_gold_source_sha256', lambda: 'b' * 64)
    with pytest.raises(ValueError, match='source epoch'):
        sft_data.build_gold_example(instance, plan, split='train', gold_record=record, publication=publish())
