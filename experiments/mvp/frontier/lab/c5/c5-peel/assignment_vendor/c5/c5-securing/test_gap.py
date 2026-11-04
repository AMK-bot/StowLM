"""Small real-map checks of the assignment LP and of the cold boundary."""
import ast
from pathlib import Path

import numpy as np
import pytest

from gap import Assignment, Settings
from stow import reference


HERE = Path(__file__).parent


class Page:
    deck, kind, slots, stacks = True, 'long', (0, 1, 2), (0,)
    cover, limit_kg, hmax = 'C', 90000., 10.


class Table:
    stack_base_z = np.array([12.5])


class Container:
    def __init__(self, mass, height=2.591):
        self.gross_mass_kg, self.height_m, self.length_ft, self.oog = mass, height, 40, None


class Hull:
    lpp_m, beam_m, depth_m, scantling_draught_m, bilge_keel = 124.41, 22.5, 11.3, 8.7, False


def solver():
    a = Assignment.__new__(Assignment)
    a.T = Table()
    a.T.box_ids = {0: 'a', 1: 'b', 2: 'c'}
    a.row_cache = {}
    a.motions = reference(Hull, 1.9)

    class Model:
        def _slot_xy(self, page, k):
            return 60., 5.
    a.m = Model()
    a.m.p = type('P', (), dict(boxes=dict(a=Container(20000.), b=Container(15000.), c=Container(8000.))))
    return a


def test_heavy_low_beats_heavy_high():
    a = solver()
    assert a.stack_ratio(Page, [0, 1, 2]) < a.stack_ratio(Page, [2, 1, 0])


def test_cached_rows_are_the_same_rows():
    a = solver()
    assert a.stack_ratio(Page, [0, 1, 2]) == a.stack_ratio(Page, [0, 1, 2])


def test_settings_default_keeps_every_pair():
    assert Settings().pairs_per_box == 0


def test_method_reads_no_stored_data():
    """Cold boundary: nothing in the runtime sources touches stored plans, results or the context's stored fields."""
    for name in ('method.py', 'gap.py', 'stow.py', 'rows.py', 'chainlab.py'):
        text = (HERE/name).read_text()
        ast.parse(text)
        for word in ("'stored", 'stored_plan', 'stored_lifts', "'baseline'", 'results/', 'json.loads'):
            assert word not in text, (name, word)
