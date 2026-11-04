"""Explicit preparation only: fresh-check demo plans and store Spark sidecars.

Example (requires separately authorized Spark credit; never run by the page)::

    uv run python -m app.prefetch_explanations --instance demo/instance.json \
        --plan demo/scalable.plan.json --plan demo/baseline.plan.json

The normal Spark credit/spend gates remain active. Rejected responses are saved
with their failed validator verdict; the app uses its deterministic template.
"""
from __future__ import annotations

import argparse
import json

from app.inputs import load_inputs
from app.stored_explanations import explanation_sidecar_path, prefetch_explanation
from llm.spark.explain import PlanExplainer
from stowage.checker import check, report_matches_inputs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance', required=True, help='Complete synthetic demo instance')
    parser.add_argument('--plan', required=True, action='append', help='Saved MovePlan/PlannerResult; repeat for each demo plan')
    args = parser.parse_args(argv)
    rejected = 0
    for path in args.plan:
        loaded = load_inputs(instance_path=args.instance, plan_path=path)
        if loaded.plan is None:
            raise ValueError(f'Demo artifact has no plan: {path}')
        report = check(loaded.instance, loaded.plan)
        if not report_matches_inputs(report, loaded.instance, loaded.plan):
            raise ValueError(f'Fresh checker report is not bound to the selected instance and plan: {path}')
        output = explanation_sidecar_path(path)
        artifact = prefetch_explanation(loaded.instance, loaded.plan, report,
            explainer=PlanExplainer(), output_path=output)
        accepted = artifact['validator']['accepted']
        rejected += not accepted
        print(json.dumps({'plan': str(path), 'sidecar': str(output), 'validator': artifact['validator']}))
    return 1 if rejected else 0


if __name__ == '__main__':
    raise SystemExit(main())
