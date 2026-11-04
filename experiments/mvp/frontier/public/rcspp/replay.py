"""Replay published RCSPPSuite plans through check_rcspp.py. Usage: replay.py <suite_dir> <out.jsonl> [name ...]."""
from collections import Counter
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_rcspp import Readings, evaluate
from rcspp_io import read_instance, read_result, read_vessel, vessel_path, without_idle_ports


def main():
    root, out = Path(sys.argv[1]), Path(sys.argv[2])
    names = sys.argv[3:] or sorted(p.stem for p in (root/'Instances').glob('*.xml'))
    vessels, totals = {}, Counter()
    with open(out, 'w') as sink:
        for name in names:
            start = time.time()
            instance = without_idle_ports(read_instance(root/'Instances'/f'{name}.xml'))
            result = read_result(root/'Results'/f'{name}_results.xml')
            vessel = vessels.setdefault(name.split('_')[0], read_vessel(vessel_path(root, name)))
            faults, m = evaluate(vessel, instance, result.positions, result.tanks, Readings())
            row = {'name': name, 'faults': dict(faults), 'loaded': m['loaded'], 'file_points': result.points, 'file_ballast': result.ballast,
                   'file_restows': result.restows, 'file_objective': result.objective, 'ballast': m['ballast'], 'restows': m['restows'],
                   'hatch_restows': m['hatch_restows'], 'moves': m['moves'], 'points_flat': m['points_flat'], 'points_file': m['points_file'],
                   'objective_flat': m['objective_flat'], 'objective_file': m['objective_file'], 'ports': m['ports'], 'seconds': time.time()-start}
            sink.write(json.dumps(row)+'\n')
            totals.update({k: 1 for k in faults})
            print(name, round(row['seconds'], 1), 's', 'loaded', m['loaded'], 'obj', round(m['objective_flat'], 2), 'file', result.objective,
                  'restows', m['restows'], dict(faults), flush=True)
    print('plans with a fault, by rule:', dict(totals))


if __name__ == '__main__':
    main()
