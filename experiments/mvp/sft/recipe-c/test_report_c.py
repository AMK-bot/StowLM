"""D167 rule, D158 label, reuse rule and pairing on synthetic rows; no solver, no model."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polish_c
import report_c as r


def rows(*moves):
    return {f'm{i}': dict(moves=m, outcome=r.OWN if m is not None else 'dead end') for i, m in enumerate(moves)}


def test_pair_uses_mutual_maps_only():
    p = r.pair(rows(10, 20, None, 5), rows(20, None, 7, 5))
    assert (p['mutual'], p['only_left'], p['only_right'], p['fewer'], p['equal'], p['more']) == (2, 1, 1, 1, 1, 0)
    assert p['delta'] == pytest.approx(15/25-1)


def test_exact_sign_test_matches_d147():
    assert r.sign_p(6, 2) == pytest.approx(.2890625)
    assert r.sign_p(0, 0) == 1.


def test_d167_rule_needs_passes_and_three_percent():
    c = rows(100, 100, None)
    assert r.choose(c, rows(96, 97, None)) == 'C-ens'  # -3.5 %, equal passes
    assert r.choose(c, rows(98, 99, None)) == 'C'      # -1.5 %
    assert r.choose(rows(100, 100, 100), rows(90, 90, None)) == 'C'  # fewer passes


@pytest.mark.parametrize('val,test,label', [
    ((-.06, 50, 50), (-.06, 14, 14), 'Better'),
    ((-.06, 50, 50), (-.01, 14, 14), 'Worse'),  # a split between the two sets
    ((-.01, 48, 50), (.04, 13, 14), 'Par'),
    ((-.01, 47, 50), (.04, 14, 14), 'Worse'),
    ((-.01, 50, 50), (.06, 14, 14), 'Worse'),
    ((-.06, 48, 50), (-.06, 14, 14), 'Worse'),  # passes too low for Better, Δ too large for Par
])
def test_d158_label(val, test, label):
    assert r.outcome_label(val, test) == label


def test_reuse_only_when_the_16_sample_run_settles_the_first_8():
    c16 = {'a': dict(input='a', outcome=r.OWN, chosen_sample=3, deadline=False, timeouts=0),
           'b': dict(input='b', outcome=r.OWN, chosen_sample=12, deadline=False, timeouts=0),
           'c': dict(input='c', outcome='dead end', chosen_sample=0, deadline=False, timeouts=0, n_complete=0, n_dead_end=16),
           'd': dict(input='d', outcome='error', chosen_sample=0, deadline=False, timeouts=2)}
    assert r.not_derivable(c16, ['a', 'b', 'c', 'd', 'e']) == ['b', 'd', 'e']


def test_polish_keeps_the_incumbent_unless_better():
    assert polish_c.admitted(30, None) == 30
    assert polish_c.admitted(30, 30) == 30
    assert polish_c.admitted(30, 31) == 30
    assert polish_c.admitted(30, 28) == 28


def test_polished_refuses_a_record_for_another_incumbent(tmp_path):
    row = dict(outcome=r.OWN, moves=30, cpu_s=1, pipeline_s=1)
    (tmp_path/'m.json').write_text(json.dumps(dict(before=31, after=30, polish_s=2)))
    with pytest.raises(ValueError):
        r.polished(dict(m=row), tmp_path)
    (tmp_path/'m.json').write_text(json.dumps(dict(before=30, after=28, polish_s=2)))
    (tmp_path/'m.result.json').write_text('{}')
    assert r.polished(dict(m=row), tmp_path)['m']['moves'] == 28


FULL = dict(input='x', outcome='checker fail', chosen_sample=0, deadline=False, timeouts=0, n_complete=16, n_dead_end=0, n_max_tokens=0,
            n_checked=16, candidate_failures=dict(a='full checker rejected candidate or report binding'))


def test_no_pass_settles_the_subset_only_with_evidence():
    assert r.not_derivable(dict(a=FULL), ['x']) == []
    assert r.not_derivable(dict(a=dict(FULL, n_complete=None, n_checked=None)), ['x']) == ['x']  # outer error, no metrics
    assert r.not_derivable(dict(a=dict(FULL, n_complete=8, n_checked=8)), ['x']) == ['x']        # 8 of 16 decoded
    assert r.not_derivable(dict(a=dict(FULL, n_checked=9)), ['x']) == ['x']                      # some never judged
    assert r.not_derivable(dict(a=dict(FULL, candidate_failures=dict(a='ValueError: boom'))), ['x']) == ['x']
    assert r.not_derivable(dict(a=dict(FULL, n_complete=0, n_dead_end=16, n_checked=None)), ['x']) == []  # all dead ends


def test_zero_passes_or_mutual_maps_still_report():
    arms = dict(a=rows(None, None), b=rows(3, None))
    arms['a']['m0'].update(cpu_s=1, decode_s=1)
    assert any('no mutual maps' in line for line in r.pair_table(arms, (('a', 'b'),)))
    assert '—' in r.table(arms)[2]


def test_improved_row_points_to_the_polished_plan(tmp_path):
    row = dict(outcome=r.OWN, moves=30, cpu_s=1, pipeline_s=1, result='old')
    (tmp_path/'m.json').write_text(json.dumps(dict(before=30, after=28, polish_s=2)))
    with pytest.raises(ValueError):
        r.polished(dict(m=row), tmp_path)
    (tmp_path/'m.result.json').write_text('{}')
    got = r.polished(dict(m=row), tmp_path)['m']
    assert got['result'] == str(tmp_path/'m.result.json') and got['before_result'] == 'old'


def test_stale_two_step_cache_is_refused(tmp_path):
    import hashlib
    source = hashlib.sha256((r.ROOT/'src/stowage/solver/twostep.py').read_bytes()).hexdigest()
    row = dict(instance_id='m', input='a.json', finalize_s_budget=600., twostep_py_sha256=source, passed=False)
    (tmp_path/'m.json').write_text(json.dumps(row))
    r.check_twostep_cache(tmp_path, {'a.json': 'h'}, 600.)
    with pytest.raises(SystemExit):
        r.check_twostep_cache(tmp_path, {'a.json': 'h'}, 120.)
    with pytest.raises(SystemExit):
        r.check_twostep_cache(tmp_path, {'b.json': 'h'}, 600.)


def test_ensemble_charges_both_arms(tmp_path):
    def rec(folder, moves, pipe):
        (folder/'s0/instances/m').mkdir(parents=True)
        (folder/'s0/instances/m.json').write_text(json.dumps(dict(instance_id='m', rung='small', input='i', pipeline_s=pipe, status='ok', metrics=dict(gen_wall_s=pipe))))
        (folder/'s0/instances/m/outcome.json').write_text(json.dumps(dict(outcome=r.OWN)))
        (folder/'s0/instances/m/report.json').write_text(json.dumps(dict(count=dict(weighted_nonproductive=moves))))
    rec(tmp_path/'ens-82', 10, 11)
    rec(tmp_path/'ens-83', 9, 23)
    got = r.ensemble(tmp_path, derive=False)['m']
    assert (got['source_adapter'], got['pipeline_s'], got['moves']) == ('8.3', 34, 9)
