"""Deterministic, hand-authored SFT contract fixtures; never physical gold.

No solver, RNG retry, mask-generated action selection or skip path is involved.
The hull-free variant exercises the real mask diagnostically; the complete hull
variant tests fail-closed production admission when linear evidence is missing.
"""

from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.model.fixtures import build_instance


def deterministic_case(n_boxes=1, *, hull=False):
    assert n_boxes in (1, 2, 3)
    instance = build_instance()
    instance.id = f"synthetic-sft-{n_boxes}"
    boxes = [box.model_copy(update={"dg": None, "oog": None, "special_stowage": [],
                                    "length_ft": 20, "iso_type": "22G1", "reefer": False,
                                    "reefer_setpoint_c": None, "height_m": 2.591,
                                    "gross_mass_kg": 12000.})
             for box in instance.containers[:n_boxes]]
    instance.containers = boxes
    instance.load_list = [box.id for box in boxes]
    instance.discharge_list = []
    instance.yard.stacks = [instance.yard.stacks[0].model_copy(update={
        "key": f"A-00{i + 1}-01", "length_ft": 20, "boxes": [box.id]})
        for i, box in enumerate(boxes)]
    if not hull:
        instance.vessel.hull = None
    slots = ("0010182", "0010282", "0030182")
    actions = [Place(box.id, slot) for box, slot in zip(boxes, slots)]
    # expand() supplies structured actions and move records independently of mask.
    plan = get_action_space().expand(instance, actions)
    return instance, plan


def diagnostic_engine(instance, space):
    """Actual default engine, with the fixture's absent hull explicitly diagnostic."""
    from stowage.feasibility import make_feasibility_engine
    return make_feasibility_engine(instance, space)
