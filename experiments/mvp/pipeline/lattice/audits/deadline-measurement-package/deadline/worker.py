"""One row, one session. The supervisor owns its hard stop and final verdict."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from ..measurement_lifecycle import durable_json
from .supervisor import ROOT, VALIDATED_BY


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', type=Path, required=True)
    args = parser.parse_args()
    request = args.request.resolve()
    job = json.loads(request.read_text())
    baseline = job.pop('baseline', None)
    directory = request.parent
    if hashlib.sha256((ROOT/job['map']).read_bytes()).hexdigest() != job['input_sha256']:
        raise ValueError('frozen map changed')
    from stowage.io import load_instance
    from experiments.mvp.reader.schema import validate_answer, context_from_instance
    from experiments.mvp.pipeline.binding import plan_with_rules
    from ..measure import profile, expected_identity
    instance = load_instance(ROOT/job['map'])
    if job['rule_type'] == 'baseline':
        job['canonical_rule'] = None
    else:
        from stowage.model import MovePlan
        from ..live_campaign import select_rule
        from experiments.mvp.reader.schema import answer_envelope
        from experiments.mvp.reader.pipeline import intrinsic_violations
        prior = MovePlan.model_validate(baseline['plan']) if baseline.get('passed') and baseline.get('plan') else None
        answer, selection = select_rule(instance, prior, job['rule_type'])
        errors = intrinsic_violations(instance, answer, prior) if prior is not None else None
        job.update(canonical_rule=answer_envelope(answer), selection=selection,
            comparison=dict(baseline_key=baseline['key'], baseline_passed=bool(baseline.get('passed')),
                binding_classification='unknown' if errors is None else ('binding' if errors else 'already_obeyed'),
                baseline_instruction_violations=errors))
    durable_json(directory/'selection.json', {key:job[key] for key in
        ('canonical_rule','selection','comparison') if key in job})
    answers = [] if job['canonical_rule'] is None else [validate_answer(
        json.dumps(job['canonical_rule']), context_from_instance(instance))]
    identity = expected_identity(job['cell'], job['map'], job['profile'], job['canonical_rule'], instance)
    budget, finish, config = profile(job['profile'])
    def incumbent(event):
        elapsed = time.monotonic()-job['started_monotonic']
        # The adapter has already freshly checked the original instance and
        # independently audited every confirmed rule before this callback.
        event = dict(event, engine_wall_s=event.get('wall_s'), wall_s=elapsed,
                     published_wall_s=elapsed, measurement_identity=identity,
                     validated_by=VALIDATED_BY, worker_pid=os.getpid())
        durable_json(directory/'incumbents'/(uuid.uuid4().hex+'.json'), event)
    result, evidence = plan_with_rules(instance, answers, budget_s=budget, finalize_s=finish,
        config=config, seed=0, on_incumbent=incumbent)
    plan = result.plan.model_dump(mode='json') if result is not None and result.plan is not None else None
    if evidence.get('passed') is True and plan is not None:
        incumbent(dict(evidence, plan=plan))
    row = dict(job, measurement_identity=identity, evidence=evidence,
        status=evidence['status'], passed=evidence.get('passed') is True,
        plan=plan, lifts=evidence.get('lifts'), lane=evidence.get('lane'),
        wall_s=time.monotonic()-job['started_monotonic'], finished_at=time.time(),
        first_checked_wall_s=evidence.get('first_checked_wall_s'), worker_pid=os.getpid())
    durable_json(directory/'result.json', row)


if __name__ == '__main__':
    main()
