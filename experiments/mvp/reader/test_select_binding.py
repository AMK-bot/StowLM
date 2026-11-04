"""Synthetic eligibility regressions; never run validation planners or paid APIs."""
from types import SimpleNamespace as NS
import pytest
from llm.instructions.apply import ConstrainedInstance
from experiments.mvp.reader.select_binding import physical_slots_for_box, per_box_slot_screen, select


def ship(bays=(1,3), accepts_40=True):
    cells = [NS(id=f'{b:03d}0102',accepts_20=True,accepts_40=accepts_40,accepts_45=False) for b in bays]
    return NS(vessel=NS(cells=cells),containers=[NS(id='dg',length_ft=40),NS(id='dry',length_ft=20)],load_list=['dg','dry'])


def test_per_box_dg_closure_removes_all_long_slots_despite_global_capacity():
    instance=ship()
    compiled=ConstrainedInstance(instance,_per_box={'dg':{'0010102'}})
    assert len(compiled.open_cells())==2  # Original global screen passed.
    assert compiled.open_cells('dg')=={'0030102'}  # Cells survive but cannot fit 40ft.
    assert physical_slots_for_box(instance,instance.containers[0])==('0020102',)
    assert per_box_slot_screen(instance,compiled)==['dg']


def test_per_box_closure_can_leave_another_declared_physical_pair():
    instance=ship((1,3,5,7))
    compiled=ConstrainedInstance(instance,_per_box={'dg':{'0010102','0030102'}})
    assert '0060102' in physical_slots_for_box(instance,instance.containers[0])
    assert per_box_slot_screen(instance,compiled)==[]


def test_pair_requires_matching_partner_cell_and_size_acceptance():
    instance=ship((1,5))
    assert physical_slots_for_box(instance,instance.containers[0])==()
    instance=ship(accepts_40=False)
    assert physical_slots_for_box(instance,instance.containers[0])==()
    assert len(physical_slots_for_box(instance,instance.containers[1]))==2


def test_frozen_output_guard_precedes_any_dataset_access(tmp_path):
    output=tmp_path/'binding-cases.json'
    output.write_text('frozen')
    with pytest.raises(FileExistsError,match='frozen preregistered cohort'):
        select(output)
    assert output.read_text()=='frozen'
