from pathlib import Path
import random
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pages_balanced import Model, Page, Pattern
from test_moments import example


def balanced_example():
    m = example()
    m.__class__ = Model
    # Geometry-aware pricing types remain distinct across these mirrors.
    m.T.stack_base_z[:] = 1.
    for k in ('c0', 'c1'):
        m.p.cells[k].lcg_m = 20.; m.p.cells[k].tcg_m = -2.
    for k in ('c2', 'c3'):
        m.p.cells[k].lcg_m = 20.; m.p.cells[k].tcg_m = 3.
    m.columns = [Pattern(0, (0,), 0.), Pattern(1, (1,), 0.)]
    m.pool = {(c.page, c.boxes): k for k,c in enumerate(m.columns)}
    m.legal = lambda page, boxes: True
    m.lash_ok = lambda page, boxes: True
    return m


def test_geometry_distinct_page_swap_improves_exact_moment_without_new_coverage():
    m = balanced_example()
    before = m.selection_moments([0, 1])
    out, heel = m.balance([0, 1], random.Random(0))
    after = m.selection_moments(out)
    assert before['transverse_tm'] == 40.
    assert heel == after['transverse_tm'] == -10.
    assert after['mass_t'] == before['mass_t']
    assert after['vertical_tm'] == pytest.approx(before['vertical_tm'])
    assert after['longitudinal_tm'] == before['longitudinal_tm']
    assert sorted(b for k in out for b in m.columns[k].boxes) == [0, 1]
    assert m.type_of[0] != m.type_of[1]
    assert m.balance_metadata['accepted'] == 1


def test_fixed_columns_and_their_stacks_remain_fixed():
    m = balanced_example()
    m.fixed_columns = [0]
    out, heel = m.balance([0, 1], random.Random(0))
    assert out == [0, 1]
    assert heel == 40.


def test_lashing_rejection_preserves_incumbent():
    m = balanced_example()
    m.lash_ok = lambda page, boxes: boxes == ((0,) if page is m.pages[0] else (1,))
    out, heel = m.balance([0, 1], random.Random(0))
    assert out == [0, 1] and heel == 40.


def test_fore_aft_guard_rejects_a_transverse_improvement():
    m = balanced_example()
    for k in ('c2','c3'):
        m.p.cells[k].lcg_m = 60.
    out, heel = m.balance([0, 1], random.Random(0), lcg_slack_m=.001)
    assert out == [0, 1] and heel == 40.


def test_empty_page_move_can_beat_the_best_occupied_swap():
    from types import SimpleNamespace as NS
    m = balanced_example()
    m.pages.append(Page('20', (2,), (4,), 60000., 20., True, 'B'))
    m.T.stack_base_z = np.array([1., 1., 1.])
    m.T.slot_cells = np.vstack([m.T.slot_cells, [4]])
    m.T.cell_ids.append('c4')
    m.p.cells['c4'] = NS(lcg_m=20., tcg_m=1.)
    m.type_of[2] = ('empty',)
    out, heel = m.balance([0, 1], random.Random(0))
    assert heel == 0.
    assert m.columns[out[1]].page == 2
    assert m.columns[out[1]].boxes == (1,)


def test_banned_destination_pattern_is_not_reintroduced():
    m = balanced_example()
    for page, boxes in [(0, (1,)), (1, (0,))]:
        k = len(m.columns)
        m.columns.append(Pattern(page, boxes, 0.))
        m.pool[page, boxes] = k
        m.banned.add(k)
    out, heel = m.balance([0, 1], random.Random(0))
    assert out == [0, 1] and heel == 40.


def test_zero_operation_limit_preserves_incumbent():
    m = balanced_example()
    out, heel = m.balance([0, 1], random.Random(0), rounds=0)
    assert out == [0, 1] and heel == 40.
    assert m.balance_metadata['examined'] == 0


def test_short_destination_is_rejected_before_geometry_indexing():
    m = balanced_example()
    m.pages.append(Page('20', (2,), (), 60000., 20., True, 'B'))
    m.T.stack_base_z = np.array([1., 1., 1.])
    out, heel = m.balance([0, 1], random.Random(0))
    assert heel == -10.
    assert all(m.columns[k].page != 2 for k in out)


def test_bad_coverage_is_rejected():
    m = balanced_example()
    with pytest.raises(ValueError, match='complete'):
        m.balance([0], random.Random(0))
