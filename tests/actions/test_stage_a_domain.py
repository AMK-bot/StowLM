"""D69 sequencing screen is relative to the Stage A no-ship-shift domain."""
import pytest

from stowage.actions.expander import Expander
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.model.ship import Placement
from stowage.model.yard import YardStack
from stowage.moves import apply_move
from stowage.rules.catalogue import rule_freeze_v1


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('stage', ['A', 'B'])
def test_aboard_place_is_excluded_only_from_active_stage_a(stage, enabled):
    inst = tiny_instance(8, n_load=1, n_other=0, p40=0)
    inst.stage = stage
    cid = inst.load_list[0]
    with rule_freeze_v1(enabled=enabled):
        ex = Expander(inst)
        ex.step(Place(cid, '0010182'))
        before = ex.world_state()
        shift = Place(cid, '0030182')
        if enabled and stage == 'A':
            assert not ex.can_expand(shift)
            with pytest.raises(ValueError, match='Stage A'):
                ex.step(shift)
            assert ex.world_state() == before
        else:
            assert ex.can_expand(shift)
            moves = ex.step(shift)
            assert len(moves) == 1 and moves[0].kind == 'restow_shift'
            assert apply_move(inst, before, moves[0]) == ex.world_state()


def test_stage_b_does_not_apply_stage_a_self_blocking_screen():
    inst = tiny_instance(9, n_load=2, n_other=1, p40=0, max_tiers=2)
    inst.stage = 'B'
    inst.port.max_crane_height_tiers = 2
    a, b, base = [box.id for box in inst.containers]
    inst.yard.stacks = [YardStack(key='A-001-01', length_ft=20, boxes=[a, b])]
    inst.onboard.placements = [Placement(container_id=base, slot='0010282')]
    with rule_freeze_v1():
        ex = Expander(inst)
        # Stage B can rescue the far lift by shifting this near stack's top.
        assert ex.can_expand(Place(a, '0010284'), pending_load_targets={b: ['0010182']})
