"""Cheap two-seed workload serialization diagnostic; no physics or solver calls.

Repository-owned historical source snapshots may be imported in a fresh process
to isolate input/model drift. This never rewrites those snapshots or test pins.
"""
import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from time import perf_counter


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--parameter-root", type=Path)
    parser.add_argument("--legacy-input-source-epoch", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    if args.source_root:
        sys.path.insert(0, str(args.source_root.resolve() / "src"))
    import stowage.params as parameters
    if args.parameter_root:
        parameter_root = args.parameter_root.resolve()
        parameters.repo_root = lambda: parameter_root
    path = root / "tests/yard/test_contract_workload.py"
    spec = importlib.util.spec_from_file_location("workload_fixture", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    from stowage.rules.catalogue import rule_freeze_v1
    import stowage.model.fixtures as model_fixture
    import stowage.yard.simulator as simulator
    rows = []
    for seed in (41, 2):
        started = perf_counter()
        with rule_freeze_v1(enabled=False):
            template = fixture._template()
            template.provenance.versions = {"fixture": "yard-legacy-v1"}
            if args.legacy_input_source_epoch:
                template.port.sources = ["synthetic fixture; no actual terminal operating limits"]
            calls, report = fixture.sample_ship_calls(template, fixture._parameters(), days=1, seed=seed)
        payload = dict(calls=[dict(id=call.id, instance=call.instance.model_dump(mode="json"),
            arrival_s=call.arrival_s, cutoff_s=call.cutoff_s,
            gate_in_times=call.gate_in_times, service_s=call.service_s,
            load_order=call.load_order) for call in calls], report=asdict(report))
        rows.append(dict(seed=seed, sha256=digest(payload), payload=payload,
            elapsed_s=perf_counter()-started))
    record = dict(source_root=str(args.source_root or root),
        parameter_root=str(args.parameter_root or root),
        legacy_input_source_epoch=args.legacy_input_source_epoch,
        fixture_file=model_fixture.__file__, simulator_file=simulator.__file__, rows=rows)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps({**{k: v for k, v in record.items() if k != "rows"},
        "rows": [{k: v for k, v in row.items() if k != "payload"} for row in rows]}))


if __name__ == "__main__":
    main()
