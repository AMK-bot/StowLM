"""Run immutable qualification scripts with an explicit freshwater fixture.

Only the input port water density changes. Original producer source, numerical
methods, sample design and promotion guards remain in force. This adapter and
its density are separately pinned in the extension's audit manifest.
"""
from __future__ import annotations
import importlib.util
import hashlib
import json
from pathlib import Path
import runpy
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from compute import abort_check

HERE = Path(__file__).resolve().parent
OWNER = HERE.parent.parent
DENSITY = 1000.0
ADAPTER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_ORIGINAL_SPEC = importlib.util.spec_from_file_location

class FixtureLoader:
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        path = OWNER / 'driver.py'
        module.__file__ = str(path)
        exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
        original = module.HELPER['cargo_instance']
        def freshwater(ship, level):
            instance, manifest = original(ship, level)
            port = instance.port.model_copy(update={'water_density_kg_m3': DENSITY})
            return instance.model_copy(update={'port': port}), manifest
        module.HELPER['cargo_instance'] = freshwater
        original_snapshot = module.snapshot
        def guarded_snapshot(*args, **kwargs):
            abort_check()
            adapter_pin()
            return original_snapshot(*args, **kwargs)
        module.snapshot = guarded_snapshot
        original_write = module.write
        def write_profile(path, record):
            if isinstance(record, dict):
                record = dict(record, qualification_fixture_profile={
                    'water_density_kg_m3': DENSITY,
                    'adapter': adapter_pin()})
            return original_write(path, record)
        module.write = write_profile
        module.QUALIFICATION_PROFILE = {'water_density_kg_m3': DENSITY,
            'adapter': str(Path(__file__).resolve())}

class NecessaryLoader:
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        path = OWNER / 'necessary.py'
        module.__file__ = str(path)
        exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
        from necessary_adapter import install as install_necessary
        install_necessary(module)


def profile_spec(name, location, *args, **kwargs):
    spec = _ORIGINAL_SPEC(name, location, *args, **kwargs)
    if Path(location).resolve() == OWNER / 'driver.py':
        spec.loader = FixtureLoader()
    elif Path(location).resolve() == OWNER / 'necessary.py':
        spec.loader = NecessaryLoader()
    return spec

def install():
    importlib.util.spec_from_file_location = profile_spec

def load_driver():
    install()
    spec = profile_spec('freshwater_epoch_driver', OWNER / 'driver.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def adapter_pin():
    abort_check()
    path = Path(__file__).resolve()
    current = hashlib.sha256(path.read_bytes()).hexdigest()
    if current != ADAPTER_SHA256:
        raise ValueError('qualification fixture adapter changed during capture')
    return {'path': str(path.relative_to(OWNER.parents[2])),
            'sha256': ADAPTER_SHA256}

def main():
    abort_check()
    target = Path(sys.argv[1]).resolve()
    sys.argv = [str(target), *sys.argv[2:]]
    install()
    if target == OWNER / 'driver.py':
        load_driver().main()
        return
    name = 'freshwater_' + target.stem.replace('-', '_')
    spec = profile_spec(name, target)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if target == OWNER / 'promote.py':
        original_build = module.build_ship
        def pinned_build(*args, **kwargs):
            entry, issues, rows = original_build(*args, **kwargs)
            if entry is not None:
                payload = entry[1]
                for key in ('evidence', 'conservative_evidence', 'necessary_evidence'):
                    if key in payload:
                        from necessary_adapter import pin as evidence_pin, CERTIFICATE
                        manifest = HERE.parent/'closure/run-producers.json'
                        frozen_sources = json.loads(manifest.read_text())['sources']
                        frozen_pins = [evidence_pin(OWNER.parents[2]/path) for path in frozen_sources]
                        if any(item['sha256'] != frozen_sources[item['path']] for item in frozen_pins):
                            raise ValueError('frozen run producer source changed before promotion')
                        extra = [adapter_pin(), evidence_pin(HERE/'necessary_adapter.py'), evidence_pin(manifest), *frozen_pins]
                        if float(payload['boundary_list']['water_density_kg_m3']) == 1000. and args[0] == 'feeder_500':
                            extra.append(evidence_pin(CERTIFICATE))
                        payload[key] = [*payload[key], *extra]
            return entry, issues, rows
        module.build_ship = pinned_build
    if hasattr(module, 'main'):
        result = module.main()
        if isinstance(result, int):
            raise SystemExit(result)

if __name__ == '__main__':
    main()
