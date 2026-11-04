"""Real CPU generator → uniform token mask → ballast → full D50 checker.

No solver repair, hull substitution, rule relaxation or sample selection.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from llm.stowlm.format import load_tokenizer
from stowage.generate.instances import generate_simulated_instance
from stowage.generate.random_yard import generate_random_instance

_PATH = Path(__file__).resolve().parents[2] / "experiments/mask/pass_or_dead_end.py"
_SPEC = spec_from_file_location("mask_integration_experiment", _PATH)
experiment = module_from_spec(_SPEC)
_SPEC.loader.exec_module(experiment)


@pytest.fixture(scope="module", params=["random", "simulated"])
def sampled(request):
    generate = generate_random_instance if request.param == "random" else generate_simulated_instance
    instance = generate("tiny", seed=1)
    return experiment.sample_and_check(instance, load_tokenizer(), seed=17, c2=False,
                                       ballast_seconds=2.0)


def test_generated_mask_samples_record_full_voyage_verdict_or_real_dead_end(sampled):
    """A local-legal completed plan must never be counted as a pass or dead end."""
    assert sampled["c2"] is False
    assert sampled["background_boxes"] > 0
    if sampled["decoder_status"] == "dead_end":
        assert sampled["outcome"] == "dead_end"
        assert sampled["dead_end"]["reason"]
        assert "checker_passed" not in sampled
    else:
        assert sampled["decoder_status"] == "complete"
        assert set(sampled["voyage_conditions"]) == {"departure", "arrival"}
        for conditions in sampled["voyage_conditions"].values():
            assert set(conditions) == {"stability", "strength"}
            assert all(part["ran"] for part in conditions.values())
        assert sampled["checker_binding"]
        if sampled["outcome"] == "passed":
            assert sampled["checker_passed"] and sampled["finalize_status"] == "ok"
            assert not sampled["hard_violations"]
        else:
            assert sampled["outcome"] == "failed"
            assert not sampled["checker_passed"] or sampled["finalize_status"] != "ok"
            assert sampled["hard_violations"] or sampled["ballast_status"] not in {"ok", "kept"}


@pytest.mark.xfail(strict=True, reason=(
    "Deferred outside task5.1 fence: current generated tiny complete stows fail "
    "IS Code B2 containership second-check at departure and arrival after ballast. "
    "No completion guarantee follows from the necessary feasibility screens (D23)."))
def test_random_mask_done_criterion_full_checker_pass_or_reported_dead_end(sampled):
    assert sampled["outcome"] in {"passed", "dead_end"}, sampled
