"""Fresh integration gate of a recorded public C6 witness, never a search start."""
import json
from datetime import datetime, timezone
from pathlib import Path

from stowage.io import load_instance
from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from .batch import write_json
from .gates import gate_plan

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def main():
    source = ROOT/'experiments/mvp/pipeline/binding/fixtures/medium_one_port_per_hatch_worker.json'
    recorded = json.loads(source.read_text())
    measured_path = ROOT/recorded['baseline']['source_measurement']
    if sha256_file(measured_path) != recorded['baseline']['source_measurement_sha256']:
        raise ValueError('recorded witness measurement provenance changed')
    measured = json.loads(measured_path.read_text())
    instance = load_instance(ROOT/recorded['map'])
    plan = recorded['baseline']['plan']
    audit = gate_plan(instance, plan, [])
    row = dict(source=str(source.relative_to(ROOT)), source_sha256=sha256_file(source),
               note='Recorded public C6 development witness; fresh integration gate, no search. '
                    'Historical live profile; not a standard-budget hero measurement.',
               instance_sha256=sha256_json(instance.model_dump(mode='json')),
               computed_utc=datetime.fromtimestamp(measured['finished_at'], timezone.utc).isoformat(),
               rechecked_utc=datetime.now(timezone.utc).isoformat(), gate=audit)
    write_json(HERE/'fixture-verification.json', row)
    if audit['passed']:
        row.update(instance=instance.model_dump(mode='json'), plan=plan, canonical_rules=[],
                   mode='stored', label='stored', status='checked_plan',
                   engine_version=audit['engine_identity']['engine_version'],
                   engine_manifest_sha256=audit['engine_identity']['engine_manifest_sha256'])
        write_json(HERE/'fixtures/recorded-c6-integration.json', row)
    print(json.dumps(dict(passed=audit['passed'], lifts=audit['lifts'],
                          gauge_bad_steps=audit['gauges']['outside_limits_steps'],
                          gauge_unavailable=audit['gauges']['unavailable_steps'])), flush=True)


if __name__ == '__main__':
    main()
