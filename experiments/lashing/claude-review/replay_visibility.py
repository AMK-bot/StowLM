"""Public visibility replay of six frozen full/background-only stows."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'experiments/checker'))
from small_stow_common import background_only, cases, load_case, source_identity
from stowage.checker.visibility import check_visibility


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    args = parser.parse_args()
    out = Path(__file__).with_name(args.label)
    out.mkdir(exist_ok=True)
    started = time.monotonic()
    before = source_identity()
    (out/'source-start.json').write_text(json.dumps(before, indent=2)+'\n')
    rows = []
    for case in cases():
        instance, plan, full = load_case(case)
        unchanged = instance.model_dump_json(), plan.model_dump_json(), full.model_dump_json()
        reports = {}
        for name, state in [('full', full), ('background_only', background_only(full))]:
            report = check_visibility(instance, state, call_index=instance.call_index)
            assert report.ran, (case, name, report.error)
            reports[name] = report.model_dump(mode='json')
        assert unchanged == (instance.model_dump_json(), plan.model_dump_json(), full.model_dump_json())
        row = dict(case=case, reports=reports)
        (out/f'{case}.json').write_text(json.dumps(row, indent=2)+'\n')
        rows.append(row)
        print(json.dumps(dict(case=case, blind_m={n:r['metrics']['blind_distance_m']
            for n,r in reports.items()}, violations={n:len(r['violations']) for n,r in reports.items()})), flush=True)
    after = source_identity()
    result = dict(rows=rows, wall_s=time.monotonic()-started, source_before=before,
                  source_after=after, source_stable=before == after, original_inputs_unchanged=True,
                  diagnostic_only='Background-only states omit required cargo; no plan approval.')
    (out/'summary.json').write_text(json.dumps(result, indent=2)+'\n')
    assert before == after, 'source changed during replay'


if __name__ == '__main__':
    main()
