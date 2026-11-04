"""Freshwater qualification gates, using the campaign's shared CPU admission.

No phase starts automatically. Candidate gates inspect fresh observations;
ordinary public-model replay and manifest binding require promotion first.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path

from ordinary_campaign import run

HERE = Path(__file__).resolve().parent
SHIPS = ('feeder_500', 'feeder_1000', 'feeder_1700')
LEVELS = ('light', 'medium', 'full')


def preservation(candidate_path, baseline_path):
    from profile_run import adapter_pin
    before = json.loads(baseline_path.read_text())
    candidate = json.loads(candidate_path.read_text())
    if not set(before['entries']) <= set(candidate['entries']):
        raise ValueError('candidate removed an existing response')
    for key, record in before['entries'].items():
        if candidate['entries'].get(key) != record:
            raise ValueError('candidate modifies an existing response entry: '+key)
    new = {json.loads((HERE/'ordinary'/ship/'raw.json').read_text())['diagnostics']['response_calibration_identity']
           for ship in SHIPS}
    if len(new) != 3 or not new <= set(candidate['entries']):
        raise ValueError('candidate lacks the three independently measured freshwater responses')
    for key in new:
        record = candidate['entries'][key]
        if record['boundary_list']['water_density_kg_m3'] != 1000.:
            raise ValueError('fresh response has a non-freshwater boundary certificate')
        for evidence in ('evidence', 'conservative_evidence', 'necessary_evidence'):
            if adapter_pin() not in record[evidence]:
                raise ValueError('fresh response lacks mandatory adapter pin in '+evidence)
    report = dict(passed=True, old_entries_unchanged=len(before['entries']), additional_responses=sorted(new),
        baseline_bank=dict(path=str(baseline_path),
            sha256=hashlib.sha256(baseline_path.read_bytes()).hexdigest()),
        candidate_bank=dict(path=str(candidate_path),
            sha256=hashlib.sha256(candidate_path.read_bytes()).hexdigest()))
    (HERE/'candidate-preservation.json').write_text(json.dumps(report, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('candidate', 'ordinary', 'manifest'), required=True)
    parser.add_argument('--bank', type=Path)
    parser.add_argument('--baseline-bank', type=Path, default=HERE/'pre-extension-bank.json')
    parser.add_argument('--workers', type=int, default=1, choices=(1, 2, 3))
    parser.add_argument('--additional-fresh-population', type=Path, action='append', default=[],
                        help='explicit newly measured density1000 edge-schema diagnostic to gate')
    args = parser.parse_args()
    if args.phase == 'candidate':
        if args.bank is None:
            parser.error('candidate phase requires --bank')
        preservation(args.bank, args.baseline_bank)
        required = [HERE/"diagnostic-edges"/(name+".json") for name in
            ("claude-edge-cause", "claude-face-pilot", "claude-face-budget-pilot")]
        if not all(path.exists() for path in required):
            raise ValueError("all prescribed fresh diagnostic captures must finish before candidate gates")
        populations = [*required, *args.additional_fresh_population]
        extra = [value for path in populations
                 for value in ('--additional-fresh-population', path)]
        run('candidate-residuals', [HERE/'freshwater_residual_gate.py', '--bank', args.bank,
            '--output', HERE/'candidate-residual-gate.json', *extra])
        run('candidate-rows', [HERE/'claude_edge_audit.py', '--bank', args.bank,
            '--output', HERE/'candidate-row-audit.json', *extra])
    elif args.phase == 'ordinary':
        common = [HERE/'d49_holdout_audit.py', '--input-root', HERE/'ordinary',
                  '--output', HERE/'holdout-audit']
        # These queued wrappers all share acquire_slot with raw/edge/diagnostic
        # workers. --workers can never override compute-capacity.json.
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(run, 'ordinary-replay-'+ship+'-'+level,
                [*common, '--phase', 'replay', '--ship', ship, '--level', level])
                for ship in SHIPS for level in LEVELS]
            for future in as_completed(futures):
                future.result()
        run('ordinary-rows', [*common, '--phase', 'rows'])
        run('ordinary-summary', [*common, '--phase', 'summary'])
    else:
        run('audit-inputs', [HERE/'claude_audit_inputs.py'])


if __name__ == '__main__':
    main()
