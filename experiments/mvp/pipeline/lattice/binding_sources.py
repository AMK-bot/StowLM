"""Shared planning-runtime closure; measurement launchers never enter its hash."""
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json

SOURCE_SCOPE = ('binding Python runtime files in regular packages; excludes tests, measurement launchers, '
                '_logic3_* discovery wrappers, review3 operational/measurement drivers and the audited deadline measurement package')
_LAUNCHERS = {'measure.py', 'live_campaign.py'}
_REVIEW3_DRIVERS = {'activation.py', 'campaign.py', 'measurement.py', 'operations.py', 'recorded.py', 'watchdog.py'}
_DATA_DIRS = {'measurements', 'fixtures', 'cache', 'runtime-cache', '__pycache__', 'sealed'}


def runtime_source_files(binding_root):
    root = Path(binding_root)
    found = []
    def visit(directory):
        for path in sorted(directory.iterdir()):
            if path.is_dir():
                if path.relative_to(root).as_posix() == 'deadline':
                    continue
                if (path.name not in _DATA_DIRS and not path.name.startswith(('.', 'test', 'pytest'))
                        and (path/'__init__.py').is_file()):
                    visit(path)
                continue
            if path.suffix != '.py' or path.name in _LAUNCHERS or path.name.startswith(('test_', 'measurement_', '_logic3_')):
                continue
            relative = path.relative_to(root)
            if relative.parent.as_posix() == 'review3' and path.name in _REVIEW3_DRIVERS:
                continue
            found.append(relative.as_posix())
    if root.is_dir():
        visit(root)
    return tuple(sorted(found))


def runtime_fingerprint(binding_root):
    root = Path(binding_root)
    return sha256_json({name: sha256_file(root/name) for name in runtime_source_files(root)})
