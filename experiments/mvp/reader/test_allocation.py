"""Allocation correctness; synthetic packing screens plus one train-only smoke."""
from types import SimpleNamespace as NS

import pytest
from ortools.sat.python import cp_model

from experiments.mvp.reader import allocation
from experiments.mvp.reader.constraints import InfeasibleInstruction


class Obj(NS):
    def model_copy(self, *, update):
        return Obj(**(vars(self) | update))


def tiny(*, long=False, retained=False):
    stacks = [NS(id=f's{bay}', on_deck=False, hatch_cover_id=cover)
              for bay, cover in [(1, 'A'), (3, 'B')]]
    cells = [NS(id=f'{bay:03d}0102', stack_id=f's{bay}') for bay in (1, 3)]
    boxes = [NS(id='x', pod='P1', gross_mass_kg=1000),
             NS(id='y', pod='P2', gross_mass_kg=1000)]
    return Obj(id='synthetic', vessel=NS(cells=cells, stacks=stacks,
                hatch_covers=[NS(id=c, hold_stack_ids=[s.id for s in stacks if s.hatch_cover_id == c],
                                 deck_stack_ids=[]) for c in ['A', 'B']]),
               containers=boxes, rotation=NS(calls=[NS(locode='P1', index=1), NS(locode='P2', index=2)]),
               onboard=Obj(placements=[NS(container_id='x', slot='0010102')] if retained else []),
               discharge_list=[], load_list=['y'] if retained else ['x', 'y'], long=long)


class FakeMaster:
    def __init__(self, problem, *, boxes, **kwargs):
        self.instance = problem.instance
        self.groups = [[bid] for bid in boxes]
        self.T = NS(slot_ids=['0020102'] if self.instance.long else ['0010102', '0030102'])
    def _build(self):
        m = cp_model.CpModel()
        x = {(g, s): m.NewBoolVar(f'x{g}_{s}') for g in range(len(self.groups)) for s in range(len(self.T.slot_ids))}
        for g in range(len(self.groups)):
            m.AddExactlyOne(x[g, s] for s in range(len(self.T.slot_ids)))
        for s in range(len(self.T.slot_ids)):
            m.AddAtMostOne(x[g, s] for g in range(len(self.groups)))
        return m, x, 1


@pytest.fixture
def fake_master(monkeypatch):
    monkeypatch.setattr(allocation, 'Problem', lambda instance: NS(instance=instance))
    monkeypatch.setattr(allocation, 'MasterModel', FakeMaster)


def test_two_ports_receive_distinct_physical_covers(fake_master):
    compiled, meta = allocation.derive_hatch_allocation(tiny())
    assert set(meta['cover_ports'].values()) == {'P1', 'P2'}
    assert not compiled.audit_positions(meta['packing_witness'])
    assert meta['checker_certificate'] is False
    assert meta['shared_covers'] is False


def test_retained_slot_and_cover_remain_fixed(fake_master):
    compiled, meta = allocation.derive_hatch_allocation(tiny(retained=True))
    assert meta['cover_ports']['A'] == 'P1'
    assert meta['packing_witness']['x'] == '0010102'
    assert compiled.forbids('y', '0010102')


def test_long_box_links_both_covers_and_cannot_share_ports(fake_master):
    with pytest.raises(InfeasibleInstruction):
        allocation.derive_hatch_allocation(tiny(long=True))


def test_lone_long_box_assigns_every_touched_cover(fake_master):
    instance = tiny(long=True)
    instance.load_list = ['x']
    compiled, meta = allocation.derive_hatch_allocation(instance)
    assert meta['cover_ports'] == {'A': 'P1', 'B': 'P1'}
    assert compiled.forbids('x', '0020102') is None


def test_conflicting_retained_pods_fail_before_packing(fake_master):
    instance = tiny(retained=True)
    instance.onboard.placements.append(NS(container_id='y', slot='0010102'))
    with pytest.raises(InfeasibleInstruction, match='mixes PODs'):
        allocation.derive_hatch_allocation(instance)


def test_worker_cap():
    with pytest.raises(ValueError, match='two CPU workers'):
        allocation.derive_hatch_allocation(tiny(), workers=3)
