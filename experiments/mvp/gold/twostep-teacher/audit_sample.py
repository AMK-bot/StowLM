"""Blocking audit: a seeded random sample of best-d151/ winners through the unmodified verify_candidate path (a fresh
checker process per plan, epoch-bound). Writes audit-sample.json, then exits 1 unless every winner passes at its recorded
cost with this run's provenance. Usage: audit_sample.py [n=30] [seed=150] [workers=3]
"""
from llm.exit import outcome_epoch

from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import random
import sys

EPOCH = outcome_epoch.process_snapshot()
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def verdict(rows, n):
    """Disagreements among n audited winners; too few rows is itself a failure."""
    bad = [r for r in rows if r['passed'] is not True or r['moves'] != r['recorded'] or r.get('provenance_ok') is not True]
    if len(rows) != n:
        bad.append(dict(case='population', error=f'{len(rows)} audited, {n} required'))
    return bad


def audit_one(path):
    from llm.exit.selection import Candidate, verify_candidate
    from stowage.io import load_instance
    from stowage.model import MovePlan
    import d151
    record, manifest = d151.read(path), d151.read(HERE/'d151-manifest.json')
    given = record['provenance']
    raw = ROOT/record['instance_path']
    instance = load_instance(raw)
    ok = (given['uuid'] == manifest['uuid'] and given['outcome_epoch'] == manifest['outcome_epoch']
          and given['outcome_epoch'] == outcome_epoch.epoch_id(EPOCH) and given['input_sha256'] == d151.sha(raw))
    checked = verify_candidate(instance, Candidate(MovePlan.model_validate(record['plan']), given['source'], given['source']),
                               ballast_s=120., expected_outcome_epoch=EPOCH)
    return dict(case=given['case'], source=given['source'], recorded=given['costs'][given['source']], passed=checked.passed,
                moves=checked.weighted_moves, provenance_ok=ok, error=checked.details.get('error'))


def main():
    n, seed = int(sys.argv[1]) if len(sys.argv) > 1 else 30, int(sys.argv[2]) if len(sys.argv) > 2 else 150
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    paths = sorted((HERE/'best-d151').glob('*/record.json'))
    if len(paths) < n:
        raise SystemExit(f'only {len(paths)} winners, {n} required')
    with ProcessPoolExecutor(workers) as pool:
        rows = list(pool.map(audit_one, random.Random(seed).sample(paths, n)))
    bad = verdict(rows, n)
    (HERE/'audit-sample.json').write_text(json.dumps(dict(n=n, seed=seed, agree=n-len(bad), bad=bad, rows=rows), indent=1)+'\n')
    print(n-len(bad), '/', n, flush=True)
    if bad:
        raise SystemExit(f'audit disagreement: {bad[:3]}')


if __name__ == '__main__':
    main()
