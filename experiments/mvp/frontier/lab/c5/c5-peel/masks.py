"""Scoped search-mask peeling; never changes checker functions or input objects.

Construct and consume feasibility engines inside ``peeled_masks(level)``. Engines
created there must not escape: their cached state/table reflects that level.
Only the legacy (non rule-freeze-v1) feasibility domain is supported.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import tomllib
from pathlib import Path

import numpy as np

import stowage.feasibility._engine as native
from stowage.checker import rules, rules_special
from stowage.model import Placement
from stowage.rules.catalogue import rule_freeze_v1_enabled

TABLE = Path(__file__).resolve().parent.parent / 'leader' / 'tiers.toml'
E6 = 'expert.e6.heavy_under_deck'
E1 = 'expert.e1.heavy_bottom'
E2 = 'expert.e2.weight_order'
E8 = frozenset({'expert.e8.under_deck', 'expert.e8.on_deck',
                'expert.e8.away_from_heat', 'expert.e8.top_stowage'})
REEFER = 'expert.physical.reefer_plug'


def peeled_rule_ids(level):
    """The immutable leader table decides which requested mask rules can peel."""
    level = int(str(level).removeprefix('L'))
    if not 0 <= level <= 5:
        raise ValueError('level must be L0..L5')
    table = tomllib.loads(TABLE.read_text())
    tiers = table['levels'][f'L{level}']
    return frozenset(rule for tier in tiers for rule in table['tiers'][tier]['rules'])


@contextmanager
def peeled_masks(level):
    """Temporarily drop only E6/E1/E2/E8/plug constraints in search masks.

    Existing higher-tier geometry, VGM, IMDG, access, capacity, hull and stability
    screens stay in place. L4/L5 use the same placement-mask peeling as L3;
    soft physics is the page oracle's separate responsibility. Unknown IDs fail
    closed because the table supplies all permission decisions.
    """
    peeled = peeled_rule_ids(level)
    if not peeled:
        yield
        return
    if rule_freeze_v1_enabled():
        raise NotImplementedError('peeled masks support legacy feasibility only; rule-freeze-v1 fails closed')
    removed = peeled & (E8 | {E1, E2, E6, REEFER})
    owner = native.IncrementalEngine
    original_build = native.build_tables
    original_init = owner.__init__
    original_heat_many = owner._heat_many
    changed = {}

    def patch(name, value):
        changed[name] = getattr(owner, name)
        setattr(owner, name, value)

    def build(instance):
        # Read valid data first: peeling a rule must never manufacture missing
        # vessel evidence (a T0 failure). Preserve missing-parameter rejections.
        original = original_build(instance)
        overrides = copy.deepcopy(instance.rule_overrides)
        if E6 in peeled and original.e6_threshold is not None and original.e6_fallback is not None:
            overrides.setdefault(E6, {}).update(heavy_threshold=float(np.max(original.mass, initial=0)) + 1,
                allow_on_deck_if_no_compatible_hold_cell=True)
        if E1 in peeled and original.e1_threshold is not None:
            overrides.setdefault(E1, {})['heavy_threshold'] = 0.0
        if E2 in peeled and original.e2_valid:
            overrides.setdefault(E2, {})['tolerance'] = 1 << 20
        boxes = [b.model_copy(update={
            'special_stowage': [flag for flag in b.special_stowage
                if 'expert.e8.' + str(flag) not in peeled],
            'reefer': False if REEFER in peeled else b.reefer,
        }) for b in instance.containers]
        projected = instance.model_copy(update={'containers': boxes, 'rule_overrides': overrides})
        table = original_build(projected)
        table.instance = instance
        # Keep the heat validation path: missing clearance/source geometry is
        # T0 even when the heat booking itself may be broken.
        table.heat = original.heat
        return table

    def init(self, instance, space, config):
        if rule_freeze_v1_enabled():
            raise NotImplementedError('rule-freeze-v1 fails closed')
        original_init(self, instance, space, config)
        if 'expert.e8.top_stowage' in peeled:
            self._attr = {cid: (*facts[:4], bool(self._boxes[cid].oog and
                self._boxes[cid].oog.over_height_m), *facts[5:]) for cid, facts in self._attr.items()}
            # Root may contain inherited cargo; close flags must reflect the
            # peeled booking while OOG physical closure remains enforced.
            self._restack(self._cur, range(len(self.T.stack_ids)))

    def acceptable(violations, cid=None):
        return not any(v.severity == 'hard' and v.rule_id not in removed and
                       (cid is None or cid in v.containers or not v.containers) for v in violations)

    def exact(self, st, ship, b, s):
        cid = self.T.box_ids[b]
        return acceptable(rules.placement_violations(self.instance, ship,
            self._boxes[cid], self.T.slot_ids[s]))

    def special(self, st, b, s, geo):
        cid = self.T.box_ids[b]
        ship = self._ship(st)
        ship = ship.model_copy(update={'placements': [*ship.placements,
            Placement.model_construct(slot=self.T.slot_ids[s], container_id=cid)]})
        return acceptable(rules_special.special_violations(self.instance, ship,
            call_index=self.instance.call_index, focus_container_id=cid), cid)

    def heat_many(self, st, boxes, slots):
        # Populate the native geometry validation cache without changing it.
        original_heat_many(self, st, boxes, slots)
        clearance, missing, cell_ok, _ = self._heat_data
        if clearance is None or missing:
            return np.zeros(len(boxes), dtype=bool)
        cells = self.T.slot_cells[slots]
        return np.all((cells < 0) | cell_ok[np.maximum(cells, 0)], axis=1)

    def inherited(self, ex, j):
        placements = []
        for ci in self.T.stack_cells[j]:
            cid = ex.occ.get(self.T.cell_ids[ci])
            if cid is not None:
                p = Placement.model_construct(slot=ex.where[cid][1], container_id=cid)
                if p not in placements:
                    placements.append(p)
        ship = self.instance.onboard.model_copy(update={'placements': placements})
        violations, _ = rules._core(self.instance, ship, self.instance.call_index)
        return not acceptable(violations)

    try:
        native.build_tables = build
        patch('__init__', init)
        patch('_exact_ok', exact)
        patch('_special_ok', special)
        patch('_inherited_hard', inherited)
        if E6 in peeled:
            # These methods evaluate E6 alone. No other completion screen is
            # disabled; all C1 capacity and IMDG screens still run normally.
            patch('_e6_last', lambda self, st, cands: np.ones(len(cands), dtype=bool))
            patch('_e6_departure_ok', lambda self, st, extra: True)
        if 'expert.e8.away_from_heat' in peeled:
            patch('_heat_many', heat_many)
            patch('_heat_ok', lambda self, st, b, s: special(self, st, b, s, None))
        yield
    finally:
        native.build_tables = original_build
        for name, original in reversed(list(changed.items())):
            setattr(owner, name, original)
