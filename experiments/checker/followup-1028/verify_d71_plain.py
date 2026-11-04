"""Uninstrumented public-check control for a fixed D71 diagnostic pair."""
import argparse
import json
import os
from pathlib import Path
from time import perf_counter

from stowage.checker.full import check
from stowage.io import load_instance, load_plan
from stowage.rules.catalogue import rule_freeze_v1

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('directory', type=Path)
args = parser.parse_args()
assert os.environ.get('STOWAGE_STABILITY_TIME_BUDGET_S') is None
directory = args.directory
instance, plan = load_instance(directory/'instance.json'), load_plan(directory/'plan.json')
expected = json.loads((directory/'report-0.json').read_text())
rows = []
with rule_freeze_v1():
    for sample in range(2):
        started = perf_counter()
        report = check(instance, plan)
        result = report.model_dump(mode='json')
        row = dict(sample=sample, seconds=perf_counter()-started, passed=report.passed,
                   identical_to_instrumented_report=result == expected)
        rows.append(row)
        (directory/'plain-control.json').write_text(json.dumps(rows, indent=2)+'\n')
        print(json.dumps(row), flush=True)
        assert report.passed and row['identical_to_instrumented_report']
