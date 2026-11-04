"""StowLM planner (plan 8.1/6.2): protocol (CPU, no MLX import) and masked decoding end to end with a fake model
(no weights, but MLX arrays, so marked ``gpu``: Metal-less sandboxes must not import MLX, docs/architecture.md)."""

import pytest

from llm.stowlm.planner import StowLMPlanner
from stowage.actions.samples import tiny_instance
from stowage.feasibility import FeasibilityConfig
from stowage.planner import Planner

VOCAB = 202048


class _Cache:
    keys = values = None
    state = ()


class FakeModel:
    """Uniform logits: under the mask, greedy decoding picks the smallest allowed token id each step."""

    def make_cache(self):
        return [_Cache()]

    def __call__(self, ids, cache=None):
        import mlx.core as mx

        return mx.zeros((ids.shape[0], ids.shape[1], VOCAB))


NO_C2 = FeasibilityConfig(extra={"global_screen": 0})  # fixture hull has no linear-model inputs (as in test_mask.py)


def planner_with(model, **kw):
    p = StowLMPlanner(engine_config=NO_C2, **kw)
    p._model = model
    return p


def test_protocol_and_names():
    assert isinstance(StowLMPlanner(), Planner)
    assert StowLMPlanner().name == "stowlm.base"
    assert StowLMPlanner(adapter_path="x/adapter").name == "stowlm"
    assert StowLMPlanner(temperature=0).deterministic and not StowLMPlanner(temperature=1).deterministic


@pytest.mark.gpu
def test_masked_plan_is_engine_legal_or_dead_end():
    from stowage.moves.engine import apply_plan

    statuses = set()
    for seed in range(6):
        inst = tiny_instance(seed, n_load=4)
        res = planner_with(FakeModel()).plan(inst, seed=0)
        statuses.add(res.status)
        assert res.status in ("ok", "dead_end"), res.message
        assert res.metrics["tokens_out"] > 0 and res.planner == "stowlm.base"
        if res.status == "ok":
            # Best-of-k now tries ballast and the full checker before selecting a candidate.
            assert apply_plan(inst, res.plan).legal
            assert res.metrics["n_actions"] >= len(inst.load_list)  # every load placed (fixtures may need more)
    assert "ok" in statuses


@pytest.mark.gpu
def test_errors_are_results_not_exceptions():
    class Broken(FakeModel):
        def __call__(self, ids, cache=None):
            raise RuntimeError("boom")

    res = planner_with(Broken()).plan(tiny_instance(0, n_load=4))
    assert res.status == "error" and "boom" in res.message


@pytest.mark.gpu
def test_best_of_k_picks_lowest_objective_complete_sample():
    from stowage.moves import count_moves

    p = planner_with(FakeModel(), temperature=1.0, n_samples=6)
    assert p.version.endswith("best-of-6@T1") and not p.deterministic
    for seed in range(4):
        inst = tiny_instance(seed, n_load=4)
        res = p.plan(inst, seed=seed)
        m = res.metrics
        assert m["n_samples"] == 6 and m["n_complete"] + m["n_dead_end"] + m["n_max_tokens"] == 6
        assert 0 <= m["host_share"] <= 1
        if res.status == "ok":
            assert m["chosen_objective"] == min(m["checker_feasible_objectives"] or m["sample_objectives"])
            assert count_moves(inst, res.plan).weighted_nonproductive == m["chosen_objective"]
        else:
            assert m["n_complete"] == 0
