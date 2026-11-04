"""A real tiny integer master verifies coverage, footprints and moment rows."""
from time import monotonic
from types import SimpleNamespace as NS

from cover_mip import recover


def model(moment=False):
    columns = [NS(page=0, boxes=('a', 'b'), moment=1.),
               NS(page=1, boxes=('a',), moment=-1.),
               NS(page=2, boxes=('b',), moment=-1.)]
    return NS(boxes=['a', 'b'], stacks=[0, 1], columns=columns, banned=set(),
        pages=[NS(stacks=(0, 1)), NS(stacks=(0,)), NS(stacks=(1,))],
        lcg_target=0. if moment else None, fixed_moment=0.,
        lcg_moment=lambda c: c.moment, legal=lambda p, b: True, lash_ok=lambda p, b: True)


def test_integer_recovery_obeys_cover_conflicts_and_longitudinal_row():
    m = model(moment=True)
    chosen, record = recover(m, {}, deadline=monotonic()+3., seconds=2.)
    assert set(chosen) == {1, 2}
    assert not record['physical_certificate']
    assert 'finite pattern pool' in record['scope']


def test_missing_box_is_restricted_pool_failure_without_physical_claim():
    m = model()
    m.banned = {0, 2}
    chosen, record = recover(m, {}, deadline=monotonic()+3., seconds=2.)
    assert chosen is None
    assert record['status'] == 'UNCOVERED_POOL'
    assert not record['physical_certificate']


def test_soft_longitudinal_target_preserves_integer_cover():
    m = model(moment=True)
    m.banned = {1, 2}
    chosen, record = recover(m, deadline=monotonic()+3., seconds=2.)
    assert chosen == [0]
    assert record['lcg_slack_tm'] == 1.
    assert record['lcg_moment_excess_tm'] == 1.
    chosen, record = recover(m, deadline=monotonic()+3., seconds=2., hard_lcg=True)
    assert chosen is None
    assert not record['physical_certificate']
