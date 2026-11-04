"""Fixed reference stows, replayed through the lab's common finish comparison."""
from __future__ import annotations

import json
from pathlib import Path


def propose(instance, *, seed, budget_s, context):
    from stowage.actions import get_action_space
    from stowage.model import MovePlan
    raw = context.get('stored_plan')
    if raw is not None:
        plan = MovePlan.model_validate(raw) if isinstance(raw, dict) else raw
    else:
        # Only the fixed quick medium references are admitted; never construct
        # a candidate from sealed data or reuse a stored ballast certificate.
        from profile_baseline import ROOT, load_stored
        rows = json.loads((ROOT/'experiments/mvp/scale/runs/v6/run.json').read_text())['cohort']['maps']
        requested = Path(context['instance_path']).as_posix()
        row = next((row for row in rows if row['input'] == requested and row['split'] == 'train'), None)
        if row is None:
            context['method_metadata']['missing_reference'] = True
            return []
        _, result, _ = load_stored(row['map_id'])
        plan = result.plan
        context['method_metadata']['v6_reference'] = row['map_id']
    return [tuple(get_action_space('boxslot').from_plan(instance, plan))]
