"""Exact-learning partial-check adapters for the unchanged C1 scout ladder."""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
for path in (LAB / 'c1/leader', HERE):
    sys.path.insert(0, str(path))


def _run(instance, seed, budget_s, context, *, accelerated):
    from e9fix import install
    import ladder_kernel
    from screen import ladder_verdict
    install()
    context['method_metadata']['helper_sha256'] = {
        name: hashlib.sha256((HERE/name).read_bytes()).hexdigest()
        for name in ('screen.py', 'ladder_kernel.py')}
    original = ladder_kernel.verdict
    checks = []

    def observed(instance, plan):
        began = monotonic()
        verdict = ladder_verdict(instance, plan) if accelerated else original(instance, plan)
        elapsed = monotonic()-began
        record = dict(index=len(checks), seconds=elapsed, verdict=verdict,
                      plan=plan.model_dump(mode='json'))
        target = HERE/'results/ladder-transcripts'/('partial' if accelerated else 'reference')/instance.id
        target.mkdir(parents=True, exist_ok=True)
        (target/f'{len(checks):02d}.json').write_text(json.dumps(record, indent=2)+'\n')
        checks.append(dict(seconds=elapsed, ok=verdict[0], rules=verdict[1], bow_down=verdict[2]))
        return verdict

    ladder_kernel.verdict = observed
    try:
        candidates = ladder_kernel.run(instance, seed=seed, budget_s=budget_s, context=context)
        context['method_metadata']['partial_learning'] = accelerated
        context['method_metadata']['verdicts'] = checks
        return candidates
    finally:
        ladder_kernel.verdict = original


def propose(instance, *, seed, budget_s, context):
    return _run(instance, seed, budget_s, context, accelerated=True)


def reference(instance, *, seed, budget_s, context):
    return _run(instance, seed, budget_s, context, accelerated=False)


def finish(instance, plan, *, seed, finalize_s, label, context):
    from ladder_kernel import finish as original_finish
    return original_finish(instance, plan, seed=seed, finalize_s=finalize_s,
                           label=label, context=context)
