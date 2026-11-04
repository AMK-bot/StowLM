"""Shared resume/report regressions, with fully sealed manifest fixtures."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent


def module(name):
    spec = importlib.util.spec_from_file_location('hybrid_test_' + name, HERE/(name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


validation = module('validation')
admission = module('admission')


def admission_evidence(mask=4095, load=45., age=1.):
    observed = datetime(2026, 10, 31, 19, tzinfo=timezone.utc).timestamp()
    return admission.state_evidence(dict(mask=mask, load1=load, t=observed-age), observed_at=observed)


def manifest_for(n=10):
    maps = {f'random.small.{i}.0.2': f'experiments/mvp/corpus/val/small/feeder_500/{i}.json' for i in range(n)}
    result = dict(decision='D199', arms=['none', 'llm', 'random'], n_maps=n, maps=maps,
                  inputs={p: 'a'*64 for p in maps.values()}, runtime=dict(id='b'*64), cohort_status='active',
                  candidates={}, candidate_paths=dict(llm=[], random=[]), seed=0, search_s_per_box=6,
                  search_cap_s=120, search_floor_s=20, finalize_s=300, workers=4,
                  watchdog_policy=dict(planner_s=3600, audit_s=600, terminate_grace_s=5, kill_grace_s=5),
                  admission_rule=deepcopy(admission.RULE))
    result['cohort_id'] = validation.cohort_id(result)
    return result


def row(i, cost, arm, manifest, *, extra=False, passed=True):
    iid = f'random.small.{i}.0.2'
    inp = manifest['maps'][iid]
    result = dict(instance_id=iid, input=inp, arm=arm, cohort_id=manifest['cohort_id'],
                  binding=validation.expected_binding(manifest, inp), weighted=cost, passed=passed,
                  completed=True, final_origin=f'{arm}:sample{i}' if extra else 'blocked',
                  final_search_changed=extra, repaired=False, start_utc='2026-10-31T19:00:00+00:00',
                  end_utc='2026-10-31T19:01:00+00:00', load_start=[1, 2, 3], load_end=[2, 3, 4], wall_s=60,
                  admission=admission_evidence())
    if passed:
        result['plan'] = dict(instance_id=iid, moves=[], actions=[dict(op='P', box='A', slot='1')])
        result['plan_sha256'] = validation.plan_sha256(result['plan'])
        result['audit'] = dict(instance_id=iid, input=inp, arm=arm, cohort_id=manifest['cohort_id'],
                               runtime_id=manifest['runtime']['id'], weighted=cost, stored=cost,
                               passed=True, stored_passed=True, fresh_process=True, legal=True,
                               mask_ok=True, complete=True, all_loaded=True, repaired=False,
                               plan_sha256=result['plan_sha256'])
        boxes = ['A', 'B', 'C', 'D']
        seed = {box: str(j + (10 if extra else 0)) for j, box in enumerate(boxes)}
        evidence = validation.decision_contribution(result['final_origin'], boxes, seed, seed)
        result.update(final_search_origin=result['final_origin'], final_load_list=boxes,
                      final_seed_decisions=seed, final_contribution=evidence,
                      final_reaching_runs=[], final_shared_stow=False,
                      starts=[dict(origin=result['final_origin'], seed_decisions=seed, objective=cost)])
        result['audit']['final_contribution'] = deepcopy(evidence)
        result['candidates'] = dict(admitted=[dict(label=result['final_origin'], cost=cost)] if extra else [])
        result['extra_starts'] = [dict(origin=result['final_origin'], seed_decisions=deepcopy(seed),
                                      replayed=True, sight_clear=True)] if extra else []
        result['audit']['admitted_seed_decisions'] = {result['final_origin']: deepcopy(seed)} if extra else {}
    return result


def set_cost(result, cost):
    result['weighted'] = cost
    if 'audit' in result:
        result['audit'].update(weighted=cost, stored=cost)


def good_arms(n=10):
    manifest = manifest_for(n)
    arms = {a: {} for a in manifest['arms']}
    for arm, cost in (('none', 100), ('llm', 97), ('random', 99)):
        for i in range(n):
            result = row(i, cost, arm, manifest, extra=(arm == 'llm' and i < 2))
            arms[arm][result['instance_id']] = result
    return arms, manifest


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.manifest = manifest_for()
        self.row = row(0, 97, 'llm', self.manifest, extra=True)

    def valid(self, result=None, **kwargs):
        return validation.validate_row(self.row if result is None else result, manifest=self.manifest, **kwargs)

    def test_valid_pass_and_completed_failure_share_context_validation(self):
        self.assertTrue(self.valid())
        failure = row(0, None, 'llm', self.manifest, passed=False)
        self.assertTrue(self.valid(failure))
        failure['completed'] = False
        self.assertFalse(self.valid(failure))
        self.assertTrue(self.valid(failure, require_complete=False))

    def test_stale_plan_audit_and_three_cost_disagreement(self):
        stale = deepcopy(self.row)
        stale['plan']['actions'][0]['slot'] = '2'
        self.assertFalse(self.valid(stale))
        stale['plan_sha256'] = validation.plan_sha256(stale['plan'])
        self.assertFalse(self.valid(stale))
        for target, key in (('audit', 'weighted'), ('audit', 'stored'), ('row', 'weighted')):
            broken = deepcopy(self.row)
            (broken if target == 'row' else broken['audit'])[key] = 1000
            self.assertFalse(self.valid(broken))

    def test_every_full_audit_flag_hash_and_no_repair_is_required(self):
        for key in ('passed', 'stored_passed', 'fresh_process', 'legal', 'mask_ok', 'complete', 'all_loaded'):
            broken = deepcopy(self.row)
            broken['audit'][key] = False
            self.assertFalse(self.valid(broken), key)
        for target, key in (('row', 'plan_sha256'), ('audit', 'plan_sha256'), ('audit', 'repaired')):
            broken = deepcopy(self.row)
            (broken if target == 'row' else broken['audit'])[key] = True
            self.assertFalse(self.valid(broken))

    def test_foreign_cohort_runtime_input_and_arm_are_rejected(self):
        for target, key, value in (('row', 'cohort_id', 'f'*64), ('audit', 'cohort_id', 'f'*64),
                                  ('audit', 'runtime_id', 'f'*64), ('audit', 'arm', 'random'),
                                  ('audit', 'input', 'foreign.json'), ('row', 'instance_id', 'random.small.foreign.0.2')):
            broken = deepcopy(self.row)
            (broken if target == 'row' else broken['audit'])[key] = value
            self.assertFalse(self.valid(broken), key)
        self.assertFalse(self.valid(expected_arm='random'))
        self.assertFalse(self.valid(expected_iid='random.small.1.0.2'))
        self.assertFalse(self.valid(expected_binding={}))

    def test_manifest_is_sealed_and_inactive_cohorts_fail_even_if_resealed(self):
        changed = deepcopy(self.manifest)
        changed['runtime']['id'] = 'f'*64
        self.assertFalse(validation.validate_row(self.row, manifest=changed))
        for flag in ('quarantined', 'aborted'):
            changed = deepcopy(self.manifest)
            changed[flag] = True
            changed['cohort_id'] = validation.cohort_id(changed)
            self.assertTrue(validation.manifest_errors(changed))
        changed = deepcopy(self.manifest)
        changed['cohort_status'] = 'quarantined'
        changed['cohort_id'] = validation.cohort_id(changed)
        self.assertTrue(validation.manifest_errors(changed))

    def test_costs_must_be_finite_real_nonnegative_numbers(self):
        for cost in (float('nan'), float('inf'), -1, True, '97'):
            broken = deepcopy(self.row)
            broken['weighted'] = cost
            self.assertFalse(self.valid(broken))

    def test_missing_or_unsafe_admission_fails_and_large_mask_never_increases_workers(self):
        broken = deepcopy(self.row)
        del broken['admission']
        self.assertFalse(self.valid(broken))
        for key, value in (('load_1m', 45.01), ('core_mask', 4094), ('allowed', False), ('source', ''),
                           ('source_path', '/wrong/core-state.json'), ('core_count', 18),
                           ('age_s', 120), ('t', 0), ('observed_at', 0)):
            broken = deepcopy(self.row)
            broken['admission'][key] = value
            self.assertFalse(self.valid(broken))
        broken = deepcopy(self.row)
        broken['admission'] = admission_evidence(mask=262143)
        self.assertTrue(self.valid(broken))
        manifest = deepcopy(self.manifest)
        manifest['workers'] = 6
        manifest['cohort_id'] = validation.cohort_id(manifest)
        self.assertTrue(validation.manifest_errors(manifest))

    def test_freshness_and_popcount_are_recomputed_from_exact_reading(self):
        for mask, age, expected in ((975, 119.99, True), (255, 0, True), (1 << 30, 1, False),
                                    (127, 1, False), (4095, 120, False), (4095, -.01, False)):
            broken = deepcopy(self.row)
            broken['admission'] = admission_evidence(mask=mask, age=age)
            self.assertEqual(self.valid(broken), expected, (mask, age))
        for key, value in (('mask', True), ('mask', -1), ('load1', float('nan')), ('t', 'epoch')):
            broken = deepcopy(self.row)
            broken['admission']['raw'][key] = value
            self.assertFalse(self.valid(broken))
        broken = deepcopy(self.row)
        broken['start_utc'] = '2026-10-31T19:00:01+00:00'
        self.assertFalse(self.valid(broken))

    def test_declared_groups_cover_maps_and_d205_has_fixed_32_plus_8(self):
        manifest = manifest_for(40)
        ids = list(manifest['maps'])
        manifest.update(amendments=['D205', 'D206'], groups=dict(entering=ids[:32], aa=ids[32:]))
        manifest['cohort_id'] = validation.cohort_id(manifest)
        self.assertFalse(validation.manifest_errors(manifest))
        for groups in (dict(entering=ids[:31], aa=ids[31:]),
                       dict(entering=ids[:32], aa=ids[:8]),
                       dict(entering=ids[:32], aa=ids[33:]), None):
            broken = deepcopy(manifest)
            broken['groups'] = groups
            broken['cohort_id'] = validation.cohort_id(broken)
            self.assertTrue(validation.manifest_errors(broken))

    def test_retained_decisions_must_match_fresh_audit_and_seed_record(self):
        broken = deepcopy(self.row)
        broken['final_contribution']['retained_count'] = 0
        broken['audit']['final_contribution'] = deepcopy(broken['final_contribution'])
        self.assertFalse(self.valid(broken))
        broken = deepcopy(self.row)
        broken['audit']['final_contribution']['final_decisions']['A'] = 'foreign'
        self.assertFalse(self.valid(broken))
        broken = deepcopy(self.row)
        broken['starts'] = []
        self.assertFalse(self.valid(broken))

    def test_admitted_metadata_and_seed_maps_are_bound_to_fresh_audit(self):
        broken = deepcopy(self.row)
        broken['candidates']['admitted'][0]['label'] = 'adapter:edited'
        self.assertFalse(self.valid(broken))
        broken = deepcopy(self.row)
        broken['extra_starts'][0]['seed_decisions']['A'] = 'edited'
        self.assertFalse(self.valid(broken))
        broken = deepcopy(self.row)
        broken['audit']['admitted_seed_decisions'] = {}
        self.assertFalse(self.valid(broken))
        broken = deepcopy(self.row)
        broken['candidates']['admitted'] = [dict(label='master')]
        broken['extra_starts'] = [dict(origin='master', seed_decisions=broken['final_seed_decisions'])]
        broken['audit']['admitted_seed_decisions'] = {'master': broken['final_seed_decisions']}
        self.assertFalse(self.valid(broken))
        broken = deepcopy(self.row)
        forged_seed = dict(broken['final_seed_decisions'], A='forged')
        broken['final_seed_decisions'] = forged_seed
        broken['starts'][0]['seed_decisions'] = forged_seed
        evidence = validation.decision_contribution(broken['final_origin'], broken['final_load_list'],
                    forged_seed, broken['final_contribution']['final_decisions'])
        broken['final_contribution'] = evidence
        broken['audit']['final_contribution'] = deepcopy(evidence)
        self.assertFalse(self.valid(broken))


if __name__ == '__main__':
    unittest.main()
