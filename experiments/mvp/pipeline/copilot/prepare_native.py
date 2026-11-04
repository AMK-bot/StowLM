"""Prepare openly labelled native evidence, not a model or sealed evaluation."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from time import perf_counter

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from .native_runtime import native_caches

HERE = Path(__file__).resolve().parent
OUT = HERE / 'fixtures' / 'agency-native'


def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2) + '\n')


def prepare():
    from stowage.model import Instance, MovePlan
    from stowage.moves import apply_plan
    from stowage.checker import check
    from stowage.frozen import active_stubs, require_clean
    from experiments.mvp.pipeline.binding.admission import wait_for_capacity
    from experiments.mvp.pipeline.lattice.gates import gate_plan
    from experiments.mvp.pipeline.lattice import save_entry

    OUT.mkdir(parents=True, exist_ok=True)
    write('process.json', dict(pid=os.getpid(), started_utc=datetime.now(timezone.utc).isoformat()))
    source = HERE.parent / 'explain' / 'fixtures'
    instance_raw = json.loads((source / 'instance.json').read_text())
    baseline_raw = json.loads((source / 'plan.json').read_text())
    instance = Instance.model_validate(instance_raw)
    baseline = MovePlan.model_validate(baseline_raw)
    # Two extra, legal yard relocations return to exactly the original position.
    # This is a deliberately inefficient constructed candidate, not an optimum.
    first = baseline_raw['moves'][0]
    assert first['kind'] == 'relocate'
    back = dict(first, src=first['dst'], dst=first['src'])
    costly_raw = dict(baseline_raw, id=baseline_raw['id'] + '.extra-yard-pair',
                      moves=[first, back, first, *baseline_raw['moves'][1:]], actions=None, action_space=None)
    costly = MovePlan.model_validate(costly_raw)
    assert apply_plan(instance, costly).legal
    write('instance.json', instance_raw)
    write('baseline-plan.json', baseline_raw)
    write('costly-plan.json', costly_raw)
    write('source.json', dict(label='Synthetic thirteen-box development fixture; not the lattice hero.',
          source_instance_sha256=sha256_file(source / 'instance.json'),
          source_plan_sha256=sha256_file(source / 'plan.json'),
          modification='Costly candidate adds a redundant legal yard relocation out-and-back pair; no rule or physics changed.'))
    rows = []
    with native_caches():
        for name, plan, cap in [('baseline', baseline, 2), ('costly', costly, 3)]:
            existing = OUT / (name + '-report.json')
            if existing.exists():
                report = json.loads(existing.read_text())
            else:
                admission = wait_for_capacity()
                print(json.dumps(dict(phase='native_checker', candidate=name, admission=admission)), flush=True)
                started = perf_counter()
                report_model = check(instance, plan)
                assert not active_stubs()
                require_clean(instance, plan, report_model, what='copilot agency native preparation')
                report = report_model.model_dump(mode='json')
                write(name + '-report.json', report)
                write(name + '-timing.json', dict(wall_s=perf_counter()-started, admission=admission,
                    checker='stowage.checker.check', all_parts_ran=all(p.ran and not p.error for p in report_model.parts.values())))
            print(json.dumps(dict(phase='checker_done', candidate=name, passed=report['passed'])), flush=True)
            rules = [dict(schema_version='stowlm.reader/2', constraint=dict(type='max_tiers', bay=None, zone='deck', max_tiers=cap))]
            gate_file = OUT / (name + '-lattice-gate.json')
            if gate_file.exists():
                gate = json.loads(gate_file.read_text())
            else:
                gate = gate_plan(instance, plan, rules)
                write(name + '-lattice-gate.json', gate)
            row = dict(name=name, checker_passed=report['passed'], lattice_passed=gate['passed'],
                       rules=rules, plan_sha256=sha256_json(plan.model_dump(mode='json')))
            if gate['passed']:
                identity = gate['engine_identity']
                save_entry(dict(instance=instance_raw, instance_sha256=sha256_json(instance_raw),
                    plan=plan.model_dump(mode='json'), canonical_rules=rules, status='checked_plan',
                    computed_utc=datetime.now(timezone.utc).isoformat(),
                    engine_version=identity['engine_version'], engine_manifest_sha256=identity['engine_manifest_sha256'],
                    synthetic=True, gate=gate), store_dir=OUT / 'store')
            rows.append(row)
            write('summary.json', dict(label='Native fixture preparation; not model performance.', rows=rows))
    return rows


if __name__ == '__main__':
    print(json.dumps(prepare(), indent=2))
