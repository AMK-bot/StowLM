"""Pre-registered D185 boundary cases; synthetic checked results, no solvers."""
import pytest

from experiments.mvp.strategy.proposer.verdict import ARMS, d185_prefix_verdict, d185_verdict


def group(results, count):
    return {"arms": {arm: dict(maps=count, completed_maps=count, incomplete_maps=[],
                passing_maps=len(results[arm]), passing_map_moves=results[arm]) for arm in ARMS}}


def costs(count, value=100, *, start=0):
    return {f"m{index}": value for index in range(start, count + start)}


def groups(*, spark=None, glimmer=None, rule=None, search=None, small_model=100, small_rule=100):
    return dict(medium=group(dict(spark=spark if spark is not None else costs(4),
                    glimmer=glimmer if glimmer is not None else costs(4),
                    rule=rule if rule is not None else costs(4),
                    search=search if search is not None else costs(4)), 8),
                small=group(dict(spark=costs(2, small_model), glimmer=costs(2, small_model),
                                 rule=costs(2, small_rule), search=costs(2)), 2))


@pytest.mark.parametrize("k", [1, 4])
def test_exact_two_extra_passes_qualifies_at_registered_budgets(k):
    result = d185_prefix_verdict(k, groups(spark=costs(6)))
    assert result["status"] == "PASS"
    assert result["qualifying_models"] == ["spark"]
    assert result["models"]["spark"]["medium_pass_branch"] is True
    assert result["models"]["spark"]["medium_move_branch"] is False
    assert result["llm_parked"] is False


def test_one_extra_pass_without_move_improvement_does_not_qualify():
    result = d185_prefix_verdict(4, groups(spark=costs(5)))
    assert result["status"] == "NOT_JUSTIFIED"
    assert result["fine_tuning_justified"] is False


@pytest.mark.parametrize("model_cost,expected", [(95, "PASS"), (95.00001, "NOT_JUSTIFIED"),
                                                 (94.99999, "PASS")])
def test_exact_five_percent_medium_threshold(model_cost, expected):
    assert d185_prefix_verdict(4, groups(spark=costs(4, model_cost)))["status"] == expected


def test_decimal_boundary_has_no_float_rounding_rejection():
    result = d185_prefix_verdict(1, groups(spark=costs(4, .285), rule=costs(4, .3),
                                          search=costs(4, .3)))
    assert result["status"] == "PASS"


def test_fewer_moves_cannot_compensate_for_loss_of_medium_passes():
    result = d185_prefix_verdict(4, groups(spark=costs(3, 1)))
    assert result["status"] == "NOT_JUSTIFIED"
    assert result["models"]["spark"]["medium_no_pass_loss"] is False


@pytest.mark.parametrize("model_cost,expected", [(105, "PASS"), (105.00001, "NOT_JUSTIFIED"),
                                                 (104.99999, "PASS")])
def test_exact_small_five_percent_parity_boundary(model_cost, expected):
    assert d185_prefix_verdict(4, groups(spark=costs(6), small_model=model_cost))["status"] == expected


def test_small_rule_is_required_even_when_model_wins_medium_pass_branch():
    result = d185_prefix_verdict(4, groups(spark=costs(8), small_model=106))
    assert result["status"] == "NOT_JUSTIFIED"
    assert result["models"]["spark"]["small_within_five_percent"] is False


def test_small_is_only_mutual_move_parity_without_extra_pass_gate():
    data = groups(spark=costs(6))
    data["small"]["arms"]["spark"].update(passing_maps=1, passing_map_moves=costs(1))
    assert d185_prefix_verdict(4, data)["status"] == "PASS"


@pytest.mark.parametrize("count,expected", [(3, "INCONCLUSIVE"), (4, "NOT_JUSTIFIED")])
def test_medium_minimum_four_pass_gate_is_inclusive(count, expected):
    data = groups(**{arm: costs(count) for arm in ARMS})
    result = d185_prefix_verdict(4, data)
    assert result["status"] == expected
    assert result["maximum_medium_passes"] == count
    assert result["llm_parked"] is True


def test_four_model_passes_can_open_gate_and_win_by_two():
    result = d185_prefix_verdict(4, groups(spark=costs(4), glimmer=costs(2),
                                           rule=costs(2), search=costs(2)))
    assert result["status"] == "PASS"


@pytest.mark.parametrize("stronger", ["rule", "search"])
def test_pass_branch_must_beat_the_stronger_baseline(stronger):
    data = groups(spark=costs(6), **{stronger: costs(5)})
    result = d185_prefix_verdict(4, data)
    assert result["status"] == "NOT_JUSTIFIED"
    assert result["models"]["spark"]["medium_pass_gain"] == 1


@pytest.mark.parametrize("stronger", ["rule", "search"])
def test_move_branch_must_beat_each_baseline(stronger):
    data = groups(spark=costs(4, 95), **{stronger: costs(4, 99)})
    result = d185_prefix_verdict(4, data)
    assert result["status"] == "NOT_JUSTIFIED"
    assert result["models"]["spark"]["medium_moves"][stronger]["threshold_met"] is False


def test_move_branch_uses_each_baselines_respective_mutual_maps():
    data = groups(spark=costs(8, 90), rule=costs(4), search=costs(4, 100, start=4))
    result = d185_prefix_verdict(4, data)
    assert result["status"] == "PASS"
    assert result["models"]["spark"]["medium_moves"]["rule"]["mutual_maps"] == list(costs(4))
    assert result["models"]["spark"]["medium_moves"]["search"]["mutual_maps"] == list(costs(4, start=4))


def test_better_arm_does_not_invent_an_oracle_union_pass_count():
    result = d185_prefix_verdict(4, groups(spark=costs(6), rule=costs(4),
                                          search=costs(4, start=4)))
    assert result["status"] == "PASS"
    assert result["better_rule_search"]["passing_maps"] == 4


@pytest.mark.parametrize("size", ["small", "medium"])
@pytest.mark.parametrize("arm", ARMS)
def test_incomplete_checked_budget_cannot_supply_a_verdict(size, arm):
    data = groups(spark=costs(8, 1))
    data[size]["arms"][arm].update(completed_maps=0, incomplete_maps=["missing"])
    result = d185_prefix_verdict(4, data)
    assert result["status"] == "INCONCLUSIVE"
    assert "incomplete" in result["reasons"][0]


@pytest.mark.parametrize("size", ["small", "medium"])
def test_missing_size_cannot_supply_a_verdict(size):
    data = groups(spark=costs(8, 1))
    del data[size]
    assert d185_prefix_verdict(4, data)["status"] == "INCONCLUSIVE"


def test_missing_arm_cannot_supply_a_verdict():
    data = groups(spark=costs(8, 1))
    del data["medium"]["arms"]["rule"]
    assert d185_prefix_verdict(4, data)["status"] == "INCONCLUSIVE"


def test_unsupported_budget_cannot_supply_a_verdict():
    assert d185_prefix_verdict(4, groups(spark=costs(8, 1)), supported=False)["status"] == "INCONCLUSIVE"


def test_empty_small_mutual_set_is_not_vacuous_parity():
    data = groups(spark=costs(6))
    data["small"]["arms"]["spark"].update(passing_maps=0, passing_map_moves={})
    assert d185_prefix_verdict(4, data)["status"] == "NOT_JUSTIFIED"


def test_empty_medium_mutual_set_cannot_satisfy_move_branch():
    data = groups(spark=costs(4, 1, start=4))
    result = d185_prefix_verdict(4, data)
    assert result["status"] == "NOT_JUSTIFIED"
    assert result["models"]["spark"]["medium_moves"]["rule"]["n"] == 0


def test_zero_medium_tie_cannot_claim_five_percent_move_gain():
    result = d185_prefix_verdict(4, groups(**{arm: costs(4, 0) for arm in ARMS}))
    assert result["status"] == "NOT_JUSTIFIED"


def test_zero_small_tie_is_valid_parity():
    assert d185_prefix_verdict(4, groups(spark=costs(6), small_model=0, small_rule=0))["status"] == "PASS"


def test_k16_cannot_qualify_even_with_large_gain():
    data = {"16": dict(supported_by_all_budgets=True, sizes=groups(spark=costs(8, 1)))}
    result = d185_verdict(data)
    assert result["status"] == "INCONCLUSIVE"
    assert result["qualifying_budgets"] == []
    assert result["llm_parked"] is True


def test_one_eligible_passing_prefix_suffices_and_diagnostic_is_ignored():
    data = {"1": dict(supported_by_all_budgets=True, sizes=groups()),
            "4": dict(supported_by_all_budgets=True, sizes=groups(spark=costs(6))),
            "16": dict(supported_by_all_budgets=True, sizes=groups())}
    result = d185_verdict(data)
    assert result["status"] == "PASS"
    assert result["qualifying_budgets"] == [4]
    assert result["fine_tuning_justified"] is True


def test_glimmer_can_qualify_without_spark():
    result = d185_prefix_verdict(4, groups(glimmer=costs(4, 95)))
    assert result["qualifying_models"] == ["glimmer"]


def test_aggregate_not_justified_if_completed_prefix_fails_and_other_is_inconclusive():
    data = {"1": dict(supported_by_all_budgets=True, sizes=groups()),
            "4": dict(supported_by_all_budgets=False, sizes=groups(spark=costs(8)))}
    result = d185_verdict(data)
    assert result["status"] == "NOT_JUSTIFIED"
    assert result["llm_parked"] is True
