"""Run rcspp_plan on suite instances and judge every plan with check_rcspp, which imports nothing from the planner.

Usage: run_rcspp.py <suite_dir> <out.jsonl> <names...> [--budget 240] [--passes 3] [--workers 4] [--sides 1] [--lash-changed-only] [--mixed N]
--mixed overrides the instance's limit on mixed bays, to test how much a generous limit is worth.
Rows hold numbers only; the plans themselves are derived from unlicensed data and go to --plans, outside git.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_rcspp import Readings, evaluate
from rcspp_io import read_instance, read_result, read_vessel, vessel_path, without_idle_ports
from rcspp_plan import full_positions, plan

HERE = Path(__file__).resolve().parent
SOURCE = hashlib.sha256(b''.join((HERE/f).read_bytes() for f in ('rcspp_io.py', 'check_rcspp.py', 'rcspp_plan.py'))).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('suite')
    parser.add_argument('out')
    parser.add_argument('names', nargs='+')
    parser.add_argument('--budget', type=float, default=240.)
    parser.add_argument('--passes', type=int, default=3)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--sides', type=int, default=1)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--lash-changed-only', action='store_true')
    parser.add_argument('--mixed', type=int, default=None)
    parser.add_argument('--plans', default=None)
    args = parser.parse_args()
    root = Path(args.suite)
    for name in args.names:
        started = time.time()
        vessel = read_vessel(vessel_path(root, name))
        instance = without_idle_ports(read_instance(root/'Instances'/f'{name}.xml'))
        published = read_result(root/'Results'/f'{name}_results.xml')
        if args.mixed is not None:
            instance.max_mixed_bays = args.mixed
        best, odd = plan(vessel, instance, args.budget, args.passes, args.workers, args.seed, args.sides, args.lash_changed_only,
                         log=lambda m: print(name, m, flush=True))
        positions = full_positions(best['stow'], instance)
        readings = Readings(lash_changed_only=args.lash_changed_only)
        faults, m = evaluate(vessel, instance, positions, best['tanks'], readings)
        strict, _ = evaluate(vessel, instance, positions, best['tanks'])
        row = {'name': name, 'source_sha256': SOURCE, 'mixed_override': args.mixed, 'wall_s': time.time()-started, 'budget': args.budget, 'seed': args.seed, 'lash_changed_only': args.lash_changed_only,
               'faults': dict(faults), 'strict_faults': dict(strict), 'loaded': m['loaded'], 'list': len(instance.load), 'ballast': m['ballast'],
               'restows': m['restows'], 'hatch_restows': m['hatch_restows'], 'points_flat': m['points_flat'], 'points_file': m['points_file'],
               'objective_flat': m['objective_flat'], 'objective_file': m['objective_file'], 'published_loaded': int(published.points/50),
               'published_ballast': published.ballast, 'published_objective': published.objective, 'odd_twenties': len(odd),
               'pass': best['pass'], 'ballast_failed': best['failed'], 'master': {k: v for k, v in (best['master'] or {}).items() if k != 'allocation'}}
        with open(args.out, 'a') as sink:
            sink.write(json.dumps(row)+'\n')
        if args.plans:
            Path(args.plans).mkdir(parents=True, exist_ok=True)
            (Path(args.plans)/f'{name}.json').write_text(json.dumps({'positions': positions, 'tanks': best['tanks']}))
        print(name, 'loaded', m['loaded'], 'of', len(instance.load), 'published', row['published_loaded'], 'ballast', round(m['ballast']), 'vs', round(published.ballast),
              'objective', round(m['objective_flat']), 'vs', round(published.objective), 'restows', m['restows'], 'hatch', m['hatch_restows'], 'wall', round(row['wall_s']), dict(faults), flush=True)


if __name__ == '__main__':
    main()
