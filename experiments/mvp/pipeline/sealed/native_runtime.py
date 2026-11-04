"""Route unchanged native planners/checkers to this harness's numerical caches."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import shutil
import time

from .common import HERE, ROOT, HarnessError, atomic_json, read_json, sha256

CACHE = HERE/'runtime-cache'


def _sign_receipt(receipt, *, key_dir=None):
    """Use the real signer, routing its writable key directory into our fence."""
    from experiments.mvp.pipeline.package.receipt import sign_receipt
    return sign_receipt(receipt, key_dir=HERE/'.keys')


def configure():
    """Path configuration only: no planner, checker or rule function is replaced."""
    # Import core first: its legacy import-time vessel path is then replaced.
    importlib.import_module('experiments.mvp.pipeline.runner.core')
    runtime = importlib.import_module('experiments.mvp.pipeline.runner.runtime')
    admission = importlib.import_module('experiments.mvp.pipeline.runner.admission')
    runtime.HERE = HERE
    admission.CACHE_ROOT = CACHE
    os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(CACHE/'vessels')
    runtime.configure_caches()
    # Package passes its own explicit key path; changing receipt.KEY_DIR alone
    # cannot redirect it. Keep package.HERE intact for its real source hashes.
    package = importlib.import_module('experiments.mvp.pipeline.package')
    package.sign_receipt = _sign_receipt


def seed(out_dir):
    """Copy only hash-pinned physical acceleration, never any cargo plan."""
    snapshot = ROOT/'experiments/mvp/pipeline/maps/runtime-snapshot.json'
    declared = read_json(snapshot)['acceleration_cache_files']
    source_root = snapshot.parent/'runtime'
    files = {}
    started = time.time()
    for relative, digest in sorted(declared.items()):
        parts = Path(relative).parts
        if parts[:3] == ('experiments','cache','stability') and len(parts) == 4:
            target = CACHE/'stability'/parts[-1]
        elif parts[:3] == ('experiments','cache','vessels') and len(parts) == 4 and parts[-1].startswith(
                ('feeder_500.full.','feeder_800.full.','feeder_1700.full.')):
            target = CACHE/'vessels'/parts[-1]
        else:
            continue
        source = source_root/relative
        if sha256(source) != digest:
            raise HarnessError('Numerical cache source differs from generation snapshot')
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.is_symlink() or not target.exists() or sha256(target) != digest:
            temporary = target.with_suffix(target.suffix+f'.{os.getpid()}.tmp')
            shutil.copy2(source,temporary)
            if sha256(temporary) != digest:
                raise HarnessError('Numerical cache copy hash differs')
            temporary.replace(target)
        shutil.copystat(source,target)
        files[str(target.relative_to(HERE))] = dict(sha256=digest,bytes=source.stat().st_size)
    report = dict(started=started,completed=time.time(),source_sha256=sha256(snapshot),
                  files=files,plans_copied=False,policy='Exact physical bytes and dispatch mtimes; no solver changes')
    atomic_json(Path(out_dir)/'cache-seeding.json',report)
    return report
