"""D97 legacy-small comparison records; no solving or historical trust shortcut.

Call after a fresh same-instance two-step baseline and current full-checker +
complete production token-mask checks of the legacy plan. Compare the baseline
BEFORE LNS (``solve_gold.metrics['baseline_objective']``), never its improved
``objective_after``. The caller owns actual checks and artifact publication.
"""
from __future__ import annotations

import math
import re
from numbers import Real


def comparison_record(legacy_objective, baseline_objective, *,
                      legacy_checker_passed, legacy_mask_passed,
                      baseline_checker_passed, baseline_mask_passed,
                      input_sha256, legacy_input_sha256,
                      legacy_plan_sha256=None, baseline_plan_sha256=None,
                      baseline_version=None):
    """Return an auditable retain/drop decision from fresh measured evidence.

    Objectives must be checker/move-replay ``weighted_nonproductive`` counts
    under the same instance weights. Missing baseline evidence drops the legacy
    candidate as unverified; it is not evidence that its objective is worse.
    Gate booleans must come from this epoch, not archived result flags. A new
    warm-start plan can be published independently of this legacy decision.
    """
    gates = dict(legacy_checker=legacy_checker_passed is True,
                 legacy_mask=legacy_mask_passed is True,
                 baseline_checker=baseline_checker_passed is True,
                 baseline_mask=baseline_mask_passed is True)

    def valid_objective(value):
        return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value) and value >= 0

    measured_legacy = float(legacy_objective) if valid_objective(legacy_objective) else None
    measured_baseline = float(baseline_objective) if valid_objective(baseline_objective) else None
    same_input = (isinstance(input_sha256, str)
                  and re.fullmatch(r"[0-9a-f]{64}", input_sha256) is not None
                  and input_sha256 == legacy_input_sha256)
    reason = None
    if not same_input:
        reason = "legacy_quality_input_mismatch"
    elif not gates["baseline_checker"] or not gates["baseline_mask"] or measured_baseline is None:
        reason = "legacy_quality_baseline_unverified"
    elif not gates["legacy_checker"]:
        reason = "legacy_quality_checker"
    elif not gates["legacy_mask"]:
        reason = "legacy_quality_mask"
    elif measured_legacy is None:
        reason = "legacy_quality_objective_unavailable"
    elif measured_legacy > measured_baseline:
        reason = "legacy_quality_worse_than_twostep"
    return dict(format="stowlm-legacy-quality-d97-v1", retain_legacy=reason is None,
                decision="retain" if reason is None else "drop", drop_reason=reason,
                objective_definition="weighted_nonproductive",
                legacy_objective=measured_legacy, baseline_objective=measured_baseline,
                objective_delta=(measured_legacy-measured_baseline
                                 if measured_legacy is not None and measured_baseline is not None else None),
                gates=gates, same_input=same_input, input_sha256=input_sha256,
                legacy_input_sha256=legacy_input_sha256,
                legacy_plan_sha256=legacy_plan_sha256, baseline_plan_sha256=baseline_plan_sha256,
                baseline_version=baseline_version,
                baseline_scope="fresh same-instance two-step before LNS",
                publication_scope="legacy candidate only; new warmstart candidate is independent")
