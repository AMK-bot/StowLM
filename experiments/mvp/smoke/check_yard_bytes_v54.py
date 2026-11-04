"""Compare all OFF workload bytes with the retained pre-D72 simulator source.

No generator/model code, expected hashes or serialized fields are changed.
The reference uses the same current dependencies, isolating simulator edits.
"""
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from stowage.rules.catalogue import rule_freeze_v1
from stowage.yard import simulator


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def wire(calls, report):
    return {"calls": [{"id": call.id, "instance": call.instance.model_dump(mode="json"),
        "arrival_s": call.arrival_s, "cutoff_s": call.cutoff_s, "gate_in_times": call.gate_in_times,
        "service_s": call.service_s, "load_order": call.load_order} for call in calls], "report": asdict(report)}


def digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def main():
    reference_path = OUT / "v54-yard-pre-d72-simulator.py"
    reference = load("stowage.yard._v54_before_d72", reference_path)
    fixture = load("_v54_yard_fixture", ROOT / "tests/yard/test_contract_workload.py")
    expected = {41: "9885b8182dd2171541281246592ea4c964bc5d4e53a24ddd2c763454203a7563",
                2: "b9f9ee0e8ab5a9afb6fbc5af1e868080533b55b9e1ab4ef19e7407d7acc27d49"}
    rows = []
    for seed, old_digest in expected.items():
        began = time.monotonic()
        values = {}
        for label, module in (("current", simulator), ("pre-d72", reference)):
            with rule_freeze_v1(enabled=False):
                template = fixture._template()
                template.provenance.versions = {"fixture": "yard-legacy-v1"}
                calls, report = module.sample_ship_calls(template, fixture._parameters(), days=1, seed=seed)
            payload = wire(calls, report)
            (OUT / f"v54-yard-{label}-{seed}.json").write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
            values[label] = payload
        rows.append(dict(seed=seed, expected_sha256=old_digest,
                         current_sha256=digest(values["current"]), reference_sha256=digest(values["pre-d72"]),
                         full_payload_equal=values["current"] == values["pre-d72"],
                         elapsed_s=time.monotonic() - began))
    result = dict(reference_original_path="/tmp/w2-l3-yard-d72-before/simulator.py",
        reference_source_sha256=hashlib.sha256(reference_path.read_bytes()).hexdigest(),
        current_source_sha256=hashlib.sha256(Path(simulator.__file__).read_bytes()).hexdigest(), rows=rows,
        note="Same current dependencies for both sources; no field exclusions. Equal payloads localize old hash drift outside simulator changes. Exact changed model/parameter/cargo fields remain unverified without the original old payload.")
    (OUT / "v54-yard-byte-comparison.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
