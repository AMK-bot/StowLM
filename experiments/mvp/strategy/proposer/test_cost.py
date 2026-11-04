"""Offline cost guards account for complete frozen pools, never only winners."""
import hashlib
import json

import pytest

from experiments.mvp.strategy.proposer.cost import (
    preparation_budget_plan, validate_spark_source_cost,
)


def row(**usage):
    return {"map_id": "medium-one", "arm": "spark", "attempt": 1,
            "text": "invalid layout still costs", "usage": usage}


def source(*rows):
    return ("\n".join(json.dumps(r) for r in rows) + "\n").encode()


def billed(cost):
    return row(billed_cost_usd=cost, billed_cost_includes_all_tokens=True)


def test_under_two_and_actual_billing_preferred():
    response = billed(1.99)
    response["usage"].update(input_tokens=100_000_000, output_tokens=100_000_000,
                             output_tokens_includes_reasoning=True)
    data = source(response)
    result = validate_spark_source_cost(data, expected_sha256=hashlib.sha256(data).hexdigest())
    assert result["total_cost_usd"] == 1.99
    assert result["total_cost_usd_decimal"] == "1.99"
    assert result["method"] == "actual-billed-cost"
    assert result["spark_rows"] == result["total_rows"] == 1
    assert result["calls_made"] is False


@pytest.mark.parametrize("cost", [2, 2.0, 2.01])
def test_exact_two_and_over_two_fail(cost):
    with pytest.raises(ValueError, match="strictly under"):
        validate_spark_source_cost(source(billed(cost)))


def test_recorded_rates_include_reasoning():
    result = validate_spark_source_cost(source(row(input_tokens=1000, output_tokens=2000,
                                                   output_tokens_includes_reasoning=True)))
    assert result["total_cost_usd"] == 0.00975
    assert result["method"] == "recorded-token-rates"


def test_expected_hash_mismatch_fails_before_reading():
    with pytest.raises(ValueError, match="sha256"):
        validate_spark_source_cost(b"not json", expected_sha256="0" * 64)


@pytest.mark.parametrize("response", [
    {"arm": "spark", "text": "BLOCKS"},
    row(input_tokens=100),
    row(input_tokens=100, output_tokens=100),
    row(input_tokens=100, output_tokens=100, output_tokens_includes_reasoning=False),
    row(input_tokens=100, output_tokens=100, output_tokens_includes_reasoning="true"),
    row(billed_cost_usd=0.1),
    row(billed_cost_usd=0.1, billed_cost_includes_all_tokens=False),
])
def test_missing_or_incomplete_usage_fails_closed(response):
    with pytest.raises(ValueError):
        validate_spark_source_cost(source(response))


@pytest.mark.parametrize("amount", [-1, True, "0.1", float("nan"), float("inf")])
def test_cost_numeric_finite_nonnegative(amount):
    with pytest.raises(ValueError, match="finite nonnegative"):
        validate_spark_source_cost(source(billed(amount)))


@pytest.mark.parametrize("amount", [-1, True, "10", 1.1, float("nan"), float("inf")])
def test_token_counts_require_nonnegative_integers_even_if_billed(amount):
    response = billed(0.1)
    response["usage"]["output_tokens"] = amount
    with pytest.raises(ValueError, match="nonnegative integer"):
        validate_spark_source_cost(source(response))


def test_invalid_and_unconsumed_rows_count_towards_total():
    responses = [billed(0.9), billed(1.1)]
    responses[0]["text"] = "INVALID"
    responses[1].update(map_id="unused-map", attempt=256, text="UNCONSUMED")
    with pytest.raises(ValueError, match="strictly under"):
        validate_spark_source_cost(source(*responses))


def test_whole_pool_has_all_rows_including_non_spark_metadata():
    data = source(billed(0.1), {"arm": "glimmer", "text": "glimmer has no Spark cost"}, billed(0.2))
    result = validate_spark_source_cost(data)
    assert result["spark_rows"] == 2
    assert result["total_rows"] == 3
    assert result["total_cost_usd"] == 0.3


def test_mixed_actual_and_rate_usage():
    result = validate_spark_source_cost(source(billed(0.1), row(input_tokens=1000, output_tokens=0,
                                                               output_tokens_includes_reasoning=True)))
    assert result["total_cost_usd"] == 0.10125
    assert result["method"] == "mixed-actual-and-recorded-rates"


@pytest.mark.parametrize("limit", [0, -1, 2.01, True, float("inf")])
def test_caller_cannot_weaken_d185_cap(limit):
    with pytest.raises(ValueError):
        validate_spark_source_cost(source(billed(0.1)), limit_usd=limit)


def test_custom_stricter_cap_is_strict():
    with pytest.raises(ValueError, match="strictly under"):
        validate_spark_source_cost(source(billed(0.1)), limit_usd=0.1)


@pytest.mark.parametrize("data", [b"", b'{"arm":"unknown"}\n', b'{"arm":"glimmer"}\n',
                                    b'[]\n', b'{"arm":"spark","arm":"glimmer"}\n', b'\xff'])
def test_malformed_empty_or_unchargeable_pool_rejected(data):
    with pytest.raises(ValueError):
        validate_spark_source_cost(data)


def measurements():
    return {"measurements": [{"size": "small", "map_id": "s", "prompt": {"glimmer_raw_tokens": 1000}},
                              {"size": "medium", "map_id": "m", "prompt": {"glimmer_raw_tokens": 2000}},
                              {"size": "full", "map_id": "sizing-only", "prompt": {"glimmer_raw_tokens": 999999}}]}


def test_preparation_includes_all_max_tries_and_reasoning_cap_not_full_ship():
    result = preparation_budget_plan(measurements())
    assert len(result["maps"]) == 2
    assert result["max_proposals_per_map"] == 256
    assert result["output_token_cap_including_reasoning"] == 4096
    assert result["estimated_usd_at_max_proposals"] == pytest.approx(9.872896)
    assert result["estimated_under_cap"] is False
    assert result["estimated_usd_by_proposal_count"]["4"] == pytest.approx(0.154264)
    assert "No guarantee" in result["estimate_caveat"]
    assert result["calls_made"] is False


@pytest.mark.parametrize("kwargs", [{"max_proposals": 0}, {"max_proposals": True},
                                    {"output_token_cap": -1}, {"output_token_cap": 1.1}])
def test_bad_preparation_budgets_rejected(kwargs):
    with pytest.raises(ValueError):
        preparation_budget_plan(measurements(), **kwargs)


def test_preparation_empty_or_duplicate_maps_rejected():
    with pytest.raises(ValueError):
        preparation_budget_plan({})
    data = measurements()
    data["measurements"].append(data["measurements"][0])
    with pytest.raises(ValueError, match="duplicate"):
        preparation_budget_plan(data)


def test_campaign_deduplicates_same_source_for_k1_k4(tmp_path):
    from experiments.mvp.strategy.proposer.cost import assert_registry_covers, register_spark_pool
    cost = validate_spark_source_cost(source(billed(1.1)))
    path = tmp_path / "campaign.json"
    first = register_spark_pool(cost, path)
    second = register_spark_pool(cost, path)
    assert first == second
    assert len(second["pools"]) == 1
    assert second["campaign_total_cost_usd"] == 1.1
    assert assert_registry_covers(cost, second)["unique_pools"] == 1
    assert json.loads(path.read_text()) == second
    assert not path.with_name(path.name + ".lock").exists()


def test_campaign_two_individually_valid_pools_cannot_exceed_total_cap(tmp_path):
    from experiments.mvp.strategy.proposer.cost import register_spark_pool
    first = validate_spark_source_cost(source(billed(1.1)))
    other_row = billed(1.1)
    other_row["map_id"] = "different-pool"
    second = validate_spark_source_cost(source(other_row))
    path = tmp_path / "campaign.json"
    registered = register_spark_pool(first, path)
    with pytest.raises(ValueError, match="across all unique pools"):
        register_spark_pool(second, path)
    assert json.loads(path.read_text()) == registered
    assert not path.with_name(path.name + ".lock").exists()


def test_campaign_exact_two_from_different_pools_rejected(tmp_path):
    from experiments.mvp.strategy.proposer.cost import register_spark_pool
    path = tmp_path / "campaign.json"
    register_spark_pool(validate_spark_source_cost(source(billed(0.9))), path)
    with pytest.raises(ValueError, match="strictly under"):
        register_spark_pool(validate_spark_source_cost(source(billed(1.1))), path)


def test_campaign_same_source_cannot_change_cost(tmp_path):
    from copy import deepcopy
    from experiments.mvp.strategy.proposer.cost import register_spark_pool
    cost = validate_spark_source_cost(source(billed(0.1)))
    path = tmp_path / "campaign.json"
    first = register_spark_pool(cost, path)
    changed = deepcopy(cost)
    changed.update(total_cost_usd=0.2, total_cost_usd_decimal="0.2")
    with pytest.raises(ValueError, match="metadata changed"):
        register_spark_pool(changed, path)
    assert json.loads(path.read_text()) == first
    assert not path.with_name(path.name + ".lock").exists()


def test_campaign_owned_lock_only(tmp_path):
    from experiments.mvp.strategy.proposer.cost import register_spark_pool
    path = tmp_path / "campaign.json"
    lock = path.with_name(path.name + ".lock")
    lock.write_text("another owner's lock")
    with pytest.raises(ValueError, match="locked"):
        register_spark_pool(validate_spark_source_cost(source(billed(0.1))), path)
    assert lock.read_text() == "another owner's lock"
    assert not path.exists()


def test_campaign_snapshot_must_cover_source_and_totals(tmp_path):
    from copy import deepcopy
    from experiments.mvp.strategy.proposer.cost import assert_registry_covers, register_spark_pool
    cost = validate_spark_source_cost(source(billed(0.1)))
    registry = register_spark_pool(cost, tmp_path / "campaign.json")
    changed = deepcopy(registry)
    changed["campaign_total_cost_usd_decimal"] = "0"
    with pytest.raises(ValueError, match="total"):
        assert_registry_covers(cost, changed)
    other = validate_spark_source_cost(source(billed(0.2)))
    with pytest.raises(ValueError, match="does not cover"):
        assert_registry_covers(other, registry)


def test_campaign_bad_registry_fails_closed_releasing_owned_lock(tmp_path):
    from experiments.mvp.strategy.proposer.cost import register_spark_pool
    path = tmp_path / "campaign.json"
    path.write_text("broken json")
    with pytest.raises(ValueError, match="invalid Spark campaign"):
        register_spark_pool(validate_spark_source_cost(source(billed(0.1))), path)
    assert path.read_text() == "broken json"
    assert not path.with_name(path.name + ".lock").exists()
