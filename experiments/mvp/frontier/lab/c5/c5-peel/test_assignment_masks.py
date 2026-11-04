"""Matrix-preservation and tier-mask checks, with no optimization calls."""
from contextlib import contextmanager
import ast
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import assignment_masks as masks
from tiers import tier_for


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gaps():
    old_rows = sys.modules.get('rows')
    rows = load_file('assignment_test_rows', masks.VENDOR/'c5/c5-securing/rows.py')
    sys.modules['rows'] = rows
    try:
        tagged = load_file('assignment_test_tagged_gap', masks.VENDOR/'c5/c5-securing/gap.py')
        ordinary = load_file('assignment_test_original_gap', HERE.parent/'c5-securing/gap.py')
        yield tagged, ordinary
    finally:
        if old_rows is None:
            sys.modules.pop('rows', None)
        else:
            sys.modules['rows'] = old_rows


def synthetic_assignment(gap):
    a = gap.Assignment.__new__(gap.Assignment)
    a.T = SimpleNamespace(slot_cells=np.array([[0, 1], [2, 3], [4, 5]]),
        rank=np.array([2, 1, 0]), e2_tol=0, e1_threshold=15000.,
        closes_stack=np.array([False, False, True]), share=np.array([15000., 10000., 5000.]),
        mass=np.array([30000., 20000., 10000.]), height=np.array([2.6, 2.6, 2.6]),
        is_dg=np.array([True, False, False]), stack_base_z=np.array([10.]),
        units=np.array([2, 2, 2]))
    page = SimpleNamespace(kind='long', slots=(0, 1, 2), stacks=(0,), limit_kg=60000.,
        hmax=12., deck=True, cover='C')
    a.m = SimpleNamespace(T=a.T, boxes=[0, 1, 2], pages=[page], ports=[1, 2],
        heavy={0: True, 1: True, 2: False}, pod={0: 1, 1: 2, 2: 2},
        sight_frames=[{}], _slot_xy=lambda page, k: (50., 2.),
        sight_limit=lambda page, k, b: 20.)
    a.s = gap.Settings(lcg=(45., 55.), tcg=.1, vertical_max_tm=1200.)
    a.pair = [(b, q) for b in a.m.boxes for q in page.slots]
    a.n = len(a.pair)
    a.box_pos = {b: b for b in a.m.boxes}
    a.slot_page = {q: [(0, q)] for q in page.slots}
    a.high = {b: False for b in a.m.boxes}
    a.cost = lambda: np.arange(a.n, dtype=float)*.01
    a.oog_conflicts = lambda: [(0, [4])]
    a.page_rows = lambda page, height=None: (np.array([[.01, .02, .03]]),
        np.array([[.001, .002, .003]]), np.array([[.001, .002, .003]]), [('tension', 'stiff', 0)])
    a.box_force = lambda b: (float(a.T.mass[b]), 100., 100.)
    a._built = None
    return a


def test_row_tagging_preserves_every_l0_number_and_records_slack_offsets(gaps):
    tagged, ordinary = gaps
    new, old = synthetic_assignment(tagged), synthetic_assignment(ordinary)
    actual, expected = new.build(), old.build()
    for index in (0, 2, 4):
        np.testing.assert_array_equal(actual[index], expected[index])
    for index in (1, 3):
        assert actual[index].shape == expected[index].shape
        assert (actual[index] != expected[index]).nnz == 0
    assert len(new.row_rules) == len(new.row_info) == actual[1].shape[0]
    assert all(info['rule_id'] == rule for rule, info in zip(new.row_rules, new.row_info))
    groups = {info['group'] for info in new.row_info}
    assert {'cell', 'support', 'closer', 'oog', 'weight_order', 'heavy_bottom',
            'restow_objective', 'stack_weight', 'height', 'dg_surrogate',
            'visibility', 'securing', 'moment_proxy'} <= groups
    assert new.variable_offsets == {'assignment': (0, 9), 'uncovered': (9, 12),
                                    'extra': (12, len(actual[0]))}
    assert set(new.extra_variables) == set(range(12, len(actual[0])))
    for column, info in new.extra_variables.items():
        assert info['rows']
        assert all(actual[1][row, column] == -1. for row in info['rows'])
        assert all(new.row_rules[row] == info['rule_id'] for row in info['rows'])
    for group in ('heavy_bottom', 'weight_order'):
        indices = [i for i, info in enumerate(new.row_info) if info['group'] == group]
        assert any(np.any(actual[1].getrow(i).data > 0) and
                   np.any(actual[1].getrow(i).data < 0) for i in indices)
    for info in new.row_info:
        if info['group'] in {'cell', 'support', 'closer', 'height', 'oog'}:
            assert tier_for(info['rule_id']) == 'T0'
        if info['group'] in {'moment_proxy', 'visibility'}:
            assert tier_for(info['rule_id']) == 'T2' and info['proxy']
        if info['group'] == 'dg_surrogate':
            assert tier_for(info['rule_id']) == 'T1' and info['proxy']


def test_only_vendor_build_changed(gaps):
    tagged, ordinary = gaps
    def without_build(path):
        tree = ast.parse(Path(path).read_text())
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == 'Assignment':
                node.body = [method for method in node.body
                             if not isinstance(method, ast.FunctionDef) or method.name != 'build']
        return ast.dump(tree, include_attributes=False)
    assert without_build(tagged.__file__) == without_build(ordinary.__file__)


@pytest.fixture
def model_class(monkeypatch, gaps):
    tagged, _ = gaps
    # Fake only construction, preserving the same pure-column checks as the
    # copied vendor base. __module__ identifies the private vendor test module.
    class Model:
        def __init__(self):
            self.T = SimpleNamespace(is_dg=np.array([False, False]),
                closes_stack=np.array([False, False]), height=np.array([2., 2.]),
                share=np.array([1., 1.]), rank=np.array([0, 2]), e2_tol=0,
                stack_base_z=np.array([0.]))
            self.boxes, self.pod, self.heavy = [0, 1], {0: 1, 1: 1}, {0: False, 1: True}
            self.sight_frames = [{}]
            self.pages = [SimpleNamespace(kind='20', slots=(0, 1), stacks=(0,),
                limit_kg=10., hmax=10., deck=True)]
            self.static = self._static()
            self.static = self.static.copy()
            self.static[:, 1] = False  # native sight constructor's hard pruning
            self.types, self.pricing_cache = {('ordinary',): [0]}, {}
        def _static(self):
            return np.ones((2, 2), dtype=bool)
        def sight_limit(self, page, tier, b):
            return 1.
        def port_of(self, page):
            return None
        def legal(self, page, boxes, *, any_port=False):
            if sum(self.T.height[b] for b in boxes) > page.hmax:
                return False
            if sum(self.T.share[b] for b in boxes) > page.limit_kg:
                return False
            if any(self.T.closes_stack[b] for b in boxes[:-1]):
                return False
            for i in range(1, len(boxes)):
                if self.T.rank[boxes[i]] > min(self.T.rank[b] for b in boxes[:i])+self.T.e2_tol:
                    return False
                if self.heavy[boxes[i]] and not all(self.heavy[b] for b in boxes[:i]):
                    return False
            if not all(self.static[b, page.slots[k]] for k, b in enumerate(boxes)):
                return False
            return sum(self.T.height[b] for b in boxes) <= self.sight_limit(page, 0, boxes[0])
    Model.__module__ = tagged.__name__
    @contextmanager
    def no_core_mask(level):
        yield
    monkeypatch.setattr(masks, 'peeled_masks', no_core_mask)
    monkeypatch.setattr(masks, '_static_with_peeled_heat', lambda self, peeled: self.T.static.copy()
        if hasattr(self.T, 'static') else np.ones((2, 2), dtype=bool))
    return Model


def test_l0_class_and_numbers_are_exactly_unchanged(model_class):
    Model = model_class
    original = tuple(getattr(Model, name) for name in ('__init__', '_static', 'legal'))
    with masks.assignment_masks(0, model_cls=Model):
        assert original == tuple(getattr(Model, name) for name in ('__init__', '_static', 'legal'))
        np.testing.assert_array_equal(Model().static, [[True, False], [True, False]])


def test_visibility_is_only_softened_in_lp_and_class_restores(model_class):
    Model = model_class
    original = tuple(getattr(Model, name) for name in ('__init__', '_static', 'legal'))
    for level in (1, 2, 3):
        with masks.assignment_masks(level, model_cls=Model):
            m = Model()
            assert not m.legal(m.pages[0], (0,))
            assert not m.static[0, 1]
    with pytest.raises(RuntimeError, match='unwind'):
        with masks.assignment_masks(4, model_cls=Model):
            m = Model()
            assert m.sight_frames and m.sight_limit(m.pages[0], 0, 0) == 1.
            assert m.static.all()
            assert m.legal(m.pages[0], (0, 1))
            # Height and physical closure stay T0 even after every tier peels.
            m.T.closes_stack[0] = True
            assert not m.legal(m.pages[0], (0, 1))
            m.T.closes_stack[0] = False
            m.T.height[0] = 20.
            assert not m.legal(m.pages[0], (0, 1))
            raise RuntimeError('unwind')
    assert original == tuple(getattr(Model, name) for name in ('__init__', '_static', 'legal'))


def test_stack_weight_remains_hard_until_t1_peels(model_class):
    Model = model_class
    for level in (4, 5):
        with masks.assignment_masks(level, model_cls=Model):
            m = Model()
            m.T.share[:] = 10.
            assert m.legal(m.pages[0], (0, 1)) is (level == 5)


def test_assignment_keeps_original_rule_facts_while_engine_mask_is_projected(monkeypatch, model_class):
    from stowage.io import load_instance
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    import stowage.feasibility._engine as native
    from masks import peeled_masks
    instance = load_instance(HERE.parents[5]/'experiments/mvp/corpus/val/tiny/feeder_500/random_yard/seed-020000006.json')
    original = native.build_tables(instance)
    Model = model_class
    def construct(self, problem):
        self.T, self.p = problem.engine.T, problem
        self.static = self.T.static.copy()
        self.heavy = {b: self.T.mass[b] >= self.T.e1_threshold for b in range(len(self.T.mass))}
    monkeypatch.setattr(Model, '__init__', construct)
    monkeypatch.setattr(masks, 'peeled_masks', peeled_masks)
    with masks.assignment_masks(2, model_cls=Model):
        engine = make_feasibility_engine(instance, get_action_space('boxslot'),
            config=FeasibilityConfig(extra={'global_screen': 0}))
        projected = engine.T
        problem = SimpleNamespace(instance=instance, engine=engine,
                                  boxes={box.id: box for box in instance.containers})
        model = Model(problem)
        assert engine.T is projected
        assert model.T.e1_threshold == original.e1_threshold > 0
        assert model.T.e2_tol == original.e2_tol < projected.e2_tol
        assert model.T.e6_threshold == original.e6_threshold < projected.e6_threshold
        assert projected.e1_threshold == 0
        np.testing.assert_array_equal(model.T.static, projected.static)
        assert model.assignment_original_boxes is problem.boxes
        assert model.assignment_original_tables.e1_threshold == original.e1_threshold


@pytest.mark.parametrize('aboard', [('OOG',), ('partner',), ()])
def test_partial_oog_repair_filters_omitted_oog_or_swap_partner_and_restores(model_class, aboard):
    seen = []
    class Base:
        def clear_oog(self, assignment):
            # Mirrors both dangerous accesses in the copied implementation:
            # omitted OOG table[box_id], omitted partner out[other].
            seen.append((tuple(self.boxes), self.boxes))
            return {self.T.box_ids[b]: assignment[self.T.box_ids[b]] for b in self.boxes}
    class VendorModel(model_class, Base):
        pass
    VendorModel.__module__ = model_class.__module__
    assert 'clear_oog' not in VendorModel.__dict__
    assignment = {name: '0010002' for name in aboard}
    with masks.assignment_masks(3, model_cls=VendorModel):
        model = VendorModel()
        model.T.box_ids = ['OOG', 'partner']
        original_boxes = model.boxes
        assert model.clear_oog(assignment) == assignment
        assert seen[-1][0] == tuple(i for i, name in enumerate(model.T.box_ids) if name in aboard)
        assert model.boxes is original_boxes
        assert seen[-1][1] is not original_boxes
        complete = {name: '0010002' for name in model.T.box_ids}
        assert model.clear_oog(complete) == complete
        assert seen[-1][1] is original_boxes  # complete path is unchanged
    assert 'clear_oog' not in VendorModel.__dict__
    assert VendorModel.clear_oog is Base.clear_oog


def test_partial_oog_repair_restores_box_identity_on_exception(model_class):
    class Base:
        def clear_oog(self, assignment):
            assert self.boxes == [0]
            raise RuntimeError('repair failed')
    class VendorModel(model_class, Base):
        pass
    VendorModel.__module__ = model_class.__module__
    with masks.assignment_masks(3, model_cls=VendorModel):
        model = VendorModel()
        model.T.box_ids = ['OOG', 'partner']
        original_boxes = model.boxes
        with pytest.raises(RuntimeError, match='repair failed'):
            model.clear_oog({'OOG': '0010002'})
        assert model.boxes is original_boxes
    assert 'clear_oog' not in VendorModel.__dict__
