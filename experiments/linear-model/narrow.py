"""Fixed-manifest light/medium/full synthetic departures and narrow-band sweeps.

The cargo ladder is 20%, 60%, 100% of the smaller of declared aggregate stack
capacity and design-displacement payload after lightship and half-full ballast.
Full means that declared mass budget, not all TEU spaces occupied. Containers
are standard 20 ft and known 40 ft high cubes, below 30.48 t gross; full columns use declared stack-capacity-derived masses.
A synthetic yard holds every manifest box; no private operational data is used.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
import hashlib
import math
import os
from pathlib import Path
import runpy
import resource
import sys
import time

import numpy as np

from stowage.conventions import canonical_vessel_id, make_container_id
from stowage.model import Instance
from stowage.model.cargo import SpecialStowage
from stowage.vessel.fleet import list_fleet
from stowage.vessel.hydrostatics import HullGeometry

_HERE = Path(__file__).resolve().parent
_HELPERS = runpy.run_path(str(_HERE / 'validate.py'))


def cargo_instance(name, level, *, hc_fraction=.3):
    """Return a fully validated planning instance with an explicit fixed manifest."""
    base = _HELPERS['fleet_instance'](name, allow_artificial_stations=False)
    # Exact OR presolve: each occupied 20 ft cell carries at most one <=30.48 t
    # box (a 40 ft box shares its mass across two cells). Thus this tighter
    # stack bound follows from the existing box/cell constraints; it removes
    # no plan in this benchmark's declared box universe. Physics stays original.
    original_proof=production_proof(base,name)
    original_caps={stack.id:stack.max_mass_kg for stack in base.vessel.stacks}
    effective_caps={stack.id:min(stack.max_mass_kg,30_480.*len(stack.cell_ids))
                    for stack in base.vessel.stacks}
    local_stacks=[stack.model_copy(update={'max_mass_kg':effective_caps[stack.id]})
                  for stack in base.vessel.stacks]
    base=base.model_copy(update={'vessel':base.vessel.model_copy(update={'stacks':local_stacks})})
    from stowage.vessel.fleet import load_ship_params
    params = load_ship_params(name)
    geometry = HullGeometry.from_params(params)
    hull = base.vessel.hull
    ballast = sum(t.capacity_m3*t.content_density_kg_m3 for t in base.vessel.tanks if t.adjustable)
    stack_budget = sum(s.max_mass_kg for s in base.vessel.stacks)
    from stowage.linear.physics import _fixed
    constant_mass = float(sum(_fixed(base)[:,0]))
    design_payload = geometry.hydrostatic_row(hull.design_draught_m).displacement_kg-constant_mass-.5*ballast
    budget = min(stack_budget, design_payload)
    total = budget*{'light': .2, 'medium': .6, 'full': 1.}[level]
    specs = []
    full_cargo_centres = None
    if level == 'full':
        # Uniform full-mass protocol: standard 20 ft boxes minimise KG for the
        # 30.48 t gross limit. The lighter ladder cases retain the HC mixture.
        hc_fraction = 0.
        # The declared full budget is attained by a legal column inventory.
        # Attain every stack's mass limit using the fewest <=30.48 t boxes.
        # Full mass does not imply filling every tier with light boxes: that
        # would impose an unnecessarily high KG and an unstable departure.
        stack_scale = budget/stack_budget
        def column_masses(total_mass, count):
            remaining=total_mass
            result=[]
            for tier in range(count):
                # Leave at least the 2.2 t tare for every remaining box.
                gross=min(30_480.,remaining-2_200.*(count-tier-1))
                result.append(gross); remaining-=gross
            return result
        stacks = sorted(base.vessel.stacks, key=lambda s:s.id)
        lookup = {(s.bay,s.row,s.on_deck):s for s in stacks}
        pairs = [(s, lookup.get((s.bay+2,s.row,True))) for s in stacks
                 if s.on_deck and (s.bay-1)%4 == 0]
        pairs = [(s,t) for s,t in pairs if t is not None]
        physical_tokens = sum(math.ceil(stack_scale*s.max_mass_kg/30_480.) for s in stacks)
        chosen, covered, hc_tokens = [], set(), 0
        for left,right in pairs:
            count = 2*math.ceil(stack_scale*(left.max_mass_kg+right.max_mass_kg)/30_480.)
            replaced_tokens = sum(math.ceil(stack_scale*s.max_mass_kg/30_480.) for s in (left,right))
            new_tokens = physical_tokens-replaced_tokens+count
            if abs((hc_tokens+count)/new_tokens-hc_fraction) <= abs(hc_tokens/physical_tokens-hc_fraction):
                assert len(left.cell_ids) == len(right.cell_ids)
                assert abs(left.max_mass_kg-right.max_mass_kg) < 1e-6
                chosen.append((left,right)); covered.update((left.id,right.id))
                hc_tokens += count
                physical_tokens = new_tokens
        for left,right in chosen:
            count = math.ceil(stack_scale*(left.max_mass_kg+right.max_mass_kg)/30_480.)
            assert count <= min(len(left.cell_ids),len(right.cell_ids))
            specs.extend((True,gross,'on_deck') for gross in
                         column_masses(stack_scale*(left.max_mass_kg+right.max_mass_kg),count))
        for stack in stacks:
            if stack.id in covered: continue
            count = math.ceil(stack_scale*stack.max_mass_kg/30_480.)
            assert count <= len(stack.cell_ids)
            specs.extend((False,gross,'on_deck' if stack.on_deck else 'under_deck')
                         for gross in column_masses(stack_scale*stack.max_mass_kg,count))
        assert abs(sum(gross for _,gross,_ in specs)-total) < 1e-5
        hc_count = sum(hc for hc,_,_ in specs)
        std_count = len(specs)-hc_count
        tokens = physical_tokens
        cells = {cell.id:cell for cell in base.vessel.cells}
        mx=my=mz=0.
        for stack in stacks:
            count=math.ceil(stack_scale*stack.max_mass_kg/30_480.)
            for tier,gross in enumerate(column_masses(stack_scale*stack.max_mass_kg,count)):
                cell=cells[stack.cell_ids[tier]]
                mx+=gross*cell.lcg_m; my+=gross*cell.tcg_m
                mz+=gross*(stack.base_z_m+(tier+.5)*2.591)
        full_cargo_centres={'lcg_m':mx/total,'tcg_m':my/total,'kg_m':mz/total}
    else:
        tokens = math.ceil(total/17500.)
        hc_count = min(round(tokens*hc_fraction/2), tokens//2)
        std_count = tokens-2*hc_count
        nominal = std_count*20000.+hc_count*30000.
        factor = total/nominal
        assert factor <= 1.0 and tokens <= base.vessel.nominal_teu
        specs = [(index < hc_count, factor*(30000. if index < hc_count else 20000.),
                  'under_deck' if canonical_vessel_id(name)=='feeder_800' and level=='medium' and index<hc_count else None)
                 for index in range(std_count+hc_count)]
    template = base.containers[1]
    containers, yard_stacks = [], []
    for index, (high_cube, gross_mass, location) in enumerate(specs):
        assert (4200. if high_cube else 2200.) <= gross_mass <= 30_480.
        cid = make_container_id('QZXU', index+1)
        container = template.model_copy(update={
            'id': cid, 'iso_type': '45G1' if high_cube else '22G1',
            'length_ft': 40 if high_cube else 20,
            'height_m': 2.896 if high_cube else 2.591,
            'tare_kg': 4200. if high_cube else 2200.,
            'gross_mass_kg': gross_mass,
            'pol': 'THLCH', 'pod': 'THBKK', 'dg': None, 'oog': None,
            'special_stowage': [SpecialStowage(location)] if location else [], 'reefer': False, 'reefer_setpoint_c': None,
        })
        containers.append(container)
        bay, row = 2*(index%499)+1, index//499+1
        yard_stacks.append({'key': f'A-{bay:03d}-{row:02d}',
                            'length_ft': container.length_ft, 'boxes': [cid]})
    payload = base.model_dump(mode='python')
    payload.update(id=f'narrow_{name}_{level}', containers=[c.model_dump() for c in containers],
                   load_list=[c.id for c in containers], discharge_list=[])
    block = payload['port']['yard']['blocks'][0]
    block.update(id='A', n_bays=998, n_rows=max(1, math.ceil(len(containers)/499)),
                 max_tiers=1, reefer_rows=[], dg_block=False)
    payload['port']['yard']['blocks'] = [block]
    payload['yard']['stacks'] = yard_stacks
    instance = Instance.model_validate(payload)
    return instance, {'cargo_mass_kg': total, 'cargo_budget_kg': budget,
                      'constant_mass_kg': constant_mass, 'budget_binding': 'stack' if stack_budget <= design_payload else 'design_displacement',
                      'budget_basis': 'min(aggregate stack limits, design displacement minus lightship and half ballast)',
                      'design_payload_kg': design_payload, 'aggregate_stack_capacity_kg': stack_budget,
                      'hc_40ft_count': hc_count, 'standard_20ft_count': std_count,
                      'full_column_mass_recipe': 'standard20 maximum30480kg bottom boxes plus lighter top remainder' if level == 'full' else None,
                      'full_cargo_centres': full_cargo_centres,
                      'hc_under_deck_only': canonical_vessel_id(name)=='feeder_800' and level=='medium',
                      'production_layout_proof':original_proof,
                      'stack_cap_presolve':{'original_kg':original_caps,'effective_kg':effective_caps,
                         'reason':'Exact implied bound for known <=30480kg box universe: min(production stack cap, 30480 * available 20ft cells).'},
                      'hc_fraction_physical_tokens': 2*hc_count/tokens}


def case_band(instance, name, level):
    from stowage.linear.bands import band_for_instance
    band = band_for_instance(instance, max_abs_trim_m=.4 if level != 'full' else None)
    return band


def input_snapshot(name):
    """Pin experiment inputs as well as executable source throughout a case."""
    from stowage.conventions import canonical_vessel_id, LEGACY_FLEET_PARAMETER_PINS
    from stowage.vessel.fleet import load_ship_params
    from stowage.vessel.fleet_conditions import _nonlabel_parameter_fingerprint
    root = _HERE.parents[1]
    canonical = canonical_vessel_id(name)
    params = load_ship_params(canonical)
    paths = [root/'params'/'fleet'/f'{canonical}.toml',
             root/params['hull_form']['offsets_path']]
    snapshot = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in paths}
    # Only an explicitly legacy lookup projects the old filename/raw hash.
    # The complete nonlabel tree must still equal the pinned pre-D33 input;
    # changed physics retains its actual new digest and fails archived checks.
    pin = LEGACY_FLEET_PARAMETER_PINS.get(canonical)
    if name != canonical and pin is not None and _nonlabel_parameter_fingerprint(params) == pin[1]:
        del snapshot[f'params/fleet/{canonical}.toml']
        snapshot[f'params/fleet/{name}.toml'] = pin[0]
    return snapshot


def liquid_table_snapshot(name):
    """Pin qualified method-1 numerical inputs, including absence/fallback."""
    if os.environ.get('STOWAGE_STABILITY_LIQUID_DIRECT')=='1':
        return {'status':'explicit_direct_reference','files':{}}
    from stowage.vessel.fleet import load_ship_params
    from stowage.checker import stability_liquid
    params=load_ship_params(name)
    path=stability_liquid.manifest_path(params)
    result={'manifest_path':str(path),'files':{}}
    if not path.exists():
        result['status']='missing_manifest_direct_fallback'
        return result
    manifest=json.loads(path.read_text())
    result['status']='manifest_present'
    result['metadata_matches_current']=manifest.get('metadata')==stability_liquid.metadata(params)
    paths=[path]
    for key in manifest.get('tank_table_keys',{}).values():
        paths.extend((path.parent/f'liquid-{key}.npz',path.parent/f'liquid-{key}.qualified.json'))
    for file in sorted(set(paths)):
        result['files'][str(file)]=hashlib.sha256(file.read_bytes()).hexdigest() if file.exists() else None
    result['validation']=manifest.get('validation')
    return result


def manifest_identity(instance):
    from stowage.linear.operations import departure_manifest_identity
    ids, fingerprint=departure_manifest_identity(instance)
    return {'sha256':fingerprint,'container_ids':list(ids)}


def current_manifest_identity(name, level):
    instance,_=cargo_instance(name,level)
    return manifest_identity(instance)


def source_file_hashes():
    """Mirror cache.source_fingerprint's file inventory for race diagnostics."""
    from stowage.linear import cache
    package=Path(cache.__file__).resolve().parents[1]
    paths=set()
    for area in ('linear','checker','vessel','model','rules'):
        paths.update((package/area).rglob('*.py'))
    paths.update((package/'rules').rglob('*.toml'))
    paths.update(package/name for name in ('versions.py','params.py','conventions.py'))
    return {str(path.relative_to(package)):hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


def production_proof(instance, name):
    from stowage.vessel.fleet import load_ship_params
    params=load_ship_params(name)
    offset_path=_HERE.parents[1]/params['hull_form']['offsets_path']
    stations=[station.model_dump(mode='json') for station in instance.vessel.hull.stations]
    proof={'cell_count':len(instance.vessel.cells),'strength_station_count':len(stations),
           'hull_dimensions_m':{key:getattr(instance.vessel.hull,key) for key in ('lpp_m','beam_m','depth_m','design_draught_m')},
           'tank_ids':sorted(tank.id for tank in instance.vessel.tanks),
           'layout_hull_sha256':hashlib.sha256(json.dumps({key:instance.vessel.model_dump(mode='json')[key]
                    for key in ('cells','stacks','hull','tanks')},sort_keys=True,separators=(',',':')).encode()).hexdigest(),
           'strength_envelope_sha256':hashlib.sha256(json.dumps(stations,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
           'offsets_family':params['hull_form']['family'],'offsets_parent':params['hull_form'].get('parent'),
           'hull_offsets_ref':instance.vessel.hull.hull_offsets_ref,
           'offsets_path':params['hull_form']['offsets_path'],
           'offsets_sha256_expected':params['hull_form']['offsets_sha256'],
           'offsets_sha256_actual':hashlib.sha256(offset_path.read_bytes()).hexdigest()}
    assert proof['offsets_sha256_actual']==proof['offsets_sha256_expected']
    return proof


def public_crosscheck(model, instance, sample):
    """Compare public physics with a real-box sample represented as point loads.

    The sampler supplies actual box centres, including the known HC heights.
    Splitting a 40 ft box into its two supporting-cell loads preserves its total
    mass and all moments. This tests the physics reference, not full plan legality.
    """
    from stowage.model import ShipState
    from stowage.checker.stability import check_stability
    from stowage.checker.strength import check_strength
    cell, tank, heights, exact = sample
    loads = list(instance.vessel.fixed_loads)
    loads.extend((float(x), float(y), float(z), float(m))
                 for x,y,z,m in zip(model.cell_lcg_m, model.cell_tcg_m, heights, cell) if m)
    reference = instance.model_copy(update={'vessel': instance.vessel.model_copy(update={'fixed_loads': loads})})
    # ASSUMPTION: isolated numerical-reference comparison only. These explicit
    # synthetic test bounds match 3.1's policy fixtures; they are not a loading
    # manual, inferred hydrostatic-table limits, or production departure approval.
    # Retain any supplied tighter policy. Hull upper limits and all statutory
    # criteria still apply. Metrics are compared even when those criteria fail.
    policy={key:dict(value) for key,value in instance.rule_overrides.items()}
    hull=instance.vessel.hull
    def manual(field,fixture_default):
        value=getattr(hull,field,None)
        return fixture_default if value is None else value
    trim_policy=policy.setdefault('iscode.loading.trim',{})
    trim_policy.setdefault('minimum',manual('min_trim_m',-10.))
    if getattr(hull,'max_trim_m',None) is not None:
        trim_policy.setdefault('maximum',hull.max_trim_m)
    displacement=policy.setdefault('iscode.loading.displacement',{})
    displacement.setdefault('minimum',manual('min_displacement_kg',1.))
    displacement.setdefault('maximum',manual('max_displacement_kg',1.e9))
    reference=reference.model_copy(update={'rule_overrides':policy})
    values = {t.id: float(tank[i] if t.adjustable else instance.onboard.tank_masses_kg.get(t.id, 0.))
              for i,t in enumerate(sorted(instance.vessel.tanks, key=lambda t:t.id))}
    ship = ShipState(vessel_id=instance.vessel.id, tank_masses_kg=values)
    strength = check_strength(reference, ship, call_index=instance.call_index)
    previous_direct = os.environ.get('STOWAGE_STABILITY_DIRECT')
    method=os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD','1')
    # Method 1 must exercise the same qualified-table production dispatch as
    # the reference, rather than silently disabling both table families.
    os.environ['STOWAGE_STABILITY_DIRECT'] = '0' if method=='1' else '1'
    try:
        stability = check_stability(reference, ship, call_index=instance.call_index)
    finally:
        if previous_direct is None: os.environ.pop('STOWAGE_STABILITY_DIRECT', None)
        else: os.environ['STOWAGE_STABILITY_DIRECT'] = previous_direct
    if not strength.ran or not stability.ran:
        return {'passed': False, 'strength_ran': strength.ran, 'stability_ran': stability.ran,
                'synthetic_reference_loading_policy':policy,
                'strength_error': strength.error, 'stability_error': stability.error}
    deltas = {key: abs(float(stability.metrics[key])-float(exact[key]))
              for key in ('gm_m', 'trim_m', 'list_rad')}
    for metric in ('sf_n','bm_nm'):
        public = np.array([strength.metrics[f'{metric}@{format(float(x),".12g")}'] for x in model.station_x_m])
        deltas[metric] = float(np.max(np.abs(public-exact[metric])))
    passed = (max(deltas[k] for k in ('gm_m','trim_m','list_rad')) < 1e-5
              and deltas['sf_n'] < 1. and deltas['bm_nm'] < 10.)
    return {'passed': passed,
            'synthetic_reference_loading_policy':policy,
            'representation': 'physical manifest masses and true VCG represented as fixed point loads; verifies GM/trim/list/SF/BM only, not cargo GZ/weather/legality',
            'stability_geometry_mode': 'production method-1 dispatch' if method=='1' else 'forced direct method-2 historical reference',
            'production_usage':{key:value for key,value in stability.metrics.items() if 'table' in key or 'queries' in key},
            'tolerances':{'gm_m':1e-5,'trim_m':1e-5,'list_rad':1e-5,'sf_n':1.,'bm_nm':10.},
            'absolute_differences': deltas, 'exact_condition': exact,
            'strength_metrics': strength.metrics, 'stability_metrics': stability.metrics,
            'strength_violations': [v.model_dump(mode='json') for v in strength.violations],
            'stability_violations': [v.model_dump(mode='json') for v in stability.violations]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ship', choices=list_fleet(), required=True)
    parser.add_argument('--level', choices=['light', 'medium', 'full'], required=True)
    parser.add_argument('--samples', type=int, default=128)
    parser.add_argument('--calibration-samples', type=int)
    parser.add_argument('--hc-fraction', type=float, default=.3)
    parser.add_argument('--seed', type=int, default=3712)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--signed-metrics',action='store_true',
                        help='Observe signed unsafe/conservative tails in calibration and held-out samples.')
    parser.add_argument('--require-liquid-tables',action='store_true',
                        help='Require current qualified manifest and observed method-1 table queries.')
    args = parser.parse_args()
    from stowage.linear.cache import source_fingerprint
    import stowage.versions as versions
    def version_snapshot():
        return {**versions.component_versions(), **{k:v for k,v in vars(versions).items() if k.endswith('_VERSION')}}
    source_start = source_fingerprint()
    files_start = source_file_hashes()
    versions_start = version_snapshot()
    inputs_start = input_snapshot(args.ship)
    liquid_start=liquid_table_snapshot(args.ship)
    if args.require_liquid_tables:
        assert liquid_start.get('metadata_matches_current') is True,'current qualified liquid manifest required'
        assert liquid_start['files'] and all(v is not None for v in liquid_start['files'].values()),'qualified tank tables/certificates missing'
    free_surface_method=os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD','1')
    output = args.output or _HERE/f'narrow-{args.ship}-{args.level}.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    header={'pid':os.getpid(),'parent_pid':os.getppid(),'ship':args.ship,'level':args.level,
            'stability_free_surface_method':free_surface_method,
            'signed_metrics_schema_version':1 if args.signed_metrics else None,
            'requires_liquid_tables':args.require_liquid_tables,
            'source_fingerprint_start':source_start,'source_file_hashes_start':files_start,
            'component_versions_start':versions_start,'input_hashes_start':inputs_start,
            'liquid_table_inputs_start':liquid_start,
            'phase':'loading production context'}
    def write_header():
        path=output.with_suffix('.header.json'); temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps(_HELPERS['serializable'](header),indent=2)+'\n'); temporary.replace(path)
    write_header()
    start = time.perf_counter()
    instance, manifest = cargo_instance(args.ship, args.level, hc_fraction=args.hc_fraction)
    cargo_identity=manifest_identity(instance)
    loaded = time.perf_counter()
    assert instance.vessel.hull.stations, 'production strength stations required'
    from stowage.linear.physics import build_model, measure_errors
    from stowage.linear.bands import band_for_instance
    band = case_band(instance, args.ship, args.level)
    proof=manifest['production_layout_proof']
    header.update(phase='before calibration',manifest=manifest,departure_manifest_identity=cargo_identity,
                  production_proof=proof,band=asdict(band),
                  tank_windows_kg=getattr(band,'_tank_mass_bounds_kg',{}),
                  source_fingerprint_after_context=source_fingerprint(),
                  input_hashes_after_context=input_snapshot(args.ship))
    write_header()
    if header['source_fingerprint_after_context']!=source_start or header['input_hashes_after_context']!=inputs_start:
        raise RuntimeError('Source/input changed while loading the context; stopped before calibration; header retained')
    print(f'{args.ship}/{args.level} context_seconds={loaded-start:.3f} band={band}', flush=True)
    model = build_model(instance, band, **({'_samples': args.calibration_samples} if args.calibration_samples else {}))
    model_built=time.perf_counter()
    signed_calibration=None
    if args.signed_metrics:
        family_diagnostics=getattr(model,'measurement_diagnostics',{})
        assert 'signed_errors' in family_diagnostics,'builder must expose actual-solver signed family calibration'
        signed_calibration={'domain':'geometry_family_synthetic_total_moment_states',
            'samples':family_diagnostics.get('family_calibration_samples',family_diagnostics.get('samples')),
            'family_cache_key':family_diagnostics.get('family_cache_key'),
            'family_calibration_band':family_diagnostics.get('family_calibration_band'),
            'calibration_scope':family_diagnostics.get('calibration_scope'),
            'signed_errors':family_diagnostics['signed_errors'],
            'family_measured_absolute_errors':family_diagnostics.get('family_measured_absolute_errors'),
            'analytic_point_height_guards':family_diagnostics.get('analytic_point_height_guards'),
            'coverage':family_diagnostics.get('coverage'),
            'liquid_usage':family_diagnostics.get('liquid_usage'),
            'note':'Reused geometry-family calibration; this is not 192 physical conditions for this fixed manifest. No instance recalibration was run.'}
    built = time.perf_counter()
    # Capture a genuinely accepted held-out sample without changing the sampler
    # or reference calculation. One public-physics comparison per vessel suffices.
    import stowage.linear.physics as physics
    captured = []
    original_exact = physics.exact_condition
    def capture(*positional, **keywords):
        exact = original_exact(*positional, **keywords)
        if not captured and (band.displacement_min_kg <= exact['displacement_kg'] <= band.displacement_max_kg
                and band.draught_min_m <= exact['draught_m'] <= band.draught_max_m
                and abs(exact['trim_m']) <= band.max_abs_trim_m
                and abs(exact['list_rad']) <= band.max_abs_heel_rad):
            captured.append((positional[2].copy(), positional[3].copy(), keywords['cell_vcg_m'].copy(), exact))
        return exact
    if args.level == 'light': physics.exact_condition = capture
    try:
        errors, diagnostics = measure_errors(model, instance, samples=args.samples, seed=args.seed)
    finally:
        physics.exact_condition = original_exact
    swept = time.perf_counter()
    signed_heldout=None
    if args.signed_metrics:
        assert 'signed_errors' in diagnostics,'held-out actual-solver signed statistics required'
        signed_heldout={'domain':'fixed_manifest_physical_loading_conditions','samples':diagnostics['samples'],
                        'signed_errors':diagnostics['signed_errors'],'liquid_usage':diagnostics.get('liquid_usage',{})}
    checker_comparison = public_crosscheck(model, instance, captured[0]) if captured else None
    inputs_end = input_snapshot(args.ship)
    source_end = source_fingerprint()
    files_end = source_file_hashes()
    versions_end = version_snapshot()
    liquid_end=liquid_table_snapshot(args.ship)
    result = {'production_proof': proof, 'departure_manifest_identity':cargo_identity,
              'stability_free_surface_method':free_surface_method,
              'signed_metrics_schema_version':1 if args.signed_metrics else None,
              'requires_liquid_tables':args.require_liquid_tables,
              'signed_calibration':signed_calibration,'signed_heldout':signed_heldout,
              'pid':os.getpid(),'source_file_hashes_start':files_start,'source_file_hashes_end':files_end,
              'source_fingerprint_start': source_start, 'source_fingerprint_end': source_end,
              'component_versions_start': versions_start, 'component_versions_end': versions_end,
              'input_hashes_start': inputs_start, 'input_hashes_end': inputs_end,
              'liquid_table_inputs_start':liquid_start,'liquid_table_inputs_end':liquid_end,
              'reference_table_inputs_clean':liquid_start==liquid_end,
              'source_clean': source_start == source_end and files_start == files_end and versions_start == versions_end and inputs_start == inputs_end,
              'public_checker_comparison': checker_comparison,
              'ship': args.ship, 'level': args.level, 'manifest': manifest,
              'model_version': model.version, 'measurement_only': False,
              'tank_windows_kg': getattr(model.band, '_tank_mass_bounds_kg', {}),
              'operating_trim_limit_m': instance.vessel.hull.max_trim_m,
              'operating_list_limit_rad': instance.vessel.hull.max_list_rad,
              'operating_gm_limit_m': model.gm_min_m,
              'band': asdict(model.band), 'model_errors': asdict(model.errors),
              'calibration': getattr(model, 'measurement_diagnostics', {}),
              'sweep_errors': asdict(errors), 'diagnostics': diagnostics,
              'station_x_m': model.station_x_m, 'context_seconds': loaded-start,
              'build_seconds': built-loaded, 'sweep_seconds': swept-built,
              'coefficient_build_seconds':model_built-loaded,
              'public_checker_seconds': time.perf_counter()-swept,
              'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2 if sys.platform == 'darwin' else 1024),
              'elapsed_seconds': time.perf_counter()-start}
    output = args.output or _HERE/f'narrow-{args.ship}-{args.level}.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(_HELPERS['serializable'](result), indent=2)+'\n')
    print(json.dumps({'ship': args.ship, 'level': args.level, 'elapsed_seconds': result['elapsed_seconds'],
                      'build_seconds': result['build_seconds'], 'sweep_seconds': result['sweep_seconds'],
                      'samples': diagnostics['samples'], 'attempts': diagnostics['attempts'],
                      'source_clean': result['source_clean'], 'gm_max_m': diagnostics['max']['gm_m'], 'gm_components': diagnostics.get('gm_components')}), flush=True)

    if not result['source_clean']:
        raise RuntimeError('Source/component versions changed during the case; recorded as dirty, not current validation')
    if not result['reference_table_inputs_clean']:
        raise RuntimeError('Qualified liquid-table inputs changed during the case; original observations retained')
    if args.require_liquid_tables:
        usage=signed_heldout.get('liquid_usage',{}) if signed_heldout else {}
        assert usage.get('table',0)>0,'no accepted method-1 table queries observed'
    if checker_comparison is not None and not checker_comparison['passed']:
        raise RuntimeError('Public physics crosscheck failed; measurements and discrepancy retained in case JSON')


if __name__ == '__main__':
    main()
