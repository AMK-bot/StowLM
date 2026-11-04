"""Pair the elementary geometric bound with a fresh scorecard passing witness."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(HERE), str(HERE.parents[1])]


def certify(row_path):
    from catalog import ensure_safe
    from stowage.io import load_instance
    from experiments.mvp.strategy.headroom.layout import BlockModel
    from theory import elementary_reefer_certificate
    row = json.loads(Path(row_path).read_text())
    path = ROOT / ensure_safe(row['input'])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row['input_sha256']
    assert row['passed'] is True and row['plan'] is not None
    instance = load_instance(path)
    bound = elementary_reefer_certificate(instance)
    assert bound is not None and row['lifts'] == bound['weighted_lifts_lower_bound']
    blocks = BlockModel.from_instance(instance)
    boxes = {b.id: b for b in instance.containers}
    port_sets = {}
    cover_sets = {}
    for box, slot in row['assignment'].items():
        for block in blocks.blocks_of_slot(slot):
            port_sets.setdefault(block, set()).add(str(boxes[box].pod))
            cover_sets.setdefault(block.rsplit('/', 1)[0], set()).add(str(boxes[box].pod))
    return dict(instance_id=instance.id, input=row['input'],
                input_sha256=row['input_sha256'], lower_bound=bound,
                attained_lifts=row['lifts'], verdict='D11 optimum attained',
                fresh_full_checker_row=str(row_path),
                fresh_row_sha256=hashlib.sha256(Path(row_path).read_bytes()).hexdigest(),
                ports_by_block={k: sorted(v) for k, v in sorted(port_sets.items())},
                ports_by_cover={k: sorted(v) for k, v in sorted(cover_sets.items())},
                one_port_per_used_block=all(len(v) == 1 for v in port_sets.values()),
                one_port_per_used_cover=all(len(v) == 1 for v in cover_sets.values()))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('row')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    Path(args.out).write_text(json.dumps(certify(args.row), indent=2, sort_keys=True) + '\n')
