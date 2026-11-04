"""Unchanged K1b compaction, V4 600 s, full checker and mask replay; D206 admission."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

from rule import HERE, ROOT, WEIGHTS_SHA256
import compact
import judge

spec = importlib.util.spec_from_file_location('d206_admission', HERE.parent/'k1b/skill-check/admission.py')
admission = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admission)
sys.path.insert(0, str(ROOT/'experiments/mvp/frontier'))
from sight import sight_clear


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')
    tmp.replace(path)


def prepare(inst, plan):
    compacted, info = compact.compact_plan(inst, plan)
    better = compacted is not None and info['compact_raw']['total'] < info['before']['total']
    chosen = compacted if better else plan
    gate, details = sight_clear(inst, chosen, {})
    return chosen, dict(compacted=better, cost=info['compact_raw']['total'] if better else info['before']['total'],
                        original_cost=info['before']['total'], gate_clear=gate, gate_rules=details['rules'], compaction=info)


def wait_admission(out):
    out = Path(out)
    while True:
        snapshot = admission.probe(ROOT/'experiments/fleet/core-state.json')
        with (out/'admission.jsonl').open('a') as stream:
            stream.write(json.dumps(snapshot, sort_keys=True)+'\n')
        if snapshot['admitted']:
            checked = admission.validate_snapshot(snapshot)
            if checked['admitted']:
                return checked
        write(out/'status.json', dict(status='waiting_admission', probe=snapshot))
        time.sleep(15)


def evaluate(inp, plan, out_dir):
    from stowage.io import load_instance
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    inp = Path(inp).resolve()
    rel = inp.relative_to(ROOT).as_posix()
    allowed = rel.startswith('experiments/mvp/corpus/val/')
    if not allowed:
        manifest = json.loads((HERE/'scale/manifest.json').read_text())
        allowed = any(j['input'] == rel and j['input_sha256'] == sha(inp) for j in manifest['inputs'])
    if not allowed or any('test' in p for p in inp.parts):
        raise ValueError('only small validation or the fixed eighteen K2 TRAIN ladder inputs')
    inst = load_instance(inp)
    binding = dict(input=rel, input_sha256=sha(inp), weights_sha256=WEIGHTS_SHA256,
                   plan_sha256=hashlib.sha256(plan.model_dump_json().encode()).hexdigest() if plan else None,
                   code={str(p.relative_to(ROOT)): sha(p) for p in
                         (HERE/'rule.py', HERE/'evaluate.py', HERE/'frozen-source/rf.py', ROOT/'src/llm/stowlm/backtrack.py', Path(compact.__file__), Path(judge.__file__))},
                   finisher_s=600, **compact.run_identity())
    if (out/'result.json').exists():
        row = json.loads((out/'result.json').read_text())
        if row['binding'] != binding:
            raise RuntimeError('existing evaluation binding changed')
        for name, digest in row.get('artifacts', {}).items():
            if sha(out/name) != digest:
                raise RuntimeError('evaluation artifact changed')
        return row
    row = dict(instance_id=inst.id, binding=binding, complete=plan is not None,
               passed=False, moves=None, gate_clear=False, gate_rules=[], timed_out=False)
    started = time.perf_counter()
    row['admission_wait_s'] = 0
    if plan is not None:
        write(out/'raw-plan.json', json.loads(plan.model_dump_json()))
        chosen, info = prepare(inst, plan)
        row.update(info)
        write(out/'chosen-plan.json', json.loads(chosen.model_dump_json()))
        waiting = time.perf_counter()
        row['admission'] = wait_admission(out)
        row['admission_wait_s'] = round(time.perf_counter()-waiting, 3)
        check, finished, report = compact.finish_and_check(inst, chosen, budget_s=600)
        if check.get('passed'):
            compact.admit_mask(check, compact.mask_ok(inst, finished.plan))
        row.update(check)
        artifacts = {}
        if finished is not None:
            write(out/'finished.json', json.loads(finished.model_dump_json()))
            artifacts['finished.json'] = sha(out/'finished.json')
        if report is not None:
            write(out/'report.json', json.loads(report.model_dump_json()))
            artifacts['report.json'] = sha(out/'report.json')
        row['artifacts'] = artifacts
    row['wall_s'] = round(time.perf_counter()-started, 3)
    row['compute_seconds'] = round(row['wall_s']-row['admission_wait_s'], 3)
    row['outcome'] = 'pass' if row['passed'] else ('dead end' if plan is None else 'finisher or checker fail')
    write(out/'result.json', row)
    return row
