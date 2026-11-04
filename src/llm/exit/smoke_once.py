"""Durable at-most-once admitted GPU smoke, keyed by adapter and code content."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[3]
ATTEMPTS = ROOT/'experiments/mvp/exit/smoke-attempts'


def smoke_key(parent, code_pins):
    digest = hashlib.sha256()
    with (Path(parent)/'adapters.safetensors').open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            digest.update(block)
    return hashlib.sha256(json.dumps(dict(adapter=digest.hexdigest(), code=code_pins),
                         sort_keys=True).encode()).hexdigest()


def attempt_path(parent, code_pins, root=None):
    return Path(root or ATTEMPTS)/(smoke_key(parent, code_pins)+'.json')


@contextmanager
def claim_attempt(path):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    record = dict(status='started',pid=os.getpid(),utc=datetime.now(timezone.utc).isoformat())
    try:
        with path.open('x') as stream:
            json.dump(record,stream)
    except FileExistsError as exc:
        raise RuntimeError('smoke already attempted for this adapter/code; inspect its durable marker') from exc
    try:
        yield
        record['status'] = 'done'
    except BaseException:
        record['status'] = 'failed'
        raise
    finally:
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(record,indent=2))
        temporary.replace(path)
