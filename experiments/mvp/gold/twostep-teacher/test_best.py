"""best.py's winner rule and summary on synthetic rows; no solver, no corpus."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('teacher_best', Path(__file__).resolve().parent/'best.py')
best = importlib.util.module_from_spec(spec)
spec.loader.exec_module(best)


def row(gold, two):
    return dict(gold=dict(passed=gold is not None, moves=gold), two_step=dict(passed=two is not None, moves=two))


def test_fewer_moves_wins_and_a_tie_keeps_gold():
    assert best.winner(row(15., 7.))[0] == 'two_step'
    assert best.winner(row(5., 7.))[0] == 'gold'
    assert best.winner(row(8., 8.))[0] == 'gold'


def test_only_a_verified_plan_can_win():
    assert best.winner(row(None, 7.)) == ('two_step', dict(gold=None, two_step=7.))
    assert best.winner(row(7., None))[0] == 'gold'
    assert best.winner(row(None, None))[0] is None
    assert best.winner(dict(gold=dict(passed=False, moves=1.), two_step=dict(passed=True, moves=9.)))[0] == 'two_step'


def test_summary_gap_counts_only_maps_both_pass():
    rows = [dict(gold=15., two_step=7., source='two_step'), dict(gold=5., two_step=7., source='gold'),
            dict(gold=None, two_step=3., source='two_step'), dict(gold=None, two_step=None, source=None)]
    table = best.table(rows)
    assert (table['both_passed'], table['two_step_better'], table['gold_better'], table['two_step_only'], table['no_teacher']) == (2, 1, 1, 1, 1)
    assert table['mean_gap'] == 3.
