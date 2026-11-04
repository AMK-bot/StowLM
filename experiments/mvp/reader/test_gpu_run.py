"""D203 stop rules and sample on synthetic rows; no model."""
from datetime import datetime

from experiments.mvp.reader import gpu_run as g


def test_plateau_needs_the_target_and_two_flat_checks():
    assert not g.plateaued([.9, .9, .9])                      # never passed 95 %
    assert not g.plateaued([.96, .97])                        # too few checks
    assert not g.plateaued([.96, .97, .98])                   # still improving
    assert g.plateaued([.96, .96, .96])                       # flat
    assert g.plateaued([.8, .97, .96, .97])                   # the last two don't beat the best before them
    assert not g.plateaued([.8, .94, .97, .96])               # 95 % passed only inside the last two


def test_hard_stop_is_20_15_edt():
    assert not g.past_hard_stop(datetime.fromisoformat('2026-10-31T20:14:59-04:00'))
    assert g.past_hard_stop(datetime.fromisoformat('2026-10-31T20:15:00-04:00'))


def test_sample_is_fixed_and_spreads_across_families():
    rows = [dict(id=f'{f}-{i}', family=f) for f in 'abc' for i in range(40)]
    first, second = g.fixed_sample(rows, 30), g.fixed_sample(list(reversed(rows)), 30)
    assert first == second and len(first) == 30
    assert {f: sum(r['family'] == f for r in first) for f in 'abc'} == dict(a=10, b=10, c=10)
