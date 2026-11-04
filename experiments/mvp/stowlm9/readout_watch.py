"""Owned live readout watcher; reload reporting code without touching map jobs."""
from pathlib import Path
import fcntl
import importlib
import json
import os
import time
import rule_readout

ROOT=Path(__file__).resolve().parent


def main():
    lock=(ROOT/'rule/readout-watch.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    (ROOT/'rule/readout-owner.json').write_text(json.dumps({'pid':os.getpid(),'started_at':time.time(),'reload_reporting_each_tick':True})+'\n')
    while True:
        try:
            importlib.reload(rule_readout).render(ROOT)
        except Exception as error:
            print('readout refresh failed',repr(error),flush=True)
        status=json.loads((ROOT/'rule/status.json').read_text())
        if status.get('pending')==0 and not status.get('active'):
            return
        time.sleep(15)


if __name__=='__main__':
    main()
