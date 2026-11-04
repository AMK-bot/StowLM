"""K1b's ranking, outcomes, pairing and verdict on synthetic rows."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import judge
import k1b_report as r


def test_rank_is_cheapest_first_greedy_on_ties_and_dedupes():
    entries = [dict(cost=20, sample=3, signature='a'), dict(cost=20, sample=0, signature='b'), dict(cost=18, sample=9, signature='c'), dict(cost=25, sample=1, signature='a')]
    assert [e['sample'] for e in judge.rank(entries)] == [9, 0, 3]


def test_outcome_labels():
    assert judge.outcome(0, []) == 'dead end'
    assert judge.outcome(4, [dict(passed=False, timed_out=True), dict(passed=True)]) == 'pass'
    assert judge.outcome(4, [dict(passed=False, timed_out=True), dict(passed=False)]) == 'finisher limit'
    assert judge.outcome(4, [dict(passed=False)]) == 'checker or finisher fail'


def arms(mine, rival):
    keys = [f'm{i}' for i in range(r.N)]
    return dict(zip(keys, mine)), dict(zip(keys, rival))


def test_verdict_thresholds():
    p = r.pair(*arms([11]*48+[None]*7, [10]*50+[None]*5))
    v = r.verdict(p)
    assert p['delta'] == pytest.approx(.1) and v['moves_met'] and v['passes_met'] and v['k1b_passes'] and not v['unparks']
    assert not r.verdict(r.pair(*arms([11]*47+[None]*8, [10]*50+[None]*5)))['passes_met']      # 47 < 48
    assert not r.verdict(r.pair(*arms([12]*50+[None]*5, [10]*50+[None]*5)))['moves_met']       # +20 %
    both = r.pair(*arms([10]*50+[None]*4+[9], [10]*50+[None]*5))
    assert r.verdict(both)['unparks'] and both['only_left'] == ['m54']


def test_arms_must_cover_the_same_maps():
    mine, rival = arms([1]*55, [1]*55)
    rival.pop('m0')
    with pytest.raises(SystemExit):
        r.pair(mine, rival)


def test_judge_reuses_a_row_only_with_its_binding_and_winner_bytes(tmp_path):
    import json
    model, plans = tmp_path/'judged/model', tmp_path/'judged/plans'
    plans.mkdir(parents=True)
    model.mkdir()
    plan, report = judge.publish(plans/'m.json', '{"p": 1}'), judge.publish(plans/'m.report.json', '{"r": 1}')
    path = model/'m.json'
    path.write_text(json.dumps(dict(binding=dict(a=1), moves=20, winner=dict(plan_sha256=plan, report_sha256=report))))
    assert judge.cached(path, dict(a=2), 'plans') is None
    assert judge.cached(path, dict(a=1), 'plans')['moves'] == 20
    (plans/'m.json').write_text('{}')
    assert judge.cached(path, dict(a=1), 'plans') is None  # same file name, other bytes
    (plans/'m.json').unlink()
    assert judge.cached(path, dict(a=1), 'plans') is None


def test_root_is_the_repository():
    assert (judge.ROOT/'pyproject.toml').is_file() and (judge.TWOSTEP.parent.name == 'strategy')


def test_small_p_values_are_not_shown_as_zero():
    assert r.show_p(3.8e-11) == '< 0.0001' and r.show_p(.0428) == '0.0428'


def test_k1b_uses_the_mask_gate_not_a_copy():
    source = Path(judge.__file__).read_text()
    assert source.count('compact.admit_mask(check, compact.mask_ok(instance, finished.plan))') == 2 and "['mask_replay']" not in source


def test_blockrule_rows_read_the_compact_selected_column():
    import pair_blockrule as b
    text = ('## small-train\n| small-a-b-1 | 2 | x | x | 1 | 1 | 1 x | 1 | 1 | 99.0 rule |\n## small-val\n'
            '| small-feeder_500-random_yard-041 | — | o | o | 3 | 2 | 2 rule | 3 | 2 | 12.0 rule |\n'
            '| small-feeder_500-random_yard-042 | — | o | o | — | — | — default | — | — | — default |\n'
            '| small-feeder_500-random_yard-043 | — | o | o | — | — | — default | — | — | pending |\n')
    assert b.blockrule_rows(text) == {'small-feeder_500-random_yard-041': 12., 'small-feeder_500-random_yard-042': None}
    assert b.map_name('experiments/mvp/corpus/val/small/feeder_800/simulated_yard/seed-041100001.json') == 'small-feeder_800-simulated_yard-041100001'


def test_k1c_fallback_is_block_rule_selection():
    import k1c_report as c
    masked = dict(a=10, b=30, c=None, d=20, e=None)
    plain = dict(a=20, b=20, c=15, d=None, e=None)
    assert c.with_fallback(masked, plain) == dict(a=10, b=20, c=15, d=20, e=None)
    assert c.with_fallback(dict(a=20), dict(a=20)) == dict(a=20)  # a tie keeps the unconstrained plan


def test_portfolio_is_best_passing_cost():
    import k1c_report as c
    assert c.portfolio(dict(a=10, b=None), dict(a=None, b=None), dict(a=12, b=None)) == dict(a=10, b=None)


def test_k1c_verdict():
    import k1c_report as c
    assert c.verdict(dict(delta=-.05, mine=51, rival=51))['passed']
    assert not c.verdict(dict(delta=-.04, mine=51, rival=51))['passed']
    assert not c.verdict(dict(delta=-.2, mine=50, rival=51))['passed']
    assert not c.verdict(dict(delta=None, mine=51, rival=51))['passed']


def test_port_constraint_matches_the_rule_on_a_real_map():
    import port_mask
    from stowage.io import load_instance
    from experiments.mvp.strategy.headroom.layout import forbids
    instance = load_instance(judge.ROOT/'experiments/mvp/corpus/val/small/feeder_1700/random_yard/seed-041100002.json')
    constraint = port_mask.PortConstraint(instance)
    box = instance.load_list[0]
    slots = sorted({c.id for c in instance.vessel.cells})
    answers = [constraint.forbids(box, s) for s in slots]
    assert any(answers) and not all(answers)  # the rule closes some slots to this box and leaves others open
    assert answers == [forbids(instance, constraint.layout, box, s, constraint.model) for s in slots]


def test_k1r_rewind_depth_rule_matches_generate_backtracking():
    import k1r
    assert k1r.next_k(0, 5, -1) == 1      # deeper than any failure
    assert k1r.next_k(1, 5, 5) == 2
    assert k1r.next_k(8, 5, 5) == 8       # capped
    assert k1r.next_k(4, 6, 5) == 1


def test_k1r_choice_is_uniform_over_open_actions_only():
    import random
    import k1r
    rng = random.Random(0)
    seen = {k1r.choose(['a', 'b', 'c'], {'b'}, rng) for _ in range(50)}
    assert seen == {'a', 'c'} and k1r.choose(['a'], {'a'}, rng) is None


def test_k1r_is_seed_stable():
    import random
    import k1r
    first = [k1r.choose(list('abcdef'), set(), random.Random(3)) for _ in range(3)]
    assert len(set(first)) == 1


def test_kcurve_walks_to_the_first_pass_within_k():
    import kcurve
    entries = [dict(sample=0, cost=30, signature='g'), dict(sample=1, cost=20, signature='x'), dict(sample=5, cost=10, signature='y')]
    verdicts = dict(g=dict(passed=True, moves=30), x=dict(passed=False), y=dict(passed=True, moves=10))
    verdict = lambda e: verdicts[e['signature']]
    assert kcurve.walk(entries, 1, verdict) == (30, 30, 1)
    assert kcurve.walk(entries, 2, verdict) == (30, 20, 2)      # x is cheaper but fails, so g stands
    assert kcurve.walk(entries, 8, verdict) == (10, 10, 1)
    assert kcurve.walk([], 8, verdict) == (None, None, 0)


def test_k1r_reading():
    import k1r_report as q
    assert q.reading(dict(delta=-.05, mine=51, rival=51)) and q.reading(dict(delta=.04, mine=51, rival=51))
    assert not q.reading(dict(delta=.06, mine=51, rival=51)) and not q.reading(dict(delta=0., mine=50, rival=51))


def test_k2_verdict_needs_a_map_both_sweep_arms_fail():
    import k2ladder as q
    row = lambda name, moves, sweep: dict(map_id=name, moves=moves, sweep=sweep)
    assert q.verdict([row('a', 90, dict(default=False, rule=False))]) == dict(met=True, wins=['a'], pending=[])
    assert not q.verdict([row('a', 90, dict(default=True, rule=False))])['met']
    assert not q.verdict([row('a', None, dict(default=False, rule=False))])['met']
    assert q.verdict([row('a', 90, None)]) == dict(met=False, wins=[], pending=['a'])


def test_k2_selects_the_registered_maps():
    import k2ladder as q
    chosen = q.maps()
    assert len(chosen) == 18 and {m['fill_target'] for m in chosen} == {40, 60} and {m['mass_level'] for m in chosen} == {'low'}
    assert all('/train/' in m['input'] for m in chosen) and chosen[0]['n_loads'] <= chosen[-1]['n_loads']
    flags = q.flags()
    assert flags[flags.index('--time-limit')+1] == '3600' and flags[flags.index('--n-samples')+1] == '16' and '--port-rule' not in flags


def test_k1r_miss_names_its_side():
    import k1r_report as q
    assert 'random is the better side' in q.outside(dict(delta=-.052, mine=51, rival=51, moves_p=.135))
    assert 'null is the better side' in q.outside(dict(delta=.06, mine=51, rival=51, moves_p=.1))
    assert q.outside(dict(delta=-.01, mine=50, rival=51, moves_p=.1)) == '**The condition is not met.**'


def test_k1c_chance_reading_names_a_miss():
    import k1c_chance_report as q
    assert q.reading(dict(delta=.05, mine=51, rival=51)) and not q.reading(dict(delta=.05, mine=50, rival=51))
    assert 'fewer moves than K1c' in q.read_out(dict(delta=-.2, mine=51, rival=51, moves_p=.01))
    assert 'passes 50 vs 51' in q.read_out(dict(delta=-.2, mine=50, rival=51, moves_p=.01))


def test_random_deadline_fires_inside_sampling():
    import time
    import k2r_decode as d
    live = d.DeadlineRandom(1, time.monotonic()+60)
    assert live.choice(['a']) == 'a'
    with pytest.raises(d.Expired):
        d.DeadlineRandom(1, time.monotonic()-1).choice(['a'])


def test_deadline_draws_match_k1r_seeds():
    import random
    import time
    import k2r_decode as d
    items = list('abcdefgh')
    assert [d.DeadlineRandom(4, time.monotonic()+60).choice(items) for _ in range(1)] == [random.Random(4).choice(items)]


def test_a_resume_keeps_the_time_already_spent(tmp_path):
    import k2r_decode as d
    path = tmp_path/'clock.json'
    assert d.load_clock(path) == 0.
    d.save_clock(path, 1234.5)
    assert d.load_clock(path) == 1234.5


def test_interrupted_sample_is_not_stored():
    source = (Path(__file__).resolve().parent/'k2r_decode.py').read_text()
    assert source.index('except Expired') < source.index("path = folder/f'sample-{i:02d}.json'")


def test_ladder_win_counts_against_chance_only_if_random_fails():
    import ladder_report as l
    model = dict(a=dict(moves=9), b=dict(moves=9), c=dict(moves=None))
    chance = dict(a=dict(moves=9), b=dict(moves=None), c=dict(moves=9))
    sweep = dict(a=dict(default=False, rule=False), b=dict(default=False, rule=False), c=dict(default=False, rule=False))
    assert l.chance_clears(model, chance, sweep) == (['a', 'b'], ['a'])


def test_port_audit_gates_only_the_rule_masked_plan():
    import chance_final as f
    import k1c_report as c
    masked, plain = dict(a=10, b=12), dict(a=20, b=20)
    admitted = f.admit_rule(masked, dict(a=['breach'], b=[]))
    assert admitted == dict(a=None, b=12)
    assert c.with_fallback(admitted, plain) == dict(a=20, b=12)  # the fallback stays, unaudited


def test_config_check_catches_a_wrong_generation():
    import chance_final as f
    parameters = dict(f.PLANNER, adapter_path='x')
    config = dict(planner_parameters=parameters, seed=0, allow_repair=False, active_stubs={}, inputs=[dict(path='i', sha256='h')])
    assert f.config_problems('a', config, True, dict(i='h')) == []
    assert f.config_problems('a', config, False, dict(i='h'))        # adapter where none is expected
    assert f.config_problems('a', config, True, dict(i='other'))     # input changed
    assert f.config_problems('a', dict(config, planner_parameters=dict(parameters, n_samples=8)), True, dict(i='h'))


def test_sample_check_catches_a_foreign_sample():
    import json
    import chance_final as f
    good = {f'sample-{i:02d}.json': json.dumps(dict(sample=i, seed=i, temperature='uniform', instance_id='m', status='dead_end', plan=None)) for i in range(16)}
    assert f.sample_problems('a', 'm', good, False) == []
    assert f.sample_problems('a', 'm', good, True)           # a random sample in a model arm
    assert f.sample_problems('a', 'm', dict(list(good.items())[:15]), False)


class FakeEngine:
    index_boxes, n_slots = ('a', 'b'), 3

    def __init__(self, history=()):
        self.history = list(history)

    def actions(self):
        return list(self.history)

    def legal_index(self):
        return list(range(6))

    def legal_discharges(self):
        return ()

    def is_complete(self):
        return False

    def dead_end(self):
        return None

    def legal_actions(self):
        from stowage.actions.space import Place
        return [Place('a', 's'), Place('b', 's')]

    def is_legal(self, action):
        return True

    depth = 0

    def push(self, action):
        self.history.append(action)

    def copy(self):
        return FakeEngine(self.history)


def test_one_placement_forbids_only_boxes_already_placed():
    import one_place
    from stowage.actions.space import Place
    from stowage.feasibility import IllegalActionError
    engine = one_place.OnePlacementEngine(FakeEngine())
    assert list(engine.legal_index()) == list(range(6))
    engine.push(Place('a', 's'))
    assert list(engine.legal_index()) == [3, 4, 5]            # box a's row of the box × slot table is gone
    assert engine.legal_actions() == [Place('b', 's')] and not engine.is_legal(Place('a', 't')) and engine.is_legal(Place('b', 't'))
    with pytest.raises(IllegalActionError):
        engine.push(Place('a', 't'))
    copied = engine.copy()
    copied.push(Place('b', 's'))
    assert engine.placed == {'a'} and copied.placed == {'a', 'b'}   # copies don't share state
    assert copied.dead_end().reason == 'one placement leaves no legal action'


def test_85a_repeats_and_steps():
    import k85a
    assert k85a.repeats(1253, 100) == 5                 # 500 of 1,753 is 28.5 %
    assert k85a.repeats(1253, 20) == 27
    assert k85a.iters_for(1753) == 1752 and k85a.iters_for(1790) == 1788


def test_85a_refuses_rule_rows_on_val_maps_and_repeats_only_rule_rows():
    import json
    import k85a
    rule = [json.dumps(dict(instance_id='r'))]
    assert k85a.merge(['b'], rule, {'v'}, 3) == ['b', rule[0], rule[0], rule[0]]
    with pytest.raises(SystemExit):
        k85a.merge(['b'], rule, {'r'}, 3)


def test_85a_regression_label():
    import k85a
    assert k85a.regression(40) == 'REGRESSION' and k85a.regression(44) == 'kept' and k85a.regression(42) == 'slipped'


def test_85a_waits_unless_reader_v2_passes_every_gate(tmp_path):
    import json
    import k85a
    path = tmp_path/'gates.json'
    assert not k85a.reader_passed(path)                                 # missing: a miss
    path.write_text(json.dumps(dict(R1=True, R2=True, R3=True)))
    assert k85a.reader_passed(path)
    path.write_text(json.dumps(dict(R1=True, R2=False, R3=True)))
    assert not k85a.reader_passed(path)
