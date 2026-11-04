"""Apply the analytic cover-port certificate to the fixed TRAIN ladder."""
from pathlib import Path
import hashlib
import json
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bound_ports
from catalog import load_set
from stowage.bounds import lower_bound as counting_bound
from stowage.io import load_instance


def main():
    manifest = {r['input']: r for r in json.loads((HERE/'MANIFEST.json').read_text())}
    out = []
    for path in load_set(str(HERE/'ladder24.txt')):
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        assert digest == manifest[path]['input_sha256']
        instance = load_instance(path)
        counting = counting_bound(instance)
        # No restricted page objective and no CP-SAT incumbent is a bound.
        base = dict(instance_id=instance.id, lower_bound=counting.weighted)
        row = bound_ports.lower_bound(instance, base_result=base)
        row.update(input=path, input_sha256=digest,
                   base_certificate='production assignment-independent counting bound')
        out.append(row)
        (HERE/'bound_ladder_results.json').write_text(json.dumps(out, indent=2)+'\n')
        print(instance.id, row['lower_bound'], flush=True)


if __name__ == '__main__':
    main()
