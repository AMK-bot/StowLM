"""CPU control-flow regressions: fake decoding, real result/binding models, no Metal."""
from types import ModuleType, SimpleNamespace
import sys

import pytest

from llm.stowlm.planner import StowLMPlanner
from stowage.actions.samples import tiny_instance
from stowage.model import BallastCondition, CheckReport, MoveCount, MovePlan, PartReport, Provenance


@pytest.fixture
def setup_selection(monkeypatch):
    import llm.stowlm.mask as mask
    import llm.stowlm.planner as module
    import stowage.checker as checker
    import stowage.feasibility as feasibility
    import stowage.moves as moves
    import stowage.planner as protocol
    from stowage.checker.full import _input_bindings
    from llm.stowlm import mlx_memory

    monkeypatch.setattr(mlx_memory, "memory_gb", lambda: {})

    instance = tiny_instance(0, n_load=1)
    state = SimpleNamespace(statuses=["complete", "complete"], objectives=[40, 44], rejected=set(),
                            throws=set(), checked=[], finalized=[], clock=0.0, generate_called=False,
                            setup_delay=0.0, decode_delay=0.0, generation_kwargs=None,
                            unballastable=set(), logprobs=[0, -1], report_mode="full",
                            check_delay={}, generate_error=False, level4={})

    class Engine:
        def __init__(self, index=0):
            self.index = index
        def copy(self):
            return Engine(self.index + 1)
        def to_plan(self):
            return MovePlan(id=f"candidate-{self.index}", instance_id=instance.id, call_index=instance.call_index)

    class Decoder:
        def __init__(self, engine, *args):
            self.engine = engine
            self.actions = []
            self.tokens = []
            self.status = "running"
            self.dead_end = None

    def generate(model, prompt, decoders, **kwargs):
        state.generate_called = True
        state.generation_kwargs = kwargs
        state.clock += state.decode_delay
        results = []
        for index, decoder in enumerate(decoders):
            decoder.status = state.statuses[index]
            decoder.tokens = [1]
            if decoder.status == "dead_end":
                decoder.dead_end = SimpleNamespace(reason="fixture dead end")
            results.append(SimpleNamespace(status=decoder.status, tokens=[1], logprob=state.logprobs[index]))
        if state.generate_error:
            raise RuntimeError("decode failed after discovering dead ends")
        if kwargs.get("on_step"):
            kwargs["on_step"](0, 0)
        return results, SimpleNamespace(summary=lambda: {"host_mask_s": 0.0, "wall_s": state.decode_delay})

    def finalize(instance, result, **kwargs):
        index = int(result.plan.id.rsplit("-", 1)[1])
        state.finalized.append(index)
        if index in state.unballastable:
            return result.model_copy(update={"status": "error", "message": "ballast not found"})
        if index in state.throws:
            raise RuntimeError("candidate ballast failed")
        plan = result.plan.model_copy(update={"ballast": BallastCondition(tank_masses_kg={}, source="finisher")})
        return result.model_copy(update={"plan": plan})

    def check(instance, plan):
        index = int(plan.id.rsplit("-", 1)[1])
        state.checked.append(index)
        state.clock += state.check_delay.get(index, 0.0)
        report = CheckReport(passed=index not in state.rejected, count=MoveCount(),
                           parts={n: PartReport(name=n) for n in
                                  ("moves", "rules", "stability", "strength", "visibility", "lashing", "constraints")},
                           provenance=Provenance(parents=list(_input_bindings(instance, plan))))
        report.parts["rules"].metrics.update(state.level4.get(index, {}))
        if state.report_mode == "detached":
            report.provenance = Provenance()
        elif state.report_mode == "incomplete":
            del report.parts["strength"]
        elif state.report_mode == "unrun":
            report.parts["stability"].ran = False
        return report

    fake_mlx = ModuleType("llm.stowlm.mask_mlx")
    fake_mlx.generate_masked = generate
    monkeypatch.setitem(sys.modules, "llm.stowlm.mask_mlx", fake_mlx)
    monkeypatch.setattr(mask, "MaskedDecoder", Decoder)
    monkeypatch.setattr(module, "load_tokenizer", lambda: object())
    monkeypatch.setattr(module, "codec_for", lambda *args: None)
    monkeypatch.setattr(module, "get_action_space", lambda: SimpleNamespace(expand=lambda inst, actions: Engine().to_plan()))
    monkeypatch.setattr(module, "encode_prompt", lambda *args: [1, 2])
    monkeypatch.setattr(feasibility, "make_feasibility_engine", lambda *args, **kwargs: Engine())
    monkeypatch.setattr(moves, "count_moves", lambda instance, plan: MoveCount(
        weighted_nonproductive=state.objectives[int(plan.id.rsplit("-", 1)[1])]))
    monkeypatch.setattr(protocol, "finalize", finalize)
    monkeypatch.setattr(checker, "check", check)
    monkeypatch.setattr(module.time, "perf_counter", lambda: state.clock)
    monkeypatch.setattr(module.time, "monotonic", lambda: state.clock)
    p = StowLMPlanner(n_samples=2)

    def load():
        state.clock += state.setup_delay
        return object()
    monkeypatch.setattr(p, "_load", load)
    return p, instance, state


def test_mixed_completion_counts_mask_dead_ends(setup_selection):
    p, instance, state = setup_selection
    state.statuses = ["complete", "dead_end"]
    result = p.plan(instance)
    assert result.metrics["mask_dead_ends"] == result.metrics["n_dead_end"] == 1


@pytest.mark.parametrize("throws", [False, True])
def test_best_of_k_keeps_higher_objective_checker_feasible_sibling(setup_selection, throws):
    p, instance, state = setup_selection
    (state.throws if throws else state.rejected).add(0)
    result = p.plan(instance)
    assert result.metrics["chosen_sample"] == 1
    assert result.metrics["chosen_objective"] == 44
    assert result.plan.ballast is not None
    assert state.finalized == [0, 1]
    assert state.checked == ([1] if throws else [0, 1])
    assert result.metrics["n_checker_feasible"] == 1
    assert result.metrics["candidate_selection_s"] >= 0
    assert not result.shippable  # pipeline still owns authoritative final checking/binding


def test_best_of_k_all_rejected_retains_complete_plan_for_repair(setup_selection):
    p, instance, state = setup_selection
    state.rejected = {0, 1}
    result = p.plan(instance)
    assert state.checked == [0, 1]
    assert result.plan is not None
    assert result.metrics["n_checker_feasible"] == 0
    assert not result.shippable


def test_elapsed_setup_budget_prevents_decode(setup_selection):
    p, instance, state = setup_selection
    state.setup_delay = 11
    result = p.plan(instance, time_limit_s=10)
    assert result.status == "timeout"
    assert not state.generate_called
    assert result.wall_time_s == 11


def test_elapsed_decode_budget_returns_timeout_partial_without_token_speed_guess(setup_selection):
    p, instance, state = setup_selection
    state.decode_delay = 11
    result = p.plan(instance, time_limit_s=10)
    assert result.status == "timeout"
    assert result.plan is not None
    assert state.generation_kwargs["max_tokens"] == p.max_tokens
    assert result.metrics["deadline_exceeded"] is True
    assert state.finalized == []


def test_ballast_failure_does_not_discard_feasible_sibling(setup_selection):
    p, instance, state = setup_selection
    state.unballastable.add(0)
    result = p.plan(instance)
    assert result.metrics["chosen_sample"] == 1
    assert state.checked == [1]
    assert result.metrics["candidate_failures"]["0"] == "ballast not found"


@pytest.mark.parametrize("mode", ["detached", "incomplete", "unrun"])
def test_candidate_selection_requires_bound_complete_full_checker_report(setup_selection, mode):
    p, instance, state = setup_selection
    state.report_mode = mode
    result = p.plan(instance)
    assert result.metrics["n_checker_feasible"] == 0
    assert state.checked == [0, 1]
    assert not result.shippable


@pytest.mark.parametrize("exhaustive", [False, True])
@pytest.mark.parametrize("logprobs, expected", [([-1, -0.1], 1), ([-1, -1], 0)])
def test_checker_feasible_ties_use_logprob_then_sample_index(setup_selection, logprobs, expected, exhaustive):
    p, instance, state = setup_selection
    p.exhaustive_selection = exhaustive
    state.objectives = [44, 44]
    state.logprobs = logprobs
    result = p.plan(instance)
    assert result.metrics["chosen_sample"] == expected
    # equal moves: rank order still checks the whole group (level 4 needs both reports)
    assert result.metrics["n_checker_feasible"] == 2
    assert sorted(state.checked) == [0, 1]


def test_deadline_during_selection_retains_previously_checked_candidate(setup_selection):
    """Exhaustive mode only: in rank order, a full pass ends selection, so no later deadline can follow it."""
    p, instance, state = setup_selection
    p.exhaustive_selection = True
    state.check_delay = {0: 1, 1: 10}
    state.objectives = [44, 40]
    result = p.plan(instance, time_limit_s=10)
    assert result.status == "timeout"
    assert result.plan.ballast is not None
    assert result.metrics["chosen_sample"] == 0
    assert result.metrics["n_checker_feasible"] == 1
    assert result.metrics["candidate_selection_s"] == 11
    assert result.metrics["deadline_exceeded"]


def test_decode_error_preserves_discovered_dead_end_count(setup_selection):
    p, instance, state = setup_selection
    state.statuses = ["dead_end", "dead_end"]
    state.generate_error = True
    result = p.plan(instance)
    assert result.status == "error"
    assert result.metrics["mask_dead_ends"] == result.metrics["n_dead_end"] == 2


def _factory_decoders(n):
    """decoder_factory giving n decoders with distinct candidate plans (the fixture's copy() reuses index 1)."""
    import llm.stowlm.mask as mask

    calls = []

    def factory(instance, engine, codec, config, k):
        calls.append(k)
        engine_cls = type(engine)
        return [mask.MaskedDecoder(engine_cls(i)) for i in range(k)]
    return factory, calls


@pytest.mark.parametrize("trial", range(40))
def test_rank_order_first_pass_selects_the_same_plan_as_checking_all(setup_selection, trial):
    """SRC_PATCH §3a: checking in (objective, -logprob, index) order and stopping at the first full pass picks
    exactly the candidate exhaustive checking picks, including ties, rejected, unballastable and throwing ones."""
    import random

    p, instance, state = setup_selection
    rng = random.Random(trial)
    k = rng.randint(1, 6)
    state.statuses = [rng.choice(["complete", "complete", "complete", "dead_end"]) for _ in range(k)]
    state.objectives = [rng.choice([40, 42, 44]) for _ in range(k)]
    state.logprobs = [rng.choice([-1.0, -0.5, -2.0]) for _ in range(k)]
    state.rejected = {i for i in range(k) if rng.random() < 0.35}
    state.unballastable = {i for i in range(k) if rng.random() < 0.15}
    state.throws = {i for i in range(k) if rng.random() < 0.1}
    state.level4 = {i: {"heavy_on_deck_count": float(rng.choice([0, 1])), "killed_slots": float(rng.choice([0, 2]))}
                    for i in range(k) if rng.random() < 0.7}
    factory, _ = _factory_decoders(k)
    p.n_samples, p.decoder_factory = k, factory
    chosen = {}
    for exhaustive in (True, False):
        state.checked, state.finalized = [], []
        p.exhaustive_selection = exhaustive
        result = p.plan(instance)
        chosen[exhaustive] = (result.status, result.metrics.get("chosen_sample"), result.metrics.get("chosen_objective"),
                              result.message, None if result.plan is None else result.plan.ballast is not None)
        if not exhaustive:
            assert result.metrics.get("selection_mode", "rank_order_first_pass") == "rank_order_first_pass"
            assert len(state.finalized) <= sum(s == "complete" for s in state.statuses)
    assert chosen[False] == chosen[True]


def test_rank_order_checks_only_until_the_best_feasible(setup_selection):
    p, instance, state = setup_selection
    factory, _ = _factory_decoders(5)
    p.n_samples, p.decoder_factory = 5, factory
    state.statuses = ["complete"] * 5
    state.objectives = [44, 40, 42, 40, 46]
    state.logprobs = [0, -1, 0, -0.5, 0]
    state.rejected = {3}  # best-ranked (40, lp -0.5) fails; next (40, lp -1) is index 1
    result = p.plan(instance)
    assert result.metrics["chosen_sample"] == 1 and state.checked[:2] == [3, 1]
    assert result.metrics["n_candidates_checked"] == 2
    assert state.checked[2:] == [0]  # D76 pass@1 checks sample 0 afterwards; selection is unaffected


def test_decoder_factory_hook_supplies_the_decoders(setup_selection):
    p, instance, state = setup_selection
    factory, calls = _factory_decoders(2)
    factory.name = "fixture-backend"
    p.decoder_factory = factory
    result = p.plan(instance)
    assert calls == [2] and result.status == "ok" and result.metrics["chosen_sample"] == 0
    p.decoder_factory = lambda *args: []  # wrong count: an error result, never an exception
    assert p.plan(instance).status == "error"


def test_decoder_backend_enters_evaluation_identity(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "model.safetensors").write_bytes(b"w")
    factory = lambda *args: []  # noqa: E731
    factory.name = "process-pool-4"
    config = StowLMPlanner(model_path=model, decoder_factory=factory).evaluation_config
    assert config["decoder_backend"] == "process-pool-4" and config["selection"].startswith("rank-order")
    assert "decoder_backend" not in StowLMPlanner(model_path=model).evaluation_config


@pytest.mark.parametrize("exhaustive", [False, True])
def test_level4_tie_breaks_follow_moves_before_logprob(setup_selection, exhaustive):
    """Logic review (D11): among equal-move feasible plans, fewer heavy boxes on deck, then fewer killed slots,
    beat model confidence; a plan with more moves never wins on level 4."""
    p, instance, state = setup_selection
    factory, _ = _factory_decoders(4)
    p.n_samples, p.decoder_factory, p.exhaustive_selection = 4, factory, exhaustive
    state.statuses = ["complete"] * 4
    state.objectives = [40, 40, 40, 38]
    state.logprobs = [0, -1, -2, -3]
    state.rejected = {3}  # the 38-move plan fails, so the 40-move group decides
    state.level4 = {0: {"heavy_on_deck_count": 2}, 1: {"heavy_on_deck_count": 1, "killed_slots": 3},
                    2: {"heavy_on_deck_count": 1, "killed_slots": 0}}
    result = p.plan(instance)
    assert result.metrics["chosen_sample"] == 2
    assert result.metrics["chosen_level4"] == {"heavy_on_deck_count": 1.0, "killed_slots": 0.0}
    if not exhaustive:
        assert sorted(state.checked) == [0, 1, 2, 3]



def test_pass_at_1_reuses_the_selection_verdict_for_sample_0(setup_selection):
    """D76: pass@1 = sample 0 (by index, no selection) judged by ballast + full checker."""
    p, instance, state = setup_selection
    state.objectives = [40, 44]
    result = p.plan(instance)
    assert result.metrics["chosen_sample"] == 0
    assert result.metrics["pass_at_1"] == {"sample": 0, "complete": True, "checker_passed": True, "objective": 40,
                                           "checked_in_selection": True}
    assert state.checked == [0]


@pytest.mark.parametrize("sample0_passes", [True, False])
def test_pass_at_1_checks_sample_0_when_early_stop_skipped_it(setup_selection, sample0_passes):
    p, instance, state = setup_selection
    state.objectives = [44, 40]  # sample 1 is ranked first and passes, so selection never checks sample 0
    if not sample0_passes:
        state.rejected = {0}
    result = p.plan(instance)
    assert result.metrics["chosen_sample"] == 1 and result.status == "ok"
    p1 = result.metrics["pass_at_1"]
    assert p1["checked_in_selection"] is False and p1["checker_passed"] is sample0_passes
    assert p1["objective"] == (44 if sample0_passes else None) and state.checked == [1, 0]


def test_pass_at_1_of_a_dead_end_first_sample(setup_selection):
    p, instance, state = setup_selection
    state.statuses = ["dead_end", "complete"]
    result = p.plan(instance)
    assert result.metrics["pass_at_1"]["complete"] is False and result.metrics["pass_at_1"]["checker_passed"] is False
    assert result.metrics["chosen_sample"] == 1


def test_pass_at_1_failure_never_changes_the_delivered_plan(setup_selection):
    """A failing pass@1 check (here sample 0's ballast finisher raises) is recorded, never propagated: the selected
    plan and status stay as selection decided (the deadline branch is guarded the same way)."""
    p, instance, state = setup_selection
    state.objectives = [44, 40]
    state.throws = {0}
    result = p.plan(instance)
    assert result.status == "ok" and result.metrics["chosen_sample"] == 1
    assert result.metrics["pass_at_1"]["checker_passed"] is False
    assert "candidate ballast failed" in result.metrics["pass_at_1"]["reason"]
