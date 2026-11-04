"""Recheck an existing synthetic, unsealed showcase plan; no solver or paid calls."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time

from experiments.mvp.pipeline.contract.schema import sha256_json
from stowage.checker.full import check, departure_condition, report_matches_inputs
from stowage.io import load_instance
from stowage.model import MovePlan

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).with_name('fixtures') / 'development'
SOURCE_INSTANCE = 'experiments/mvp/corpus/showcase/feeder_500-small.json'
SOURCE_RESULT = ('experiments/mvp/runs/stowlm-showcase/instances/'
                 'random.small.29000022.0.7/result.json')


def wait_for_capacity():
    """Wait for the fleet admission rule; missing or stale readings never admit."""
    started = time.monotonic()
    while True:
        try:
            row = json.loads((ROOT / 'experiments/fleet/core-state.json').read_text())
            mask = int(row['mask'], 0) if isinstance(row['mask'], str) else int(row['mask'])
            age = time.time() - float(row['t'])
            if mask >= 0 and mask.bit_count() >= 8 and row['load1'] <= 45 and 0 <= age < 120:
                return dict(mask=mask, load1=row['load1'], reading_age_s=age,
                            waited_s=time.monotonic() - started)
        except (OSError, TypeError, ValueError, KeyError):
            pass
        time.sleep(2)


def main():
    instance = load_instance(ROOT / SOURCE_INSTANCE)
    raw = json.loads((ROOT / SOURCE_RESULT).read_text())
    plan = MovePlan.model_validate(raw['plan'])
    admission = wait_for_capacity()
    started = time.monotonic()
    report = check(instance, plan)
    wall_s = time.monotonic() - started
    assert report.passed, report.model_dump(mode='json')
    assert report_matches_inputs(report, instance, plan)
    assert all(part.ran for part in report.parts.values())
    assert not report.provenance.tainted
    state = departure_condition(instance, plan)
    aboard = sorted(p.container_id for p in state.placements)
    assert len(aboard) >= 10
    OUT.mkdir(exist_ok=True)
    for name, value in (('instance', instance), ('plan', plan), ('report', report)):
        (OUT / f'{name}.json').write_text(
            json.dumps(value.model_dump(mode='json'), ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    metadata = dict(
        synthetic=True, mode='stored',
        label='Synthetic ships and cargo, simulated port',
        source_instance=SOURCE_INSTANCE, source_result=SOURCE_RESULT,
        created_utc=datetime.now(timezone.utc).isoformat(),
        instance_sha256=sha256_json(instance.model_dump(mode='json')),
        plan_sha256=sha256_json(plan.model_dump(mode='json')),
        report_sha256=sha256_json(report.model_dump(mode='json')),
        checker_passed=report.passed, all_parts_ran=True, report_bound_to_inputs=True,
        checker_wall_s=wall_s, boxes_aboard=len(aboard), explanation_box_ids=aboard[:10],
        admission=admission, paid_calls=0, cost_usd=0.,
        notes='Existing synthetic showcase plan, freshly rechecked; no new optimisation.')
    (OUT / 'fixture.json').write_text(json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    print(json.dumps(metadata, sort_keys=True))


if __name__ == '__main__':
    main()
