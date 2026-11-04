"""Refresh the retained-failure mix census overnight; no solver work."""
import fcntl
import json
import os
from pathlib import Path
import time

from mix import render

HERE = Path(__file__).resolve().parent


def main():
    lock = (HERE/'rule/mix-watch.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    (HERE/'rule/mix-watch-owner.json').write_text(json.dumps(dict(pid=os.getpid(), started_at=time.time())))
    while True:
        try:
            render()
        except Exception as error:
            print('mix refresh failed:', repr(error), flush=True)
        status = json.loads((HERE/'rule/status.json').read_text())
        if status.get('pending') == 0 and not status.get('active'):
            return
        time.sleep(60)


if __name__ == '__main__':
    main()
