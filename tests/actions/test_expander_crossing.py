"""C6/D69 expander regressions with legacy behavior explicitly retained."""
import pytest

from stowage.actions.expander import Expander
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.model.ship import Placement
from stowage.model.yard import YardStack
from stowage.moves import apply_move, initial_state
from stowage.rules.catalogue import rule_freeze_v1


def scenario():
    inst = tiny_instance(91, n_load=2, n_other=3, p40=0, max_tiers=2)
    inst.port.max_crane_height_tiers = 2
    ids = [box.id for box in inst.containers]
    inst.yard.stacks = [YardStack(key='A-001-02', length_ft=20, boxes=[ids[0]]),
                        YardStack(key='A-001-01', length_ft=20, boxes=ids[2:4]),
                        YardStack(key='B-001-01', length_ft=20, boxes=[ids[1], ids[4]])]
    return inst, ids


@pytest.mark.parametrize('enabled', [False, True])
def test_dig_out_clears_h_high_stack_between_load_and_lane(enabled):
    inst, ids = scenario()
    with rule_freeze_v1(enabled=enabled):
        ex = Expander(inst)
        moves = ex.step(Place(ids[0], '0010182'))
        assert [m.kind for m in moves] == (['relocate', 'load'] if enabled else ['load'])
        state = initial_state(inst)
        for move in moves:
            state = apply_move(inst, state, move)
        assert state.ship.placements[-1].container_id == ids[0]


def test_yard_target_does_not_cross_h_high_stack():
    inst, ids = scenario()
    # Default legacy scoring chooses a clean stack across row 2. Active search
    # must choose a reachable position instead.
    inst.port.yard.blocks[0].n_rows = 4
    inst.yard.stacks = [YardStack(key='A-001-01', length_ft=20, boxes=[ids[0], ids[4]]),
                        YardStack(key='A-001-02', length_ft=20, boxes=ids[2:4])]
    with rule_freeze_v1():
        ex = Expander(inst)
        moves = ex.step(Place(ids[0], '0010182'))
        assert moves[0].dst.rsplit('-', 1)[0] != 'A-001-03'
        state = initial_state(inst)
        for move in moves:
            state = apply_move(inst, state, move)


def test_mask_probe_is_pure_and_screens_pending_lift_rows():
    inst, ids = scenario()
    inst.yard.stacks = [YardStack(key='A-001-01', length_ft=20, boxes=[ids[0], ids[1]])]
    inst.onboard.placements = [Placement(container_id=ids[2], slot='0010282')]
    with rule_freeze_v1():
        ex = Expander(inst)
        before = ex.copy().__dict__
        action = Place(ids[0], '0010284')
        assert ex.can_expand(action)  # no load-domain claim means unknown
        assert not ex.can_expand(action, pending_load_targets={ids[1]: ['0010182']})
        assert ex.can_expand(action, pending_load_targets={ids[1]: ['0010182', '0030182']})
        assert ex.__dict__ == before


def test_rejected_active_action_does_not_mutate_expander():
    inst, ids = scenario()
    with rule_freeze_v1():
        ex = Expander(inst)
        before = ex.copy().__dict__
        with pytest.raises(ValueError):
            ex.step(Place(ids[0], '0010184'))  # unsupported tier
        assert ex.__dict__ == before


def test_self_blocking_screen_allows_future_forced_hatch_clearing():
    inst = tiny_instance(91, n_load=3, n_other=0, p40=0, max_tiers=2)
    inst.port.max_crane_height_tiers = 2
    a, b, c = inst.load_list
    # A real cover can span multiple transverse rows. Its next hold access
    # automatically removes the newly H-high deck row before crossing it.
    cover = inst.vessel.hatch_covers[0]
    for other in inst.vessel.hatch_covers[1:]:
        cover.deck_stack_ids += other.deck_stack_ids
        cover.hold_stack_ids += other.hold_stack_ids
        for stack in inst.vessel.stacks:
            if stack.hatch_cover_id == other.id:
                stack.hatch_cover_id = cover.id
    inst.vessel.hatch_covers = [cover]
    with rule_freeze_v1():
        ex = Expander(inst)
        ex.step(Place(a, '0010282'))
        assert ex.can_expand(Place(b, '0010284'), pending_load_targets={c: ['0010102']})
        ex.step(Place(b, '0010284'))
        moves = ex.step(Place(c, '0010102'))
        assert sum(m.kind == 'restow_off' for m in moves) == 2


def test_crossing_stack_is_cleared_before_vertical_dig_out():
    inst = tiny_instance(95, n_load=1, n_other=3, p40=0, max_tiers=2)
    inst.port.max_crane_height_tiers = 2
    ids = [box.id for box in inst.containers]
    inst.port.yard.blocks = [inst.port.yard.blocks[0]]
    inst.port.yard.blocks[0].n_rows = 4
    inst.port.yard.blocks[0].n_bays = 1
    inst.yard.stacks = [YardStack(key='A-001-04', length_ft=20, boxes=ids[:2]),
                        YardStack(key='A-001-03', length_ft=20, boxes=ids[2:])]
    with rule_freeze_v1():
        ex = Expander(inst)
        moves = ex.step(Place(ids[0], '0010182'))
        assert moves[0].container_id == ids[3]
        assert [m.kind for m in moves] == ['relocate', 'relocate', 'load']
        state = initial_state(inst)
        for move in moves:
            state = apply_move(inst, state, move)


def test_self_blocking_screen_unions_long_box_subbays():
    inst, ids = scenario()
    inst.containers[0].length_ft = 40
    inst.yard.stacks = [YardStack(key='A-001-01', length_ft=40, boxes=[ids[0]]),
                        YardStack(key='B-001-01', length_ft=20, boxes=[ids[1]])]
    inst.onboard.placements = [Placement(container_id=ids[2], slot='0010282'),
                               Placement(container_id=ids[3], slot='0030282')]
    with rule_freeze_v1():
        ex = Expander(inst)
        action = Place(ids[0], '0020284')
        assert ex.can_expand(action)
        assert not ex.can_expand(action, pending_load_targets={ids[1]: ['0010182', '0030182']})


@pytest.mark.parametrize('length', [40, 45])
@pytest.mark.parametrize('lane', ['low', 'high'])
def test_long_yard_load_clears_crossing_in_secondary_subbay(length, lane):
    inst, ids = scenario()
    inst.containers[0].length_ft = length
    inst.port.yard.blocks[0].truck_lane_side = lane
    source_row, blocker_row = (2, 1) if lane == 'low' else (1, 2)
    inst.yard.stacks = [YardStack(key=f'A-001-{source_row:02}', length_ft=length, boxes=[ids[0]]),
                        YardStack(key=f'A-002-{blocker_row:02}', length_ft=20, boxes=ids[2:4])]
    with rule_freeze_v1():
        ex = Expander(inst)
        moves = ex.step(Place(ids[0], '0020182'))
        assert [m.kind for m in moves] == ['relocate', 'load']
        assert moves[0].container_id == ids[3]
        state = initial_state(inst)
        for move in moves:
            state = apply_move(inst, state, move)
