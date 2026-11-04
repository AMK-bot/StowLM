"""Small aggregate-allocation regressions; no engine or full checker search."""
from copy import copy
from types import SimpleNamespace as NS

import pytest

from experiments.mvp.pipeline.binding import allocation
from experiments.mvp.reader.constraints import InfeasibleInstruction
from llm.instructions.apply import ConstrainedInstance


class Record(NS):
    def model_copy(self, *, update):
        out = copy(self)
        vars(out).update(update)
        return out


def ship(*, lengths=(40,), pods=('SGSIN',), rows=(1,), retained=()):
    """Every long footprint crosses covers A and B; rows add capacity."""
    cells, stacks = [], []
    for bay, cover in ((1, 'A'), (3, 'B')):
        for row in rows:
            sid = f's-{bay}-{row}'
            cell = f'{bay:03d}{row:02d}02'
            cells.append(NS(id=cell, stack_id=sid, accepts_20=True,
                            accepts_40=True, accepts_45=True))
            stacks.append(NS(id=sid, hatch_cover_id=cover, on_deck=False))
    covers = [NS(id=cover, hold_stack_ids=[s.id for s in stacks if s.hatch_cover_id == cover],
                 deck_stack_ids=[]) for cover in ('A', 'B')]
    boxes = [NS(id=f'box-{i}', length_ft=length, pod=pod)
             for i, (length, pod) in enumerate(zip(lengths, pods))]
    return Record(id='aggregate-test', vessel=NS(cells=cells, stacks=stacks, hatch_covers=covers),
                  containers=boxes, load_list=[b.id for b in boxes if b.id not in dict(retained)],
                  discharge_list=[], onboard=Record(placements=[NS(container_id=bid, slot=slot)
                                                               for bid, slot in retained]),
                  rotation=NS(calls=[NS(locode=p, index=i) for i, p in enumerate(sorted(set(pods)))]))


@pytest.fixture(autouse=True)
def already_admitted(monkeypatch):
    monkeypatch.setattr(allocation, 'wait_for_capacity', lambda: {'admitted': True})


def test_long_footprint_links_both_physical_covers_and_is_not_a_packing_certificate():
    instance = ship()
    compiled, evidence = allocation.derive_hatch_allocation(instance)
    assert compiled.cover_ports == {'A': 'SGSIN', 'B': 'SGSIN'}
    assert compiled.forbids('box-0', '0020102') is None
    assert evidence['aggregate_witness'][0]['cover_cell_counts'] == [
        {'cover': 'A', 'cells': 1}, {'cover': 'B', 'cells': 1}]
    assert evidence['necessary_relaxation'] and evidence['aggregate_capacity_witness']
    assert not evidence['static_packing_witness'] and not evidence['checker_certificate']
    assert evidence['workers'] == 1


def test_cross_cover_long_boxes_cannot_have_different_ports_even_with_enough_total_cells():
    instance = ship(lengths=(40, 40), pods=('SGSIN', 'MYTPP'), rows=(1, 2))
    with pytest.raises(InfeasibleInstruction, match='INFEASIBLE.*necessary'):
        allocation.derive_hatch_allocation(instance)


def test_retained_cells_consume_capacity_and_fix_cover_ports():
    instance = ship(lengths=(20, 20), pods=('SGSIN', 'MYTPP'),
                    retained=(('box-0', '0010102'),))
    compiled, evidence = allocation.derive_hatch_allocation(instance)
    assert compiled.cover_ports == {'A': 'SGSIN', 'B': 'MYTPP'}
    assert evidence['retained_cover_ports'] == {'A': 'SGSIN'}
    assert {'cover': 'A', 'cells': 0} in evidence['available_cells']
    assert compiled.forbids('box-1', '0030102') is None
    assert compiled.forbids('box-1', '0010102')


def test_prior_instruction_excludes_physical_footprints_before_model():
    instance = ship(lengths=(20,), pods=('SGSIN',))
    base = ConstrainedInstance(instance, _all_boxes={'0010102'})
    compiled, evidence = allocation.derive_hatch_allocation(instance, base=base)
    assert evidence['physical_slot_counts'] == {'box-0': 1}
    # The caller's composite retains the closure; the hatch mask represents
    # only its canonical hatch instruction, independently of ordinary rules.
    assert base.forbids('box-0', '0010102')
    assert compiled.forbids('box-0', '0010102') is None
    assert compiled.forbids('box-0', '0030102') is None


def test_loading_only_bay_closure_does_not_reject_unmoved_retained_cargo():
    instance = ship(lengths=(20, 20), pods=('SGSIN', 'SGSIN'),
                    retained=(('box-0', '0010102'),))
    base = ConstrainedInstance(instance, _all_boxes={'0010102'})
    compiled, evidence = allocation.derive_hatch_allocation(instance, base=base)
    assert compiled.audit_positions({'box-0': '0010102', 'box-1': '0030102'}) == []
    assert evidence['physical_slot_counts'] == {'box-1': 1}
    assert evidence['retained_cover_ports'] == {'A': 'SGSIN'}


def test_uncovered_fixed_deck_has_capacity_without_a_cover_pod_selector():
    instance = ship(lengths=(20, 20), pods=('SGSIN', 'MYTPP'))
    for stack in instance.vessel.stacks:
        stack.hatch_cover_id = None
        stack.on_deck = True
    for cover in instance.vessel.hatch_covers:
        cover.hold_stack_ids = []
    compiled, evidence = allocation.derive_hatch_allocation(instance)
    assert compiled.forbids('box-0', '0010102') is None
    assert compiled.forbids('box-1', '0030102') is None
    assert evidence['available_cells'] == [{'cover': None, 'cells': 2}]


def test_unknown_solver_result_is_unavailable_and_never_an_infeasibility_claim(monkeypatch):
    fake = NS(parameters=NS(), Solve=lambda _: allocation.cp_model.UNKNOWN,
              StatusName=lambda _: 'UNKNOWN')
    monkeypatch.setattr(allocation.cp_model, 'CpSolver', lambda: fake)
    with pytest.raises(InfeasibleInstruction, match='unavailable: UNKNOWN; no infeasibility proof'):
        allocation.derive_hatch_allocation(ship())
