"""Promote a measured linear epoch only after independent qualification.

Calibration maxima are empirical reserves, not mathematical uniform bounds.
Each station and bound receives 1.1 times its measured unsafe envelope plus
an explicit reference-transfer envelope. Safe bias is reported separately.
The exact departure checker remains mandatory. Source: one-sided residual
validation and the triangle inequality; see stowage.linear.qualification.
"""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from dataclasses import fields
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SHIPS = ('feeder_500', 'feeder_1000', 'feeder_1700')
LEVELS = ('light', 'medium', 'full')
SIDES = ('lower_unsafe', 'upper_unsafe')
REFERENCE_REPORT = ROOT/'experiments/stability/fallback/final_report.json'
L5_SOURCE = ROOT/'experiments/stability/interpolation/error_statistics.py'


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pin(path):
    path = Path(path).resolve()
    try:
        name = str(path.relative_to(ROOT))
    except ValueError:
        name = str(path)
    return dict(path=name, sha256=sha(path))


def write(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(record, indent=2, allow_nan=False)+'\n')
    temporary.replace(path)


def merge_envelopes(records, count):
    from stowage.linear.margin_policy import _envelope, _zeros
    result = _zeros(count)
    for record in records:
        checked = _envelope(record, count, name='qualification observation')
        for side in SIDES:
            for metric, values in checked[side].items():
                result[side][metric] = np.maximum(result[side][metric], values).tolist()
    return result


def reference_gm_floor(ship, raw, *, report_path=REFERENCE_REPORT,
                       l5_source=L5_SOURCE, gm_min_m=.15):
    """Qualified empirical reference allowance at a possible GM crossing.

    L5 normalises by max(A, r*abs(GM)). The absolute branch applies below
    A/r=5m. Above it a fraction f<=.2 cannot move a positive reference GM
    below (1-f*r)*5>=4.99m, or a negative one above -4.99m. Consequently
    neither relative branch can cross the .15m loading minimum. This uses
    the measured qualified fraction as an empirical envelope, not permission
    to relax a regulatory criterion or a uniform mathematical error proof.
    """
    report_path,l5_source=Path(report_path),Path(l5_source)
    report=read(report_path)
    measured=report['qualification'][ship]
    fractions=measured['combined_l5_fraction']
    if (set(fractions)!={'gz','area','gm'} or any(
            not np.isfinite(value) or not 0 <= value <= .2 for value in fractions.values())
            or measured.get('loaded_cases') != 8):
        raise ValueError('reference report lacks eight qualified cases within0.2L5')
    tree=ast.parse(l5_source.read_text())
    assignments=[node.value for node in tree.body if isinstance(node,ast.Assign)
        and any(isinstance(target,ast.Name) and target.id=='L5_TOLERANCES'
                for target in node.targets)]
    if len(assignments)!=1:
        raise ValueError('unrecognised L5 tolerance source')
    absolute,relative=map(float,ast.literal_eval(assignments[0])['gm_m'])
    if (not np.isfinite([absolute,relative,gm_min_m]).all()
            or min(absolute,relative,gm_min_m)<=0 or fractions['gm']*relative>=1):
        raise ValueError('invalid GM crossing allowance inputs')
    transition=absolute/relative
    relative_branch_lower=(1-fractions['gm']*relative)*transition
    if relative_branch_lower<=gm_min_m:
        raise ValueError('relative GM error branch can cross the loading minimum')
    loaded_path=report_path.parent/'qualification'/ship/'liquid-loaded.json'
    loaded=read(loaded_path)
    if (loaded.get('complete') is not True or loaded.get('all_passed') is not True
            or loaded.get('compared_conditions')!=8 or len(loaded.get('cases',[]))!=8
            or any(case.get('passed') is not True for case in loaded['cases'])
            or loaded['empirical_error_budget']['combined']!=fractions):
        raise ValueError('reference loaded evidence disagrees with qualification report')
    # The report's numerical qualification must be the exact reference dispatch
    # measured by the driver, rather than a later/foreign manifest.
    files={str((Path(name) if Path(name).is_absolute() else ROOT/Path(name)).resolve()):digest
        for name,digest in raw['diagnostics']['reference_inputs']['files'].items()}
    manifest=Path(loaded['manifest'])
    manifest=manifest if manifest.is_absolute() else ROOT/manifest
    expected=files.get(str(manifest))
    if expected is None or sha(manifest)!=expected:
        raise ValueError('qualified reference manifest differs from raw calibration')
    evidence=[pin(report_path),pin(l5_source),pin(loaded_path),pin(manifest)]
    for name in ('kn-evidence.json','liquid-component.json'):
        evidence.append(pin(loaded_path.parent/name))
    for filename,digest in files.items():
        path=Path(filename)
        if path.suffix=='.json':
            if digest is None or sha(path)!=digest:
                raise ValueError('reference certificate changed: '+filename)
            evidence.append(pin(path))
    floor=fractions['gm']*absolute
    return dict(kind='qualified empirical reference-crossing allowance',
        gm_m=floor,combined_l5_fraction=fractions['gm'],l5_absolute_m=absolute,
        l5_relative=relative,absolute_branch_limit_m=transition,
        relative_branch_minimum_positive_gm_m=relative_branch_lower,
        loading_gm_min_m=gm_min_m,loaded_cases=8,evidence=evidence,
        warning='Empirical reference-error reserve, not regulatory tolerance approval.')


def validate_observation(record, raw, ship, level, phase):
    if (record.get('ship') != ship or record.get('level') != level
            or record.get('phase') != phase):
        raise ValueError('case identity mismatch')
    if (record.get('physical_inputs_unchanged') is not True
            or record.get('physical_inputs_start') != raw['physical_inputs']
            or record.get('physical_inputs_end') != raw['physical_inputs']):
        raise ValueError('physical inputs changed or differ from raw calibration')
    if (type(record.get('samples')) is not int or record['samples'] <= 0
            or record['diagnostics']['samples'] != record['samples']
            or record['diagnostics']['seed'] != record['seed']):
        raise ValueError('inconsistent physical sample population')
    if record.get('public_crosscheck_completed') is not True:
        raise ValueError('public reference comparison incomplete')
    diagnostics = record['calibration']
    for key in ('family_calibration_cache_key', 'response_calibration_identity',
                'reference_identity', 'family_domain'):
        if diagnostics[key] != raw['diagnostics'][key]:
            raise ValueError('physical calibration mismatch: '+key)


def validate_sampler_identity(record):
    """Bind new targeted evidence to its archived own-version source/config.

    Legacy measurements keep their original pinned evidence, without claiming
    a builder certificate that did not exist when they ran.
    """
    names=('sampler_builder_version','sampler_source_sha256','sampler_config','sampler_inputs_hash')
    if not any(name in record for name in names):
        return None
    if not all(name in record for name in names):
        raise ValueError('incomplete targeted sampler D30 identity')
    source=record['sampler_source_sha256']
    if (record['sampler_builder_version']!='1.0' or not isinstance(source,str)
            or len(source)!=64 or any(char not in '0123456789abcdef' for char in source)):
        raise ValueError('unsupported targeted sampler builder/source identity')
    config=record['sampler_config']
    if not isinstance(config,dict) or any(config.get(name)!=record.get(name)
            for name in ('ship','level','seed','samples')):
        raise ValueError('targeted sampler configuration differs from observation')
    inputs=dict(physical_inputs=record['physical_inputs_start'],config=config,
        builder_version=record['sampler_builder_version'],source_sha256=source)
    digest=hashlib.sha256(json.dumps(inputs,sort_keys=True,separators=(',',':'),
        allow_nan=False).encode()).hexdigest()
    if record['sampler_inputs_hash']!=digest:
        raise ValueError('targeted sampler numerical inputs hash mismatch')
    archive=HERE/f'targeted-sampler-{source[:12]}.py'
    if sha(archive)!=source:
        raise ValueError('targeted sampler archived source checksum mismatch')
    return {**pin(archive),'role':'targeted sampler archived source; own builder1.0'}


def table_row(ship, level, record):
    signed = record['diagnostics']['signed_errors']
    result = dict(ship=ship, level=level, samples=record['samples'], seed=record['seed'],
        band=record['band'])
    for metric in ('gm_m', 'trim_m', 'list_rad', 'draught_m', 'sf_n', 'bm_nm'):
        sides = ('lower_unsafe',) if metric == 'gm_m' else SIDES
        result[metric] = dict(
            unsafe_max=max(float(np.max(signed[side][metric])) for side in sides),
            unsafe_p99=max(float(np.max(signed['p99'][side][metric])) for side in sides))
    result['gm_m'].update(
        conservatism_max=float(signed['lower_conservatism']['gm_m']),
        conservatism_p99=float(signed['p99']['lower_conservatism']['gm_m']))
    return result


def _driver():
    spec = importlib.util.spec_from_file_location('linear_epoch_driver', HERE/'driver.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_equivalence(ship, folder, raw):
    """Compare current cached coefficients with a fresh numerical rebuild."""
    from stowage.linear.reusable import coefficient_family
    from stowage.linear.cache import response_calibration_identity
    helper = _driver()
    start = time.perf_counter()
    instance, _ = helper.HELPER['cargo_instance'](ship, 'light')
    cached = coefficient_family(instance)
    before = helper.snapshot(ship, cached.parameters)
    if before != raw['physical_inputs']:
        raise ValueError('equivalence starts on another physical epoch')
    rebuilt = coefficient_family(instance, force_rebuild=True)
    compared = []
    for descriptor in fields(cached.template):
        name = descriptor.name
        first, second = getattr(cached.template, name), getattr(rebuilt.template, name)
        if isinstance(first, np.ndarray):
            np.testing.assert_allclose(first, second, rtol=1e-10, atol=1e-7)
            compared.append('template.'+name)
        elif isinstance(first, (int, float, tuple)) and name not in ('version',):
            if first != second:
                raise ValueError('coefficient scalar mismatch: '+name)
    for name in ('response_sf','response_bm','buoyancy_sf','buoyancy_bm','lightship_sf','lightship_bm'):
        np.testing.assert_allclose(getattr(cached,name), getattr(rebuilt,name), rtol=1e-10, atol=1e-7)
        compared.append(name)
    after = helper.snapshot(ship, cached.parameters)
    if before != after or cached.cache_key != rebuilt.cache_key:
        raise ValueError('equivalence physical inputs changed')
    record = dict(ship=ship, passed=True, fresh_numeric_equal=True,
        physical_inputs=before, response_identity=response_calibration_identity(cached),
        coefficient_key=cached.cache_key, compared=compared,
        tolerance=dict(rtol=1e-10, atol=1e-7), seconds=time.perf_counter()-start)
    write(folder/'equivalence.json', record)
    return record


def build_ship(ship, folder, *, live=True, equivalence=False, reference_error_report=None,
               production_minimums=False, first_failure_report=None, include_necessary=False,
               necessary_edge_folder=None, necessary_calibration_enrichment_folders=()):
    """Collect every independent failure, returning no promotable partial entry."""
    from stowage.linear import margin_policy as policy
    from stowage.linear.qualification import qualify_holdout
    failures, evidence, cases, rows = [], [], {}, []
    raw_path = folder/'raw.json'
    raw = read(raw_path)
    evidence.append(pin(raw_path))
    diag = raw['diagnostics']
    response = diag['response_calibration_identity']
    from stowage.linear.family_calibration import (_validated_errors,
        _validate_calibration_certificate)
    from stowage.linear.cache import load_model
    template = load_model(diag['coefficient_family_cache_key'])
    if template is None:
        failures.append('measured coefficient cache missing or corrupt')
    else:
        try:
            errors = _validated_errors(raw['errors'],len(template.station_x_m))
            _validate_calibration_certificate(diag['calibration_certificate'],raw['key'],errors,diag)
        except (ValueError,KeyError,TypeError) as exc:
            failures.append('raw calibration certificate: '+str(exc))
    if raw['key'] != diag['family_calibration_cache_key'] or diag['samples'] != 192:
        failures.append('raw calibration must contain 192 current-key samples')
    if diag['reference_identity'] != raw['physical_inputs']['reference']:
        failures.append('raw reference identity mismatch')
    if diag['reference_identity'].get('qualified') is not True:
        failures.append('raw reference is unqualified')
    for phase in ('train','holdout'):
        for level in LEVELS:
            path = folder/f'{phase}-{level}.json'
            try:
                record = read(path)
                validate_observation(record,raw,ship,level,phase)
                if production_minimums and record['samples'] < (128 if phase=='train' else 64):
                    failures.append(f'{phase}/{level}: production promotion requires '
                        f'{128 if phase=="train" else 64} samples; got {record["samples"]}')
                cases[phase,level] = record
                evidence.append(pin(path))
                if phase == 'holdout':
                    rows.append(table_row(ship,level,record))
            except (OSError,ValueError,KeyError,TypeError) as exc:
                failures.append(f'{phase}/{level}: {exc}')
    boundary = None
    try:
        path = folder/'boundary-certificate.json'
        boundary = read(path)
        # Numerical/branch evidence is separately pinned by the boundary certificate.
        policy._evidence(boundary['evidence'])
        if production_minimums:
            for population in ('calibration_samples','holdout_samples'):
                counts=boundary['qualification'][population]
                for side in SIDES:
                    if type(counts.get(side)) is not int or counts[side]<64:
                        failures.append(f'boundary LIST: production promotion requires 64 '
                            f'{population}/{side}; got {counts.get(side)}')
        evidence.append(pin(path))
    except (OSError,ValueError,KeyError,TypeError) as exc:
        failures.append('boundary LIST: '+str(exc))
    eq = None
    try:
        if equivalence:
            verify_equivalence(ship, folder, raw)
        path = folder/'equivalence.json'
        eq = read(path)
        if (eq.get('passed') is not True or eq.get('fresh_numeric_equal') is not True
                or eq.get('physical_inputs') != raw['physical_inputs']
                or eq.get('response_identity') != response):
            raise ValueError('missing passing current-epoch numerical equivalence')
        evidence.append(pin(path))
    except (OSError,ValueError,KeyError,TypeError,AssertionError) as exc:
        failures.append('equivalence: '+str(exc))
    if live:
        try:
            from stowage.vessel.fleet import load_ship_params
            if _driver().snapshot(ship, load_ship_params(ship)) != raw['physical_inputs']:
                raise ValueError('current live inputs differ from measurements')
        except (OSError,ValueError,KeyError,TypeError) as exc:
            failures.append('live epoch: '+str(exc))
    if len(cases) != 6:
        return None, failures, rows
    count = len(cases['train','light']['station_x_m'])
    stations = cases['train','light']['station_x_m']
    for key, record in cases.items():
        if record['station_x_m'] != stations:
            failures.append(str(key)+': strength station mismatch')
    if template is not None and not np.array_equal(stations,template.station_x_m):
        failures.append('strength stations differ from coefficient cache')
    train = [cases['train',level] for level in LEVELS]
    holdout = [cases['holdout',level] for level in LEVELS]
    enrichment=[]
    targeted=[]
    enrichment_populations=set()
    for level in LEVELS:
        for path in sorted(folder.glob(f'enrichment*-{level}.json')):
            try:
                record=read(path)
                # Retain the original phase and observations. A former failed
                # holdout becomes calibration evidence, never a retroactive pass.
                validate_observation(record,raw,ship,level,'holdout')
                population=(record['seed'],level)
                if population in enrichment_populations:
                    raise ValueError(f'duplicate enrichment seed/case population: {population}')
                enrichment_populations.add(population)
                if record['station_x_m']!=stations:
                    raise ValueError('strength station mismatch')
                if production_minimums and record['samples']<64:
                    raise ValueError('production enrichment requires64 original holdout samples')
                enrichment.append(record)
                evidence.append({**pin(path),
                    'role':'former holdout retained unchanged; now calibration enrichment',
                    'original_phase':record['phase'],'original_seed':record['seed']})
            except (OSError,ValueError,KeyError,TypeError) as exc:
                failures.append(f'enrichment/{path.name}: {exc}')
        for path in sorted(folder.glob(f'targeted*-{level}.json')):
            try:
                record=read(path)
                validate_observation(record,raw,ship,level,'train')
                if not str(record.get('sampling_kind','')).startswith('targeted calibration:'):
                    raise ValueError('targeted evidence lacks an explicit calibration sampling description')
                if record.get('independent_holdout') is not False:
                    raise ValueError('targeted calibration must not claim independent holdout status')
                population=(record['seed'],level)
                if population in enrichment_populations:
                    raise ValueError(f'duplicate enrichment seed/case population: {population}')
                enrichment_populations.add(population)
                if record['station_x_m']!=stations:
                    raise ValueError('strength station mismatch')
                if production_minimums and record['samples']<64:
                    raise ValueError('production targeted calibration requires64 samples')
                sampler_pin=validate_sampler_identity(record)
                if sampler_pin is not None:
                    evidence.append(sampler_pin)
                targeted.append(record)
                evidence.append({**pin(path),'role':'targeted frontier calibration',
                    'original_phase':record['phase'],'original_seed':record['seed']})
            except (OSError,ValueError,KeyError,TypeError) as exc:
                failures.append(f'targeted/{path.name}: {exc}')
    if first_failure_report is not None:
        try:
            evidence.append({**pin(first_failure_report),'role':'archived original qualification failure'})
        except OSError as exc:
            failures.append('first failure evidence: '+str(exc))
    populations=train+enrichment+targeted
    physical = merge_envelopes([r['diagnostics']['signed_errors'] for r in populations],count)
    transfer = merge_envelopes([r['reference_transfer'] for r in populations],count)
    reference_allowance=None
    if live or reference_error_report is not None:
        try:
            reference_allowance=reference_gm_floor(ship,raw,
                report_path=reference_error_report or REFERENCE_REPORT,
                gm_min_m=template.gm_min_m if template is not None else .15)
            evidence.extend(reference_allowance['evidence'])
            for side in SIDES:
                transfer[side]['gm_m']=max(transfer[side]['gm_m'],reference_allowance['gm_m'])
        except (OSError,ValueError,KeyError,TypeError) as exc:
            failures.append('qualified reference GM allowance: '+str(exc))
    measured = merge_envelopes([physical,diag['signed_errors']],count)
    observed = merge_envelopes([r['diagnostics']['signed_errors'] for r in holdout],count)
    qualification = None
    try:
        train_seeds = {r['seed'] for r in train}
        all_calibration_seeds = {r['seed'] for r in populations}
        if type(diag.get('seed')) is int:
            all_calibration_seeds.add(diag['seed'])
        holdout_seeds = {r['seed'] for r in holdout}
        if len(train_seeds) != 1 or len(holdout_seeds) != 1:
            raise ValueError('one recorded seed per independent population required')
        if holdout_seeds.intersection(all_calibration_seeds):
            raise ValueError('final holdout seed overlaps calibration enrichment seeds')
        qualification = qualify_holdout(measured,transfer,observed,station_x_m=stations,
            calibration_seed=next(iter(train_seeds)),holdout_seed=next(iter(holdout_seeds)),
            calibration_samples=sum(r['samples'] for r in populations),
            holdout_samples=sum(r['samples'] for r in holdout))
        qualification['calibration_seeds']=sorted(all_calibration_seeds)
        qualification['calibration_populations']=[
            dict(role='general geometry-family calibration',seed=diag.get('seed'),
                 samples=diag['samples'])]+[
            dict(role=('physical training' if r in train else
                       'targeted frontier calibration' if r in targeted else
                       'retained former holdout enrichment'),
                 level=r['level'],seed=r['seed'],samples=r['samples']) for r in populations]
    except ValueError as exc:
        failures.append(str(exc))
    if boundary is not None and template is not None:
        try:
            policy._boundary_list(boundary,template,response=response,
                reference=diag['reference_identity'],domain=diag['family_domain'])
        except (ValueError,KeyError,TypeError) as exc:
            failures.append('boundary LIST: '+str(exc))
    # Captured production comparisons are an independent dispatch check, rather
    # than a claimed percentile population. Check the actual affine/public gap.
    # LIST's operating decision is separately measured in boundary moment units.
    for record in holdout:
        public = record.get('public_checker_comparison') or {}
        unsafe = public.get('affine_solver_unsafe')
        if not isinstance(unsafe,dict):
            failures.append(record['level']+': missing public signed residuals')
            continue
        for side in SIDES:
            for metric,value in unsafe.get(side,{}).items():
                if metric == 'list_rad' and boundary is not None:
                    continue
                reserve=1.1*(np.asarray(measured[side][metric])+transfer[side][metric])
                excess=np.asarray(value)-reserve
                if not np.isfinite(excess).all() or np.any(excess>0):
                    failures.append(f"{record['level']}: public {side}/{metric} excess={np.max(excess):.12g}")
    entry = dict(reference_identity=diag['reference_identity'],domain=diag['family_domain'],
        policy_application_identity=policy.policy_application_identity(),
        station_x_m=stations,physical_unsafe=physical,reference_transfer=transfer,
        evidence=evidence,equivalence=eq,holdout_qualification=qualification)
    if boundary is not None:
        entry['boundary_list'] = boundary
    if reference_allowance is not None:
        entry['reference_crossing_allowance']=reference_allowance
    if enrichment:
        entry['calibration_enrichment']=dict(
            seeds=sorted({r['seed'] for r in enrichment}),
            samples=sum(r['samples'] for r in enrichment),
            original_phases_retained=True,
            warning='Former holdout failures remain failures; only the new untouched seed qualifies.')
    if targeted:
        entry['targeted_calibration']=dict(seeds=sorted({r['seed'] for r in targeted}),
            samples=sum(r['samples'] for r in targeted),
            sampling_kinds=sorted({r['sampling_kind'] for r in targeted}),
            independent_holdout=False)
    if include_necessary:
        try:
            spec=importlib.util.spec_from_file_location('linear_necessary_qualification', HERE/'necessary.py')
            helper=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)
            entry['necessary']=helper.build(sys.modules[__name__],folder,raw,populations,holdout,
                boundary,template,reference_allowance,edge_folder=necessary_edge_folder,
                calibration_enrichment_folders=necessary_calibration_enrichment_folders)
            # Shared/inward evidence is independent of the outer-only builder
            # and edge campaign. Runtime necessary calls validate their own pins.
            entry['conservative_evidence']=deepcopy(entry['evidence'])
            entry['necessary_evidence']=[pin(HERE/'necessary.py'),
                *entry['necessary']['edge_evidence']]
        except (OSError,ValueError,KeyError,TypeError) as exc:
            failures.append('necessary qualification: '+str(exc))
    return (response,entry), failures, rows


def markdown(rows):
    output = ['# Current-epoch independent qualification','',
        'Unsafe values are maximum / 99th percentile; SF/BM take the largest station. GM safe bias is separate.', '',
        '| Ship | Cargo | GM unsafe max m | GM unsafe p99 m | GM safe gap max m | GM safe gap p99 m | Trim unsafe m | LIST unsafe rad | Draught unsafe m | SF unsafe N | BM unsafe Nm |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in rows:
        def pair(metric):
            values=row[metric]
            return f"{values['unsafe_max']:.6g} / {values['unsafe_p99']:.6g}"
        gm=row['gm_m']
        output.append(f"| {row['ship']} | {row['level']} | {gm['unsafe_max']:.6g} | "
            f"{gm['unsafe_p99']:.6g} | {gm['conservatism_max']:.6g} | "
            f"{gm['conservatism_p99']:.6g} | {pair('trim_m')} | {pair('list_rad')} | "
            f"{pair('draught_m')} | {pair('sf_n')} | {pair('bm_nm')} |")
    return '\n'.join(output)+'\n'


def main():
    from stowage.linear.margin_policy import _BANK_PATH, _evidence, policy_application_identity
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--bank',type=Path,default=_BANK_PATH)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--reference-error-report',type=Path,default=REFERENCE_REPORT)
    parser.add_argument('--first-failure-report',type=Path)
    parser.add_argument('--include-necessary',action='store_true',
        help='Independently qualify D49 outer reserves from archived opposite residuals')
    parser.add_argument('--necessary-edge-input',type=Path,
        help='Separate immutable edge campaign root; defaults to --input')
    parser.add_argument('--necessary-calibration-enrichment',type=Path,nargs='+',default=[],
        help='Explicit former v1/v2 edge holdout campaign roots reused as calibration only; original records remain unchanged')
    parser.add_argument('--verify-equivalence',action='store_true',
        help='Numerical rebuilds: run only under an admitted compute reservation')
    args=parser.parse_args()
    if args.necessary_calibration_enrichment and not args.include_necessary:
        parser.error('--necessary-calibration-enrichment requires --include-necessary')
    output=args.output or args.input/'promotion'
    output.mkdir(parents=True,exist_ok=True)
    before=sha(args.bank)
    bank=read(args.bank)
    failures,rows,updates={},{},{}
    for ship in SHIPS:
        try:
            entry, issues, table=build_ship(ship,args.input/ship,
                equivalence=args.verify_equivalence,reference_error_report=args.reference_error_report,
                production_minimums=args.apply,first_failure_report=args.first_failure_report,
                include_necessary=args.include_necessary,
                necessary_edge_folder=args.necessary_edge_input/ship if args.necessary_edge_input else None,
                necessary_calibration_enrichment_folders=[root/ship for root in args.necessary_calibration_enrichment])
            failures[ship]=issues
            rows[ship]=table
            if entry is not None:
                updates[entry[0]]=entry[1]
        except (OSError,ValueError,KeyError,TypeError) as exc:
            failures[ship]=[str(exc)]
            rows[ship]=[]
    passed=not any(failures.values()) and len(updates)==3 and sum(map(len,rows.values()))==9
    candidate=json.loads(json.dumps(bank))
    candidate['entries'].update(updates)
    report=dict(passed=passed,applied=False,failures=failures,
        necessary_edge_input=str(args.necessary_edge_input or args.input) if args.include_necessary else None,
        necessary_calibration_enrichment=[str(root) for root in args.necessary_calibration_enrichment],
        previous_bank_sha256=before,response_identities=sorted(updates),
        qualification_table=[row for ship in SHIPS for row in rows[ship]],
        operational_list_gate='independently qualified boundary mass moments; angle errors reported separately',
        warning='Finite empirical qualification; exact checker remains mandatory.')
    write(output/'candidate-bank.json',candidate)
    if args.apply:
        if not passed:
            report['apply_rejected']='qualification incomplete; production bank unchanged'
        elif sha(args.bank)!=before:
            report['apply_rejected']='production bank changed during qualification'
            report['passed']=False
        else:
            # Recheck evidence and all live inputs immediately before atomic promotion.
            try:
                for entry in updates.values():
                    _evidence(entry['evidence'])
                    _evidence(entry['boundary_list']['evidence'])
                    if 'necessary_evidence' in entry:
                        _evidence(entry['necessary_evidence'])
                for ship in SHIPS:
                    _,issues,_=build_ship(ship,args.input/ship,live=True,
                        reference_error_report=args.reference_error_report,production_minimums=True,
                        first_failure_report=args.first_failure_report,include_necessary=args.include_necessary,
                        necessary_edge_folder=args.necessary_edge_input/ship if args.necessary_edge_input else None,
                        necessary_calibration_enrichment_folders=[root/ship for root in args.necessary_calibration_enrichment])
                    if issues:
                        raise ValueError('promotion final guard: '+str(issues))
                if sha(args.bank)!=before:
                    raise ValueError('production bank changed before replacement')
                application = policy_application_identity()
                if any(entry.get('policy_application_identity') != application
                       for entry in updates.values()):
                    raise ValueError('policy application source changed before replacement')
                write(args.bank,candidate)
                report['applied']=True
                report['promoted_bank_sha256']=sha(args.bank)
            except (OSError,ValueError,KeyError,TypeError) as exc:
                report['passed']=False
                report['apply_rejected']=str(exc)
    write(output/'report.json',report)
    (output/'qualification.md').write_text(markdown(report['qualification_table']))
    print(json.dumps(dict(passed=report['passed'],applied=report['applied'],failures=failures)))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    sys.exit(main())
