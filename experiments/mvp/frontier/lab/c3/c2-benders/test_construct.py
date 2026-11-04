"""Small exact-geometry descent regressions; no checker/LP/CP-SAT required."""
import importlib.util
from pathlib import Path
import sys
from time import monotonic
from types import SimpleNamespace as NS


spec = importlib.util.spec_from_file_location('_c3_construct_test_module', Path(__file__).with_name('construct.py'))
construct = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = construct
spec.loader.exec_module(construct)


class Cells:
    def __getitem__(self, key):
        row, columns = key
        return [[0], [0], [1], [1]][row][columns]


class FakeModel:
    def __init__(self, *, protect=False, separated_x=False):
        self.boxes = [0, 1, 2, 3]
        self.pages = [NS(stacks=(0,), slots=(0, 1), kind='20', deck=False),
                      NS(stacks=(1,), slots=(2, 3), kind='20', deck=True)]
        self.T = NS(slot_cells=Cells(), slot_n=[1]*4, cell_ids=['hold', 'deck'],
                    stack_base_z=[0., 10.], box_ids=['a', 'b', 'c', 'd'],
                    is_dg=[protect]*4, heat=[False]*4, fallback=[False]*4,
                    closes_stack=[False]*4)
        self.p = NS(cells={'hold': NS(lcg_m=0., tcg_m=0.),
                           'deck': NS(lcg_m=100. if separated_x else 0., tcg_m=0.)},
                    boxes={name: NS(gross_mass_kg=mass, height_m=2., oog=None)
                           for name, mass in zip(self.T.box_ids, [10000., 30000., 5000., 20000.])})
        self.pool, self.columns, self.banned = {}, [], set()
        self.add(0, (0, 1)); self.add(1, (2, 3))

    def legal(self, page, boxes):
        return bool(boxes) and len(boxes) <= len(page.slots)

    def lash_ok(self, page, boxes):
        return True

    def add(self, page, boxes):
        boxes = tuple(boxes)
        if (page, boxes) in self.pool:
            return False
        assert self.legal(self.pages[page], boxes)
        self.pool[page, boxes] = len(self.columns)
        self.columns.append(NS(page=page, boxes=boxes))
        return True


def test_descent_lowers_exact_moment_and_preserves_full_cover():
    model = FakeModel()
    changed, row = construct.descend(model, [0, 1], deadline=monotonic()+5.)
    assert construct._selection_valid(model, changed)
    assert row['status'] == 'improved'
    assert row['cargo_moment_change_kg_m'] < -50000.
    assert row['cargo_kg_change_m'] < 0.
    assert set(b for k in changed for b in model.columns[k].boxes) == set(model.boxes)
    assert max(construct._column_geometry(model, model.columns[k].page,
                                         model.columns[k].boxes)[3] for k in changed) <= 14.


def test_descent_keeps_protected_boxes_in_place():
    model = FakeModel(protect=True)
    changed, row = construct.descend(model, [0, 1], deadline=monotonic()+5.)
    assert changed == [0, 1]
    assert row['status'] == 'local_stop'
    assert row['moves'] == []


def test_lcg_guard_blocks_apparently_better_but_unreachable_moment_move():
    model = FakeModel(separated_x=True)
    changed, row = construct.descend(model, [0, 1], deadline=monotonic()+5., lcg_tolerance_m=0.)
    assert changed == [0, 1]
    assert row['cargo_moment_change_kg_m'] == 0.


def test_invalid_coverage_seed_is_rejected_without_a_move():
    changed, row = construct.descend(FakeModel(), [0], deadline=monotonic()+5.)
    assert changed == [0]
    assert row['status'] == 'invalid_seed'
