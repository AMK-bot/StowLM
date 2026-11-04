"""Generate preregistered envelope maps; sealed files are never reopened later."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import multiprocessing
import os
from pathlib import Path
import shutil
import sys
import time
from capacity import wait_for_slot

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RUNTIME = HERE / 'runtime'

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.partial')
    tmp.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False)+'\n')
    tmp.replace(path)

def specs():
    cells = [(s, f, g) for s in ('feeder_500', 'feeder_800') for f in (60,70)
             for g in ('random_yard', 'simulated_yard')]
    return [dict(split=split, ship=s, fill_target=f, generator=g, rep=rep,
                 seed=306100000+offset+100*ci+rep,
                 path=f'experiments/mvp/pipeline/maps/{split}/{s}-{g}-f{f}-r{rep}.json')
            for split, offset, count in (('dev',0,4), ('sealed',10000,3))
            for ci,(s,f,g) in enumerate(cells) for rep in range(count)]

def configure():
    assert not any(n=='stowage' or n.startswith('stowage.') for n in sys.modules)
    for key in list(os.environ):
        if key.startswith('STOWAGE_'):
            del os.environ[key]
    os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(RUNTIME/'experiments/cache/vessels')
    os.environ['TMPDIR'] = str(HERE/'tmp')
    (HERE/'tmp').mkdir(exist_ok=True)
    sys.path.insert(0, str(RUNTIME/'src'))
    spec = importlib.util.spec_from_file_location('envelope_build', RUNTIME/'experiments/mvp/corpus_tools/build.py')
    build = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = build
    spec.loader.exec_module(build)
    build.ROOT = RUNTIME
    return build

def ordered(cargo, seed):
    # Copied C6 nested_order, preserving namespace and tie-breaking.
    groups = defaultdict(list)
    for box in cargo:
        key = json.dumps([box['pod'], box['length_ft'], box['reefer'], box['dg'] is not None,
                          box['empty'], box['special_stowage']], sort_keys=True)
        groups[key].append(box)
    for group in groups.values():
        group.sort(key=lambda b: hashlib.sha256(f"C6:{seed}:{b['id']}".encode()).hexdigest())
    counts = {k:len(v) for k,v in groups.items()}
    used = Counter()
    result = []
    for n in range(len(cargo)):
        key = min((k for k in groups if used[k]<counts[k]),
                  key=lambda k:(-((n+1)*counts[k]/len(cargo)-used[k]),k))
        result.append(groups[key][used[key]])
        used[key] += 1
    return result

def make_one(row):
    admission = wait_for_slot()
    # No producer diagnostics or counts escape the sealed generator.
    with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
        try:
            return _make_one(row, admission)
        except Exception:
            if row['split'] == 'sealed':
                raise RuntimeError('sealed generation/schema/envelope validation failed; cargo diagnostics suppressed') from None
            raise

def _make_one(row, admission):
    build = configure()
    from stowage.io import load_instance, save_instance
    from stowage.frozen import current_provenance, require_clean
    from stowage.model.instance import Instance
    from stowage.generate.random_yard import regional_booking_mass_budget, _full_vessel
    from stowage.generate.random_export import full_export_policy
    from stowage.params import load_param_file
    request = json.loads((HERE/'requests.json').read_text())
    assert sha(__file__) == request['generator_sha256']
    assert sha(HERE/'PREREG.md') == request['prereg_sha256']
    assert sha(HERE/'SEED-AUDIT-PRE.json') == request['seed_audit_sha256']
    assert sha(HERE/'runtime-snapshot.json') == request['runtime_snapshot_sha256']
    snapshot = json.loads((HERE/'runtime-snapshot.json').read_text())
    assert sha(build.__file__) == snapshot['builder_sha256'], 'producer changed after runtime freeze'
    source_files = build.source_snapshot()
    assert source_files == request['source_files']
    spec = dict(split=row['split'], rung='medium', vessel=row['ship'], generator=row['generator'],
                seed=row['seed'], port='laem_chabang_like', noise=.5, n_boxes=None,
                dense_20ft=False, tiny_dg_enabled=True, max_attempts=100,
                booking_slack=0., booking_mass_fraction=None)
    private = HERE/'tmp'/f"parent-{row['seed']}"
    private.mkdir(parents=True, exist_ok=False)
    try:
        result = build.generate_one((spec, str(private), source_files))
        if result['status'] == 'failed':
            # Keep failed sealed error opaque; never publish potential cargo values.
            raise RuntimeError('producer generation failed; no replacement seed permitted')
        _, parent = build.validate_shard(build.shard_path(spec,private), current_sources=source_files)
        require_clean(parent, what='envelope parent')
        payload = parent.model_dump(mode='json')
        cargo = [b for b in payload['containers'] if b['id'] in set(payload['load_list'])]
        order = ordered(cargo,row['seed'])
        capacity = len(parent.vessel.cells)
        limit = math.floor(row['fill_target']*capacity/100)
        physical = 0
        selected = []
        for b in order:
            cost = 1 if b['length_ft']==20 else 2
            if physical+cost>limit:
                break
            selected.append(b)
            physical += cost
        pretrim = len(selected)
        selected = selected[:195]
        physical = sum(1 if b['length_ft']==20 else 2 for b in selected)
        assert pretrim>195 or abs(physical/capacity-row['fill_target']/100)<=.025
        keep = {b['id'] for b in selected}
        payload['load_list'] = sorted(keep)
        payload['id'] = f"pipeline.{row['split']}.{Path(row['path']).stem}.s{row['seed']}"
        payload['family'].update(envelope_cohort=row['split'], envelope_parent_id=parent.id,
            n_boxes=str(len(selected)), booking_utilisation_teu=repr(sum(b['length_ft']/20 for b in selected)/capacity))
        payload['provenance'] = current_provenance(seed=row['seed'],created_by='experiment.pipeline.envelope').merge_nested(
            parent,parent_ids=[parent.id]).model_dump(mode='json')
        child = Instance.model_validate(payload)
        require_clean(child,what='envelope derivative')
        reference = full_export_policy(_full_vessel(row['ship']), load_param_file('cargo','feeder_export'))
        mass = sum(b.gross_mass_kg for b in child.containers if b.id in keep)
        child.family['booking_utilisation_mass'] = repr(mass / regional_booking_mass_budget(child.vessel,reference,mass))
        assert child.stage=='A' and not child.onboard.placements
        assert not child.discharge_list and not child.forecasts and not child.plan_calls
        assert all(b.pol==child.port.locode for b in child.containers if b.id in keep)
        assert len(child.load_list)<=195 and physical/capacity<=row['fill_target']/100
        assert child.vessel==parent.vessel and child.yard==parent.yard and child.voyage==parent.voyage
        target=ROOT/row['path']
        target.parent.mkdir(parents=True,exist_ok=True)
        assert not target.exists(), 'never replace generated maps'
        save_instance(child,target)
        assert load_instance(target)==child  # generator-only schema/roundtrip gate
        output=dict(row,sha256=sha(target),schema_valid=True,generation_status='accepted')
        if row['split']=='dev':
            output.update(boxes=len(selected),teu=sum(b['length_ft']/20 for b in selected),
                ports=len(child.rotation.calls),load_port_calls=1,onboard_boxes=0,
                discharge_ports=len({b['pod'] for b in selected}),physical_cell_demand=physical,
                physical_cell_capacity=capacity,physical_cell_fill=physical/capacity,
                pretrim_boxes=pretrim,cap_trimmed=pretrim>195)
        write(HERE/'status'/f"{row['seed']}.json",output)
        return output
    finally:
        shutil.rmtree(private)

def freeze():
    from seed_audit import audit
    build=configure()
    assert not (HERE/'requests.json').exists(), 'requests already frozen'
    report=json.loads((HERE/'SEED-AUDIT-PRE.json').read_text())
    assert report['seeds']==sorted(r['seed'] for r in specs())
    assert report['audit_script_sha256']==sha(HERE/'seed_audit.py')
    assert report['raw_audit_sha256']==sha(HERE/'SEED-AUDIT-PRE-RAW.json')
    assert report['raw_audit_script_sha256']==sha(HERE/'seed_audit_lexical_v1.py')
    assert report['resolution_script_sha256']==sha(HERE/'resolve_seed_audit.py')
    assert report['amendment_sha256']==sha(HERE/'AMENDMENT-1.md')
    assert report['disjoint'], 'seed overlap or incomplete audit'
    request=dict(format='pipeline-envelope-requests-v1',created_at_utc=datetime.now(timezone.utc).isoformat(),
        specs=specs(),generator_sha256=sha(__file__),prereg_sha256=sha(HERE/'PREREG.md'),
        source_files=build.source_snapshot(),runtime_snapshot_sha256=sha(HERE/'runtime-snapshot.json'),
        seed_audit_sha256=sha(HERE/'SEED-AUDIT-PRE.json'))
    write(HERE/'requests.json',request)
    print(json.dumps(dict(event='frozen',maps=56,dev=32,sealed=24)))

def publish():
    from seed_audit import audit
    rows=[json.loads((HERE/'status'/f"{r['seed']}.json").read_text()) for r in specs()]
    assert len(rows)==56 and all(r['schema_valid'] for r in rows)
    for row in rows:
        assert sha(ROOT/row['path'])==row['sha256']
    report=audit([r['seed'] for r in rows])
    write(HERE/'SEED-AUDIT-POST.json',report)
    assert report['disjoint']
    for split in ('dev','sealed'):
        (HERE/f'{split}.txt').write_text(''.join(r['path']+'\n' for r in rows if r['split']==split))
    write(HERE/'cohort.json',dict(format='pipeline-envelope-cohort-v1',maps=rows))
    report_dev(rows)
    request=json.loads((HERE/'requests.json').read_text())
    paths=[HERE/n for n in ('generate.py','capacity.py','prepare_runtime.py','probe_runtime.py','runtime-cache-status.json','seed_audit.py','lab_set.py',
        'test_maps.py','PREREG.md','AMENDMENT-1.md','resolve_seed_audit.py','seed_audit_lexical_v1.py',
        'SEED-AUDIT-PRE-RAW.json','requests-pre-key-preservation.json','requests.json','runtime-snapshot.json','SEED-AUDIT-PRE.json',
        'SEED-AUDIT-POST.json','dev.txt','sealed.txt','cohort.json','DEV-REPORT.json','DEV-REPORT.md')]
    paths += [RUNTIME/name for name in request['source_files']]
    paths += [RUNTIME/'experiments/mvp/corpus_tools/build.py']
    write(HERE/'MANIFEST.json',dict(format='pipeline-envelope-manifest-v1',maps=rows,
        counts=dict(dev=32,sealed=24),files={p.relative_to(ROOT).as_posix():sha(p) for p in paths},
        sealed_policy='Hash/count/schema status only until task 3.1; no planner run',workers=2))
    print(json.dumps(dict(event='published',dev=32,sealed=24,manifest_sha256=sha(HERE/'MANIFEST.json'))))

def report_dev(rows):
    dev=[r for r in rows if r['split']=='dev']
    write(HERE/'DEV-REPORT.json',dict(maps=dev))
    lines=['# Development cohort only','', 'All are stage A, one load call, empty ship; physical fills use the medium region.',
        '', '| Ship | Fill | Yard | Boxes (4 maps) | TEU (4 maps) | Rotation ports (4 maps) |',
        '|---|---|---|---|---|---|']
    for s,f,g in dict.fromkeys((r['ship'],r['fill_target'],r['generator']) for r in dev):
        cell=[r for r in dev if (r['ship'],r['fill_target'],r['generator'])==(s,f,g)]
        vals=[' / '.join(str(r[k]) for r in cell) for k in ('boxes','teu','ports')]
        lines.append(f'| {s} | {f}% | {g} | '+ ' | '.join(vals)+' |')
    lines += ['', 'Per-map paths, box/TEU/port counts, physical fills and cap trimming are in DEV-REPORT.json.',
        f"195-box cap applied on {sum(r['cap_trimmed'] for r in dev)} development maps. No final dev input exceeds the envelope.",
        'No sealed cargo statistics have been inspected or reported.']
    (HERE/'DEV-REPORT.md').write_text('\n'.join(lines)+'\n')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('freeze','generate','publish'))
    args=parser.parse_args()
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        assert os.environ.get(key)=='1',f'{key}=1 required'
    wait_for_slot()
    if args.action=='freeze':
        freeze()
    elif args.action=='publish':
        publish()
    else:
        request=json.loads((HERE/'requests.json').read_text())
        assert request['specs']==specs() and request['generator_sha256']==sha(__file__)
        pending=[]
        for row in specs():
            status=HERE/'status'/f"{row['seed']}.json"
            if status.exists():
                prior=json.loads(status.read_text())
                assert sha(ROOT/prior['path'])==prior['sha256']
            else:
                pending.append(row)
        with ProcessPoolExecutor(max_workers=2,mp_context=multiprocessing.get_context('spawn'),max_tasks_per_child=1) as pool:
            futures=[pool.submit(make_one,r) for r in pending]
            for future in as_completed(futures):
                row=future.result()
                print(json.dumps(dict(event='generated',split=row['split'],seed=row['seed'],schema_valid=row['schema_valid'])),flush=True)
        publish()

if __name__=='__main__':
    main()
