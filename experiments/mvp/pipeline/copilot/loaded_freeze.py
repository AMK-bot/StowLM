"""Reject a disk-valid freeze when critical modules were loaded from older source.

This module uses only the standard library and never imports the agent. Call it
before paid work, after the critical modules needed by that work are imported.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sys
from typing import Mapping


CRITICAL_MODULES = {
    'experiments.mvp.pipeline.copilot.agent': 'agent.py',
    'experiments.mvp.pipeline.copilot.authorization': 'authorization.py',
    'experiments.mvp.pipeline.copilot.tools': 'tools.py',
    'experiments.mvp.pipeline.copilot.hero_run': 'hero_run.py',
}
STAMP = 'IMPORT_SOURCE_SHA256'


def validate_loaded_freeze(files: Mapping[str, str], *, root: Path,
                           modules: Mapping | None = None) -> list[dict]:
    """Bind loaded critical modules to their import-time source and frozen file.

    Absent modules are not imported as a side effect. A loaded module without its
    own stamp, missing from the manifest, or loaded from another path fails
    closed. ``modules`` supports isolated offline tests without mutating the
    interpreter's actual module registry.
    """
    root = Path(root).resolve()
    registry = sys.modules if modules is None else modules
    checked = []
    for name, relative in CRITICAL_MODULES.items():
        module = registry.get(name)
        if module is None:
            continue
        # Read the module dictionary directly: a package __getattr__ must not
        # synthesize a stamp or accidentally import another critical module.
        namespace = vars(module)
        stamp = namespace.get(STAMP)
        if not isinstance(stamp, str) or re.fullmatch(r'[0-9a-f]{64}', stamp) is None:
            raise RuntimeError('Loaded critical module has no import-time source stamp: ' + name)
        expected = files.get(relative)
        if not isinstance(expected, str) or re.fullmatch(r'[0-9a-f]{64}', expected) is None:
            raise RuntimeError('Loaded critical module is missing from the freeze: ' + name)
        source = root / relative
        loaded_file = namespace.get('__file__')
        if not isinstance(loaded_file, str) or Path(loaded_file).resolve() != source:
            raise RuntimeError('Loaded critical module came from another source path: ' + name)
        try:
            disk = hashlib.sha256(source.read_bytes()).hexdigest()
        except OSError as error:
            raise RuntimeError('Frozen critical source is unavailable: ' + relative) from error
        if disk != expected:
            raise RuntimeError('Critical source changed after final freeze: ' + relative)
        if stamp != expected:
            raise RuntimeError('Loaded code differs from final freeze: ' + name)
        checked.append(dict(module=name, source_file=relative,
                            import_source_sha256=stamp, frozen_source_sha256=expected))
    return checked
