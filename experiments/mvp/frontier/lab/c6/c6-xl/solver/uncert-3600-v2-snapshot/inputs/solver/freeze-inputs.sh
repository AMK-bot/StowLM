#!/bin/zsh
# Freeze only admitted ship inputs; performs hashing/JSON IO, no numerical work.
set -eu
.venv/bin/python -B - "${1:?ship id}" "${2:?immutable admission cohort}" "${3:?approved map list}" "${4:?loaded physical certificate}" <<'PY'
import hashlib,json,sys,time
from pathlib import Path
root=Path.cwd()
xl=root/'experiments/mvp/frontier/lab/c6/c6-xl'
ship,cohort_arg,list_arg,certificate_arg=sys.argv[1:]
if ship not in ('synthetic_3600','synthetic_8000','synthetic_3600_cert','synthetic_8000_cert'):
    raise SystemExit('Unknown private ship ID')
cohort,list_path,certificate=(Path(s).resolve() for s in (cohort_arg,list_arg,certificate_arg))
for p in (cohort,list_path,certificate):
    if not p.is_relative_to(xl) or not p.is_file():
        raise SystemExit(f'Admission artifact missing/outside XL: {p}')
proof=json.loads(certificate.read_text())
if not (proof.get('planning_admitted') is True or proof.get('experimental_planning_admitted') is True
        or proof.get('admission_by_ship',{}).get(ship) is True):
    raise SystemExit('Loaded physical certificate does not admit this ship')
admission=json.loads(cohort.read_text())
for key in ('admitted','planning_admitted','hydrostatics_valid'):
    if admission.get(key) is False:
        raise SystemExit(f'Immutable admission artifact reports {key}=false')
files={}
def add(path,kind,expected=None):
    path=Path(path)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    if expected is not None and digest!=expected:
        raise SystemExit(f'Pinned runtime drift: {path}')
    files[path.relative_to(root).as_posix()]=dict(path=path.relative_to(root).as_posix(),
        resolved_path=str(path.resolve()),sha256=digest,kind=kind)
runtime=xl/'runtime'
manifest=runtime/'SNAPSHOT.json'
add(manifest,'runtime_manifest')
for item in json.loads(manifest.read_text())['files']:
    add(runtime/item['path'],'pinned_runtime',item['sha256'])
for p,kind in [(xl/'build_ships_maps.py','registration'),(xl/'ships'/f'{ship}.toml','raw_ship'),
               (xl/'ships'/f'{ship}.vessel.json','embedded_ship'),(cohort,'admission_cohort'),
               (list_path,'approved_map_list'),(certificate,'loaded_physical_certificate'),
               (xl/'solver/run-paired.sh','orchestrator'),(xl/'solver/freeze-inputs.sh','snapshot_builder')]:
    add(p,kind)
if proof.get('certificate') and proof.get('certificate_sha256'):
    # Hash the reference certificate without opening its witness plan as data.
    reference=Path(proof['certificate'])
    add(reference if reference.is_absolute() else root/reference,
        'loaded_reference_certificate_bytes_only',proof['certificate_sha256'])
add(xl/'solver/MEASUREMENT-ALLOCATION.json','preregistered_allocation')
for p in sorted((xl/'solver').glob(f'benchmark-{ship}-*')):
    if p.is_file():
        add(p,'frozen_selection_artifact')
paths=[line.strip() for line in list_path.read_text().splitlines() if line.strip() and not line.lstrip().startswith('#')]
for rel in paths:
    p=(root/rel).resolve()
    if not p.is_relative_to(xl/'maps') or p.suffix!='.json':
        raise SystemExit(f'Map outside own map directory: {p}')
    instance=json.loads(p.read_text())
    if instance['vessel']['id']!=ship:
        raise SystemExit(f'Map has wrong ship: {p}')
    add(p,'approved_map')
body=dict(ship=ship,created_unix=time.time(),files=list(files.values()),maps=paths,
          physical_certificate=str(certificate.relative_to(root)),
          admission_cohort=str(cohort.relative_to(root)),
          guard='All declared bytes verified before any child and after every run; unrelated pending ships excluded.')
out=xl/'solver'/f'INPUT-SNAPSHOT.{ship}.json'
if out.exists():
    raise SystemExit(f'Snapshot already exists; retain it and use a separately named run if inputs change: {out}')
out.write_text(json.dumps(body,indent=2)+'\n')
default=xl/'solver/INPUT-SNAPSHOT.json'
if not default.exists():
    default.write_bytes(out.read_bytes())
print(f'Frozen {len(files)} files, {len(paths)} maps: {out.relative_to(root)}')
PY
