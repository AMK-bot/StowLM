"""Replay exactly the six frozen background-only conditions, without refitting."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'experiments/checker'))
from small_stow_common import background_only, cases, load_case, source_identity
from stowage.checker.lashing import check_lashing


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    before = source_identity()
    rows = []
    for case in cases():
        instance, plan, full = load_case(case)
        original = instance.model_dump_json(), plan.model_dump_json(), full.model_dump_json()
        ship = background_only(full)
        report = check_lashing(instance, ship, call_index=instance.call_index)
        assert report.ran, (case, report.error)
        row = dict(case=case, gm_m=report.metrics['gm_m'],
                   guide_ratio=report.metrics['max_cell_guide_ratio'],
                   report=report.model_dump(mode='json'))
        assert original == (instance.model_dump_json(), plan.model_dump_json(), full.model_dump_json())
        (args.out / f'{case}.json').write_text(json.dumps(row, indent=2)+'\n')
        rows.append(row)
        print(json.dumps({k: row[k] for k in ('case', 'gm_m', 'guide_ratio')}), flush=True)
    result = dict(rows=rows, wall_s=time.monotonic()-started, source_before=before,
                  source_after=source_identity(), diagnostic_only=True,
                  note='Required decision cargo omitted; these are not full departure certificates.')
    (args.out / 'summary.json').write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__':
    main()
