"""D185's pre-registered decision, using completed terminal checked budgets.

The better rule/search baseline means their maximum pass count. The move branch
must beat each baseline on its respective mutual maps. This avoids selecting a
weaker baseline after seeing a model's result.
"""
from __future__ import annotations

from fractions import Fraction


ARMS = ("spark", "glimmer", "search", "rule")
MODELS = ("spark", "glimmer")
CRITERION = (
    "At k ≤ 4 on medium, Spark or Glimmer must beat the better of rule and search "
    "by ≥ 2 more passing maps, or ≥ 5 % fewer moves on mutual maps with no loss "
    "of passes; small within 5 % of the rule. No arm passing ≥ 4 medium maps = "
    "inconclusive. Search or rule tying or winning = fine-tuning not justified."
)
BASELINE_INTERPRETATION = (
    "Medium pass gain uses max(rule passes, search passes). The move branch "
    "requires ≥ 5 % fewer mean moves against EACH rule/search arm on its "
    "respective nonempty mutual maps, without loss against their maximum pass "
    "count. Small uses mean moves on nonempty mutual model/rule maps. All arms must reach "
    "the same checked budget on every frozen small and medium map."
)


def _total(values):
    # Decimal-string fractions preserve the exact 5% boundary for JSON numbers.
    return sum((Fraction(str(value)) for value in values), Fraction(0))


def _paired(model, baseline, *, threshold, positive_baseline=True):
    mutual = sorted(set(model) & set(baseline))
    model_total = _total(model[key] for key in mutual)
    baseline_total = _total(baseline[key] for key in mutual)
    return dict(
        mutual_maps=mutual, n=len(mutual),
        model_mean=float(model_total / len(mutual)) if mutual else None,
        baseline_mean=float(baseline_total / len(mutual)) if mutual else None,
        reduction_fraction=float(1 - model_total / baseline_total)
            if mutual and baseline_total else None,
        threshold_met=bool(mutual and (baseline_total > 0 or not positive_baseline)
                           and model_total <= threshold * baseline_total),
    )


def d185_prefix_verdict(k, groups, *, supported=True):
    """Assess one budget, with no ranking from missing/incomplete-map subsets."""
    result = dict(k=k, status="INCONCLUSIVE", llm_parked=True,
                  fine_tuning_justified=False, qualifying_models=[], reasons=[], models={})
    if k not in (1, 4):
        result["reasons"] = ["k is diagnostic only; D185 permits k ≤ 4"]
        return result
    if not supported:
        result["reasons"] = ["not all arms registered this checked-evaluation budget"]
        return result
    for size in ("medium", "small"):
        group = groups.get(size, {})
        arms = group.get("arms", {})
        if set(arms) != set(ARMS) or any(not arms[arm].get("maps") for arm in ARMS):
            result["reasons"] = [f"missing {size} cohort or arm"]
            return result
        if any(arms[arm].get("incomplete_maps")
               or arms[arm].get("completed_maps") != arms[arm]["maps"] for arm in ARMS):
            result["reasons"] = [f"incomplete {size} checked-evaluation budget"]
            return result
    medium = groups["medium"]["arms"]
    small = groups["small"]["arms"]
    maximum_passes = max(arm["passing_maps"] for arm in medium.values())
    result["maximum_medium_passes"] = maximum_passes
    if maximum_passes < 4:
        result["reasons"] = ["no arm passes at least 4 medium maps; LLM stays parked"]
        return result
    rule = medium["rule"]["passing_map_moves"]
    search = medium["search"]["passing_map_moves"]
    baseline_passes = max(len(rule), len(search))
    result["better_rule_search"] = dict(passing_maps=baseline_passes,
                                        rule_passes=len(rule), search_passes=len(search))
    for arm in MODELS:
        model = medium[arm]["passing_map_moves"]
        model_small = small[arm]["passing_map_moves"]
        rule_small = small["rule"]["passing_map_moves"]
        pass_gain = len(model) - baseline_passes
        medium_moves = {name: _paired(model, baseline, threshold=Fraction(95, 100))
                        for name, baseline in (("rule", rule), ("search", search))}
        small_moves = _paired(model_small, rule_small, threshold=Fraction(105, 100),
                              positive_baseline=False)
        medium_no_pass_loss = len(model) >= baseline_passes
        pass_branch = pass_gain >= 2
        move_branch = medium_no_pass_loss and all(pair["threshold_met"] for pair in medium_moves.values())
        small_ok = small_moves["threshold_met"]
        qualifies = (pass_branch or move_branch) and small_ok
        reasons = []
        if not (pass_branch or move_branch):
            reasons.append("neither ≥ 2 extra medium passes nor ≥ 5 % fewer mutual moves without pass loss")
        if not small_ok:
            reasons.append("small is outside 5 % of rule or has no mutual maps")
        result["models"][arm] = dict(
            qualifies=qualifies, medium_pass_gain=pass_gain,
            medium_no_pass_loss=medium_no_pass_loss, medium_pass_branch=pass_branch,
            medium_move_branch=move_branch, medium_moves=medium_moves,
            small_within_five_percent=small_ok,
            small_moves=small_moves, reasons=reasons,
        )
        if qualifies:
            result["qualifying_models"].append(arm)
    if result["qualifying_models"]:
        result.update(status="PASS", llm_parked=False, fine_tuning_justified=True,
                      reasons=["D185 medium improvement and small rule-parity requirements met"])
    else:
        result.update(status="NOT_JUSTIFIED", reasons=[
            "search or rule ties/wins, or model improvement misses D185; fine-tuning not justified"])
    return result


def d185_verdict(prefixes):
    """One eligible passing k suffices; k=16 can never justify fine-tuning."""
    assessed = {key: d185_prefix_verdict(int(key), value["sizes"],
                    supported=value["supported_by_all_budgets"])
                for key, value in prefixes.items()}
    eligible = [value for key, value in assessed.items() if int(key) in (1, 4)]
    status = ("PASS" if any(value["status"] == "PASS" for value in eligible)
              else "NOT_JUSTIFIED" if any(value["status"] == "NOT_JUSTIFIED" for value in eligible)
              else "INCONCLUSIVE")
    return dict(registration="D185", criterion=CRITERION,
                baseline_interpretation=BASELINE_INTERPRETATION,
                status=status, llm_parked=status != "PASS",
                fine_tuning_justified=status == "PASS", prefixes=assessed,
                qualifying_budgets=[int(key) for key, value in assessed.items()
                                    if value["status"] == "PASS"])
