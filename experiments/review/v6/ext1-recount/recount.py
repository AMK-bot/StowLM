#!/usr/bin/env python3
"""Independent read-only recount. Comparison is an explicit later operation."""
import argparse
import ctypes
import errno
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from metrics import recount
from integrity import verify

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RUN = ROOT / 'experiments/mvp/pipeline/sealed/runs/ext-1'
MEASUREMENT = ROOT / 'experiments/mvp/pipeline/sealed/measurements/ext-1'
PREREG = 'ba3dcc46f4b773b484d156288d8ee5c5fe682b2fc8f4d5096ee2e37de211e206'


def controller_state():
    """Read process identity without sending any signal (macOS libproc)."""
    library = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    path = ctypes.create_string_buffer(4096)
    ctypes.set_errno(0)
    size = library.proc_pidpath(25903, path, len(path))
    error = ctypes.get_errno()
    return {'pid': 25903, 'state': 'live' if size > 0 else
            'exited' if error == errno.ESRCH else 'unknown', 'errno': error}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def capture(final=False):
    # This is deliberately the first content read on each invocation.
    assert sha((MEASUREMENT / 'PREREG.md').read_bytes()) == PREREG
    complete = (RUN / 'COMPLETE.json').is_file() if final else False
    process = controller_state() if final and not complete else None
    if final and not complete and process['state'] != 'exited':
        raise SystemExit('Final recount requires COMPLETE.json or confirmed controller exit')
    paths = ['config.json', 'rows.json', 'instruction-allocation.json']
    raw = {name: (RUN / name).read_bytes() for name in paths}
    config, rows, allocation = (json.loads(raw[name]) for name in paths)
    result = recount(rows, config['maps'], allocation)
    provenance = verify(RUN, MEASUREMENT, rows)
    if final:
        assert all(sha((RUN / name).read_bytes()) == sha(data)
                   for name, data in raw.items()), 'raw inputs changed during final recount'
    return {
        'label': 'fresh held-out extension, run after the freeze; not the pre-registered sealed claim',
        'phase': 'FINAL' if final else 'PARTIAL: live run; fixed registered denominators',
        'termination_evidence': 'COMPLETE.json' if complete else process,
        'captured_utc': datetime.now(timezone.utc).isoformat(),
        'input_sha256': {name: sha(data) for name, data in raw.items()},
        'recount_source_sha256': {name: sha((HERE / name).read_bytes())
                                  for name in ('recount.py', 'metrics.py', 'integrity.py')},
        'completion_marker_sha256': sha((RUN / 'COMPLETE.json').read_bytes())
            if complete else None,
        'metrics': result,
        'provenance': provenance,
        'independence': 'No report or scoring implementation imported; no REPORT.md/report.json/figures read by capture.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--final', action='store_true')
    parser.add_argument('--output', default='partial.json')
    args = parser.parse_args()
    output = (HERE / args.output).resolve()
    if not output.is_relative_to(HERE):
        parser.error('output must remain inside ext1-recount')
    result = capture(args.final)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'phase': result['phase'], 'output': str(output),
                      'input_sha256': result['input_sha256']}, indent=2))


if __name__ == '__main__':
    main()
