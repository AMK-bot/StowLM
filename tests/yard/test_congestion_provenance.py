"""Synthetic D72 settings and achieved counts accompany active simulations only."""
from dataclasses import asdict

from pydantic_core import to_jsonable_python
import pytest

from stowage.model.fixtures import build_instance
from stowage.ports import load_simulator_params
from stowage.rules.catalogue import rule_freeze_v1
from stowage.yard import ShipCall, simulate_port


@pytest.mark.parametrize("noise", [0.0, .2, .5, .8])
def test_active_result_serializes_synthetic_share_and_empty_counts(noise):
    with rule_freeze_v1():
        result = simulate_port([], load_simulator_params("laem_chabang_like"), seed=23, noise=noise)
    provenance = to_jsonable_python(asdict(result))["provenance"]
    policy = provenance["congestion_mixing"]
    assert provenance["created_by"] == "yard.simulate_port"
    assert provenance["seed"] == 23
    assert provenance["versions"]["yard_simulator"] == "5.0.0"
    assert policy["synthetic"] is True
    assert policy["policy"] == "synthetic-independent-v2"
    assert policy["noise_level"] == noise
    assert policy["congestion_mix_share"] == .25
    assert policy["configured_share"] == (.25 if noise > 0 else 0)
    assert "queue" in policy["noise_zero_capacity_policy"]
    assert "idealized" in policy["noise_zero_capacity_policy"]
    assert policy["achieved_share"] == 0
    assert "unique" in policy["share_definition"]
    assert policy["eligible_boxes"] == policy["mixing_selected_boxes"] == policy["mixed_boxes"] == 0
    assert result.__dict__["_congestion_mixing"] is result.provenance.congestion_mixing


def test_default_mix_share_is_independent_of_configured_cluster_noise():
    with rule_freeze_v1():
        params = load_simulator_params("laem_chabang_like")
        result = simulate_port([], params, seed=7)
    assert result.provenance.congestion_mixing["configured_share"] == .25


@pytest.mark.parametrize("noise", [.2, .5, .8])
@pytest.mark.parametrize("share", [0, .1, 1])
def test_explicit_mix_share_is_independent_of_clustering_noise(noise, share):
    with rule_freeze_v1():
        result = simulate_port([], load_simulator_params("laem_chabang_like"), seed=7,
                               noise=noise, congestion_mix_share=share)
    policy = result.provenance.congestion_mixing
    assert policy["congestion_mix_share"] == policy["configured_share"] == share
    assert policy["noise_level"] == noise


def test_zero_noise_disables_even_an_explicit_mix_share():
    with rule_freeze_v1():
        result = simulate_port([], load_simulator_params("laem_chabang_like"), seed=7,
                               noise=0, congestion_mix_share=1)
    policy = result.provenance.congestion_mixing
    assert policy["congestion_mix_share"] == 1
    assert policy["configured_share"] == 0


@pytest.mark.parametrize("share", [-.01, 1.01, float("nan"), float("inf"), -float("inf")])
def test_mix_share_requires_a_finite_probability(share):
    with rule_freeze_v1(), pytest.raises(ValueError, match="congestion_mix_share"):
        simulate_port([], load_simulator_params("laem_chabang_like"), seed=7,
                      congestion_mix_share=share)


def test_legacy_result_does_not_add_provenance_or_new_wire_fields():
    with rule_freeze_v1(enabled=False):
        result = simulate_port([], load_simulator_params("laem_chabang_like"), seed=7, noise=.8)
    assert "provenance" not in asdict(result)
    assert not hasattr(result, "provenance")
    assert result.simulator_version == "3.0.0"


def test_simulation_provenance_retains_input_taint_without_mutation():
    with rule_freeze_v1():
        instance = build_instance()
        instance.containers, instance.load_list = [], []
        instance.yard.stacks = []
        instance.provenance.stubs = {"fixture-only": "synthetic test"}
        call = ShipCall("source-call", instance, 10, 0, {})
        before = instance.model_dump_json()
        result = simulate_port([call], load_simulator_params("laem_chabang_like"), seed=7, noise=.2)
    assert result.provenance.stubs == {"fixture-only": "synthetic test"}
    assert "source-call" in result.provenance.parents
    assert instance.model_dump_json() == before
