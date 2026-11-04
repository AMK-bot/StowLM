"""Freeze explicit local sources and audit their hashes without importing solvers."""
from pathlib import Path
import argparse
import datetime
import hashlib
import json

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
MANIFEST = HERE/'assignment-frozen-hashes.json'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def covered():
    files = list(HERE.glob('*.py'))
    for folder in ('engine_snapshot', 'assignment_vendor'):
        files.extend((HERE/folder).rglob('*.py'))
    return {str(path.relative_to(HERE)): sha(path) for path in sorted(set(files))}

def policies():
    files = [ROOT/'experiments/mvp/frontier/lab/c5/leader/tiers.toml',
             ROOT/'src/stowage/rules/catalogue.toml']
    files.extend((ROOT/'src/stowage/checker').rglob('*.py'))
    return {str(path.relative_to(ROOT)): sha(path) for path in sorted(files)}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.check:
        data = json.loads(MANIFEST.read_text())
        current = covered()
        drift = [name for name in sorted(set(current)|set(data['files']))
                 if current.get(name) != data['files'].get(name)]
        external = policies()
        drift.extend(name for name in sorted(set(external)|set(data['checker_and_policy_files']))
                     if external.get(name) != data['checker_and_policy_files'].get(name))
        print(json.dumps({'manifest_sha256': sha(MANIFEST), 'sources': len(current),
                          'drift': drift}, indent=2))
        if drift:
            raise SystemExit(1)
        return
    data = dict(contract='D242-assignment', frozen_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        files=covered(), checker_and_policy_files=policies(),
        original_engine_snapshot_manifest_sha256=sha(HERE/'snapshot-hashes.json'),
        assignment_start_manifest_sha256=sha(HERE/'assignment-start-hashes.json'),
        work=dict(proposal_s_per_level=240, finish_s_per_relaxed_level=120,
            ordinary_finish_tries=2, ordinary_finish_s_per_try=120,
            relaxed_outer_rounds=5, lp_dive_nodes=80, retreats=6,
            integer_tail_attempts=6, integer_conflicts_per_goal=100000,
            integer_deterministic_s_per_goal=3, tail_wall_s=15,
            finishing_candidates=2, ballast_evaluations_per_stow=12,
            ballast_iterations_per_stow=2, identical_full_plan_including_ballast_finished_once=True),
        development_inputs=(HERE/'development6.txt').read_text().splitlines(),
        scope='Local copied sources only. No runtime plan, assignment, recipe or ballast from an earlier run.')
    MANIFEST.write_text(json.dumps(data, indent=2, sort_keys=True)+'\n')
    print(json.dumps({'manifest_sha256': sha(MANIFEST), 'sources': len(data['files'])}))

if __name__ == '__main__':
    main()
