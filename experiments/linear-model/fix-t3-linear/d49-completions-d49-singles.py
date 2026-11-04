"""Finish only unmeasured D49 sample requests with independent epoch pins.

These are rechecked saved requests, not additional generator draws. Results
retain their own source pins and cannot certify a different campaign epoch.
"""
import importlib.util
import json
from pathlib import Path
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("completions", HERE/"d49_completions.py")
completion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(completion)
helper = completion.helper()
from stowage.io import load_instance

sample = HERE/"d49-completions-d49-stable-sample"
output = HERE/"d49-completions-d49-singles"
output.mkdir(exist_ok=True)
for seed in (100000005, 100000009):
    label = f"tiny-{seed}"
    path = output/f"completion-{label}.json"
    if path.exists():
        raise ValueError("Retain existing single-request evidence; do not retag it")
    request_path = sample/f"request-{label}.json"
    request = json.loads(request_path.read_text())
    assert request["accepted"] and request["c2_screened"]
    row = next(r for r in request["completion_calls"] if not r["singleton_dg_probe"] and r["c2_status"] == "passed")
    instance_path = sample/f"{row['label']}-diagnostic-instance.json"
    before = helper.snapshot()
    instance = load_instance(instance_path)
    with helper.necessary_direction():
        failures, screened = helper.check(instance, output=output, label=label+"-recheck")
    if failures or screened["c2_status"] != "passed":
        raise ValueError("Saved request no longer passes current necessary screen")
    record = completion.audit_request(instance, output, label, attempts=4, seconds=15.)
    after = helper.snapshot()
    if after != before:
        raise ValueError("Single-request epoch changed; retain diagnostics, do not publish completion")
    record.update(size="tiny", seed=seed, inputs_start=before, inputs_end=after,
                  input_instance_sha256=helper.sha(instance_path),
                  original_request_sha256=helper.sha(request_path),
                  original_sample_summary_sha256=helper.sha(sample/"summary.json"),
                  current_necessary_screen=screened,
                  scope="single saved request rechecked under these own pins; no combined current-epoch campaign claim")
    completion.write(path, record)
    print(json.dumps({"request": label, "seconds": record["elapsed_s"],
                      "checks": record["full_checker_calls"], "found": record["exact_completion_found"]}), flush=True)
