"""Audit five unconditioned candidate load lists per rung against real-hull shards.

This contrasts the explicit non-DG starter service with the original cargo
profile. It is a necessary-condition experiment, not a feasibility estimate.
The retained production shards supply the exact vessel/port/tank inputs.
"""
from collections import Counter
import json
from pathlib import Path
from time import perf_counter

from stowage.generate.cargo import generate_load_list
from stowage.generate.instances import SIZE_LADDER
from stowage.generate.instances_screen import count_unresolved_provisions, screen_load_list
from stowage.io import load_instance


def main():
    root = Path(__file__).parent
    start = perf_counter()
    rows = {}
    for size, rung in SIZE_LADDER.items():
        reference = load_instance(root / size / 'seed-00000000.json')
        rejections = Counter()
        rejected = provisions = provision_boxes = 0
        dg_un = Counter()
        for seed in range(5):
            boxes = generate_load_list(None, 0, rung.boxes, seed=seed * 1_000_000)
            reasons = screen_load_list(reference.vessel, boxes, port=reference.port,
                                       tank_masses_kg=reference.onboard.tank_masses_kg)
            rejections.update(reasons)
            rejected += bool(reasons)
            count = count_unresolved_provisions(boxes)
            provisions += count > 0
            provision_boxes += count
            dg_un.update(box.dg.un_number for box in boxes if box.dg)
        rows[size] = {'candidates': 5, 'screened_out': rejected, 'screening_rate': rejected / 5,
                      'rejections': dict(sorted(rejections.items())),
                      'rates_by_reason': {k: v / 5 for k, v in sorted(rejections.items())},
                      'infeasible_by_provision_candidates': provisions,
                      'infeasible_by_provision_rate': provisions / 5,
                      'unresolved_provision_boxes': provision_boxes,
                      'dg_un_draws': dict(sorted(dg_un.items()))}
    result = {'cargo_profile': 'unconditioned feeder_export (including tiny)',
              'note': 'Five independent raw cargo candidates per rung; no simulator or departure plan. Rate denominator is candidates, not boxes.',
              'rungs': rows, 'wall_s': perf_counter() - start}
    (root / 'screening-probe.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
