"""Legacy filename, now quarantine-only: stop, drain and archive; never relaunch."""
import argparse
import fcntl
import json
from pathlib import Path
import time

HERE = Path(__file__).resolve().parent


def quarantine(run,archive,reason):
    run,archive = Path(run).resolve(),Path(archive).resolve()
    if not all(p.is_relative_to(HERE) for p in (run,archive)):
        raise ValueError('quarantine paths must remain under hybrid')
    (run/'STOP').touch()
    (run/'QUARANTINED.json').write_text(json.dumps(dict(quarantined=True,counts_for_d199=False,reason=reason),indent=1)+'\n')
    with (run/'RUN.LOCK').open('r') as lock:
        while True:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(5)
        if archive.exists():
            raise ValueError('archive already exists; refusing overwrite')
        run.rename(archive)
    print(f'Quarantined: {archive}; no automatic relaunch',flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    parser.add_argument('archive',type=Path)
    parser.add_argument('--reason',required=True)
    args = parser.parse_args()
    quarantine(args.run,args.archive,args.reason)
