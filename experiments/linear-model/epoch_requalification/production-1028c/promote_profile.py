"""Use the unchanged atomic promoter with explicit pre-final provenance pins."""
from pathlib import Path
import importlib.util
import json
import sys
ROOT=Path(__file__).resolve().parent
OWNER=ROOT.parent
REPO=OWNER.parents[2]

def extra_pins(module):
    manifest=ROOT/'closure/run-producers.json'
    sources=json.loads(manifest.read_text())['sources']
    pins=[module.pin(REPO/path) for path in sources]
    pins.append(module.pin(manifest))
    amendment=ROOT/'closure/execution-amendments.json'
    if amendment.exists():
        record=json.loads(amendment.read_text())
        for name,expected in record['sources'].items():
            item=module.pin(REPO/name)
            if item['sha256']!=expected:raise ValueError('execution amendment source changed: '+name)
            pins.append(item)
        pins.append(module.pin(amendment))
    return pins

def wrap(module):
    original=module.build_ship
    def build(*args,**kwargs):
        entry,issues,rows=original(*args,**kwargs)
        if entry is not None:
            pins=extra_pins(module)
            for key in ('evidence','conservative_evidence','necessary_evidence'):
                if key in entry[1]:entry[1][key]=[*entry[1][key],*pins]
        return entry,issues,rows
    module.build_ship=build

class ProvenanceLoader:
    def __init__(self,inner):self.inner=inner
    def create_module(self,spec):return self.inner.create_module(spec)
    def exec_module(self,module):
        self.inner.exec_module(module)
        wrap(module)

def main():
    profile=sys.argv[1]
    if profile not in ('seawater','freshwater'):raise ValueError('unknown profile')
    from compute import abort_check
    abort_check()
    if profile=='freshwater':
        sys.path.insert(0,str(ROOT/profile))
        import profile_run
        original_spec=profile_run._ORIGINAL_SPEC
        def provenance_spec(name,location,*args,**kwargs):
            spec=original_spec(name,location,*args,**kwargs)
            if Path(location).resolve()==OWNER/'promote.py':spec.loader=ProvenanceLoader(spec.loader)
            return spec
        profile_run._ORIGINAL_SPEC=provenance_spec
        sys.argv=[str(ROOT/profile/'profile_run.py'),str(OWNER/'promote.py'),*sys.argv[2:]]
        profile_run.main()
        return
    spec=importlib.util.spec_from_file_location('prefinal_seawater_promoter',OWNER/'promote.py')
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module
    spec.loader.exec_module(module);wrap(module)
    sys.argv=[str(OWNER/'promote.py'),*sys.argv[2:]]
    result=module.main()
    if result:raise SystemExit(result)

if __name__=='__main__':main()
