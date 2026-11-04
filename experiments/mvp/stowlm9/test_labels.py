"""CPU regressions for deterministic labels and fail-closed admission."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("stowlm9_labels", Path(__file__).with_name("labels.py"))
labels = importlib.util.module_from_spec(spec)
spec.loader.exec_module(labels)


def case():
    from stowage.actions import get_action_space
    from stowage.actions.space import Place
    from stowage.model.fixtures import build_instance

    instance = build_instance()
    instance.id = "synthetic-d199-label-contract"
    instance.vessel.hull = None
    boxes = [b.model_copy(update={"dg": None, "oog": None, "special_stowage": [], "length_ft": 20,
             "iso_type": "22G1", "reefer": False, "reefer_setpoint_c": None,
             "height_m": 2.591, "gross_mass_kg": 12000.}) for b in instance.containers[:2]]
    instance.containers = boxes
    instance.load_list = [b.id for b in boxes]
    instance.discharge_list = []
    instance.yard.stacks = [instance.yard.stacks[0].model_copy(update={
        "key": f"A-00{i+1}-01", "length_ft": 20, "boxes": [b.id]}) for i, b in enumerate(boxes)]
    actions = [Place(boxes[0].id, "0010182"), Place(boxes[1].id, "0010282")]
    return instance, actions, get_action_space()


def test_same_final_stow_has_identical_one_placement_labels():
    instance, actions, space = case()
    first, info = labels.canonicalize(instance, space.expand(instance, actions))
    second, other = labels.canonicalize(instance, space.expand(instance, actions[::-1]))
    assert first.actions == second.actions == [space.encode(a) for a in actions]
    assert info["actions_sha256"] == other["actions_sha256"]
    assert first.moves == second.moves
    assert info["one_placement_per_box"] and info["actions"] == 2
    # Repeated canonicalization must be an idempotent serialization.
    third, _ = labels.canonicalize(instance, first)
    assert first.model_dump(mode="json") == third.model_dump(mode="json")


def test_bad_or_out_of_domain_plans_are_reported():
    instance, actions, space = case()
    plan = space.expand(instance, actions[:1])
    with pytest.raises(labels.LabelError, match="exactly one placement"):
        labels.canonicalize(instance, plan)
    instance, actions, space = case()
    plan = space.expand(instance, actions)
    instance.discharge_list = [actions[0].box]
    with pytest.raises(labels.LabelError, match="load-only"):
        labels.canonicalize(instance, plan)


class OrderEngine:
    """Only B,A completes: demonstrates deterministic order search, no new slots."""
    def __init__(self):
        self.path = []

    def is_legal(self, action):
        return not self.path or (self.path[0].box == "B" and action.box == "A")

    def push(self, action):
        self.path.append(action)

    def pop(self):
        self.path.pop()

    def is_complete(self):
        return len(self.path) == 2


def test_canonical_order_backtracks_without_changing_placements():
    from stowage.actions.space import Place
    actions = [Place("A", "0010182"), Place("B", "0010282")]
    result, nodes = labels._order_fixed(OrderEngine(), actions[::-1])
    assert result == actions[::-1] and nodes == 4
    with pytest.raises(labels.LabelError, match="exceeded"):
        labels._order_fixed(OrderEngine(), actions, max_nodes=2)


def test_no_legal_order_is_explicitly_rejected():
    from stowage.actions.space import Place
    engine = OrderEngine()
    engine.is_legal = lambda action: False
    with pytest.raises(labels.LabelError, match="no legal complete"):
        labels._order_fixed(engine, [Place("A", "0010182")])


def test_certification_requires_epoch_and_retains_unavailability(monkeypatch):
    from llm.exit import checker_watchdog, outcome_epoch
    from llm.exit.checker_watchdog import CheckOutcome
    instance, actions, space = case()
    plan, _ = labels.canonicalize(instance, space.expand(instance, actions))
    record, info = labels.certify(instance, plan, None)
    assert record is None and "expected outcome epoch" in info["error"]
    called = []
    epoch = {"test": "immutable-test-epoch"}
    monkeypatch.setattr(outcome_epoch, "assert_current", lambda value: called.append(value))
    def unavailable(instance, plan, **kwargs):
        assert kwargs["expected_outcome_epoch"] == epoch
        return CheckOutcome(False, reason="outcome_epoch_ack_mismatch")
    monkeypatch.setattr(checker_watchdog, "run_full_check", unavailable)
    record, info = labels.certify(instance, plan, epoch)
    assert record is None and info["unavailable_reason"] == "outcome_epoch_ack_mismatch"
    assert called == [epoch]


def test_checker_and_move_cost_disagreement_is_fatal(monkeypatch):
    from llm.exit import checker_watchdog, outcome_epoch
    from llm.exit.checker_watchdog import CheckOutcome
    import stowage.checker
    instance, actions, space = case()
    plan, _ = labels.canonicalize(instance, space.expand(instance, actions))
    monkeypatch.setattr(outcome_epoch, "assert_current", lambda value: None)
    monkeypatch.setattr(stowage.checker, "report_matches_inputs", lambda *args: True)
    monkeypatch.setattr("stowage.frozen.require_clean", lambda *args, **kwargs: None)
    report = SimpleNamespace(passed=True, count=SimpleNamespace(weighted_nonproductive=999.))
    monkeypatch.setattr(checker_watchdog, "run_full_check", lambda *a, **k: CheckOutcome(True, report=report))
    record, info = labels.certify(instance, plan, {"test": "epoch"})
    assert record is None and "weighted costs disagree" in info["error"]


def test_only_cheapest_passing_finite_candidate_wins_and_ties_keep_baseline():
    def record(cost):
        return dict(weighted_moves=cost, report={"passed": True}, mask={"passed": True})
    baseline, stream = record(8), record(7)
    assert labels.choose_cheapest(stream, baseline) is stream
    assert labels.choose_cheapest(record(8), baseline) is baseline
    assert labels.choose_cheapest(None, None) is None
    with pytest.raises(labels.LabelError, match="invalid checked objective"):
        labels.choose_cheapest(record(float("nan")), baseline)
    with pytest.raises(labels.LabelError, match="missing passing"):
        labels.choose_cheapest(dict(stream, mask={"passed": False}), baseline)
