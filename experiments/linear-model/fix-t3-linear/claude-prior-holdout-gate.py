"""Read-only candidate-bank residual gate on all N1 edge observations.

Previously failed v1 holdouts remain known regression observations. They are
never used here to fit a reserve or relabelled as fresh qualification. Check
the residual bounds themselves as well as LIST's actual kg m predictor; public
row inclusion is a separate audit because a residual breach far from a hard
limit need not reject that particular state.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import time

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name,path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def pin(path):
    path = Path(path).resolve()
    try:
        name = str(path.relative_to(ROOT))
    except ValueError:
        name = str(path)
    return dict(path=name,sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def normalize(necessary,promote,path,phase,raw,template):
    role = 'review-regression' if phase == 'review' else 'grid' if phase == 'train' else 'independent-edge-holdout'
    expected_phase = 'train' if phase == 'review' else phase
    record,normalized,pins = necessary.load_edge_record(promote,path,raw,template,
        phase=expected_phase,role=role)
    if necessary._digest(record.get('list_row_samples')) != necessary._digest(normalized['list_row_samples']):
        raise ValueError('edge admitted-state duplicate differs from authoritative states: '+str(path))
    return record,normalized,pins


def compare(necessary,policy,model,reserves,normalized):
    """Return every breached scalar/station bound; never change the candidate."""
    count = len(model.station_x_m)
    optimistic = policy.optimistic_envelope(normalized['diagnostics']['signed_errors'],count)
    transfer = necessary.reference_optimistic(normalized,count)
    failures, maxima = [], {}
    comparisons = 0
    for side in policy._SIDES:
        maxima[side] = {}
        for metric in policy._METRICS:
            if metric == 'gm_m' and side == 'upper_unsafe':
                continue
            observed = np.asarray(optimistic[side][metric])+transfer[side][metric]
            reserve = np.asarray(reserves[side][metric])
            if observed.shape != reserve.shape or not np.isfinite(observed).all():
                raise ValueError('invalid candidate/observed residual shape')
            excess = observed-reserve
            comparisons += int(observed.size)
            maxima[side][metric] = dict(observed_max=float(np.max(observed)),
                reserve_min=float(np.min(reserve)),max_excess=float(np.max(excess)))
            if excess.ndim:
                for index in np.flatnonzero(excess > 0):
                    failures.append(dict(side=side,metric=metric,station_x_m=float(model.station_x_m[index]),
                        observed=float(observed[index]),reserve=float(reserve[index]),excess=float(excess[index])))
            elif excess > 0:
                failures.append(dict(side=side,metric=metric,observed=float(observed),
                    reserve=float(reserve),excess=float(excess)))
    # LIST angular residuals above are diagnostics. These are the actual outer
    # LIST rows: s*My - tan(limit)*raw_affine_restoring <= kg m reserve.
    heel = min(model.max_list_rad,model.band.max_abs_heel_rad)
    moments = {side:0. for side in policy._SIDES}
    for state in normalized['list_row_samples']:
        for side,sign in (('lower_unsafe',-1.),('upper_unsafe',1.)):
            needed = max(0.,sign*float(state['transverse_moment_kgm'])
                -np.tan(heel)*float(state['affine_restoring_kgm']))
            moments[side] = max(moments[side],needed)
    for side in policy._SIDES:
        reserve = float(reserves['list_moment_kgm'][side])
        excess = moments[side]-reserve
        comparisons += 1
        maxima[side]['list_moment_kgm'] = dict(observed_max=moments[side],reserve_min=reserve,max_excess=excess)
        if excess > 0:
            failures.append(dict(side=side,metric='list_moment_kgm',observed=moments[side],
                reserve=reserve,excess=excess))
    return dict(passed=not failures,comparisons=comparisons,failures=failures,maxima=maxima)


def controlled_diagnostics(necessary,promote,path,raw,model):
    """Validate seven controlled contrasts without turning them into training."""
    record = promote.read(path)
    if (record.get('physical_inputs_start') != raw['physical_inputs']
            or record.get('physical_inputs_end') != raw['physical_inputs']
            or len(record.get('states',[])) != 7):
        raise ValueError('controlled diagnostic epoch/count mismatch')
    pins = [pin(path)]
    for name in ('diagnostic_source','sampler_source','source_review'):
        pins.append(pin(necessary._checked_pin(promote,record.get(name))))
    review_path = necessary._checked_pin(promote,record['source_review'])
    review,_,review_pins = normalize(necessary,promote,review_path,'review',raw,model)
    reference = promote.read(necessary._checked_pin(promote,review['reference_transfer_source']))
    transfer = necessary.reference_optimistic(reference,len(model.station_x_m))
    states = record['states']
    retained = sum(state['admitted'] for state in states)
    # This in-memory adapter only reuses the validator/residual arithmetic.
    # It is never written as a population, fitted, promoted or called a holdout.
    adapter = dict(kind=necessary.EDGE_KIND,complete=True,seed=0,
        physical_inputs_start=record['physical_inputs_start'],physical_inputs_end=record['physical_inputs_end'],
        physical_inputs_unchanged=True,calibration=raw['diagnostics'],
        station_x_m=np.asarray(model.station_x_m).tolist(),states=states,samples=retained,attempts=len(states),
        coverage=dict(required_strata=['controlled-diagnostic'],strata={
            'controlled-diagnostic':dict(requested=len(states),evaluated=len(states),retained=retained)}))
    return necessary.edge_population(adapter,raw,model,transfer=transfer),pins+review_pins


def pilot_diagnostics(necessary,promote,folder,raw,model,*,expected_admitted,reference_path):
    """Check all captured full-family pilot states under their original scope.

    These are prescribed diagnostic faces, not calibration or new independent
    holdouts. Saved centroid rejects remain explicit exclusions; every admitted
    state contributes all scalar/station residuals and LIST moment slack.
    """
    path = folder/'summary.json'
    record = promote.read(path)
    states,requests = record.get('states'),record.get('requests')
    if (record.get('status') != 'completed' or record.get('requested') != 8
            or record.get('processed') != 8 or not isinstance(states,list) or len(states) != 8
            or not isinstance(requests,list) or len(requests) != 8
            or record.get('admitted') != expected_admitted
            or record.get('physical_inputs_unchanged') is not True
            or record.get('physical_inputs_start') != raw['physical_inputs']
            or record.get('physical_inputs_end') != raw['physical_inputs']):
        raise ValueError('diagnostic pilot count or physical epoch mismatch')
    sources = record.get('sources')
    if not isinstance(sources,dict) or set(sources) != {'base','driver','pilot'}:
        raise ValueError('diagnostic pilot source pins missing')
    pins = [pin(path)]
    for source in sources.values():
        pins.append(pin(necessary._checked_pin(promote,source)))
    if sources['pilot'] != pin(folder.with_name(folder.name.removesuffix('-artifacts')+'.py')):
        raise ValueError('diagnostic pilot producer differs from declared artifact family')
    prepared_path = folder/'prepared.json'
    prepared = promote.read(prepared_path)
    if (prepared.get('physical_inputs') != raw['physical_inputs']
            or prepared.get('response_identity') != raw['diagnostics']['response_calibration_identity']
            or prepared.get('domain') != raw['diagnostics']['family_domain']
            or prepared.get('station_x_m') != np.asarray(model.station_x_m).tolist()
            or any(getattr(model.band,key) != value for key,value in prepared.get('band',{}).items())):
        raise ValueError('diagnostic pilot prepared family mismatch')
    pins.append(pin(prepared_path))
    admission_path = folder/'admission.json'
    admission = promote.read(admission_path)
    if admission.get('sources') != sources or len(admission.get('requests',[])) != 8:
        raise ValueError('diagnostic pilot request admission/source mismatch')
    pins.append(pin(admission_path))
    rejected = []
    for state,request,declared in zip(states,requests,admission['requests'],strict=True):
        if (state.get('request') != request
                or any(request.get(key) != value for key,value in declared.items())):
            raise ValueError('diagnostic pilot changed a prespecified request')
        state_id = state.get('state_id')
        if not isinstance(state_id,str) or Path(state_id).name != state_id:
            raise ValueError('diagnostic pilot has invalid individual state identity')
        state_path = folder/(state_id+'.json')
        if promote.read(state_path) != state:
            raise ValueError('diagnostic pilot individual state differs from summary')
        pins.append(pin(state_path))
        if state.get('admitted') is False:
            point,domain = state.get('fixed_point',{}),raw['diagnostics']['family_domain']
            inside = (domain['fixed_x_min_m'] <= point.get('x_m',np.nan) <= domain['fixed_x_max_m']
                and abs(point.get('y_m',np.nan)) <= domain['fixed_y_abs_max_m']
                and domain['kg_min_m'] <= point.get('z_m',np.nan) <= domain['kg_max_m'])
            if inside or state.get('physical_centroid') is not False or not state.get('rejection_reason'):
                raise ValueError('diagnostic pilot centroid rejection is unsupported')
            rejected.append(dict(state_id=state_id,reason=state['rejection_reason'],fixed_point=point))
    explicit_path = folder/'explicit-requests.json'
    if explicit_path.exists():
        if promote.read(explicit_path).get('requests') != requests:
            raise ValueError('diagnostic pilot explicit tank budget requests differ')
        pins.append(pin(explicit_path))
    reference = promote.read(reference_path)
    if (reference.get('phase') != 'train' or reference.get('physical_inputs_unchanged') is not True
            or reference.get('physical_inputs_start') != raw['physical_inputs']
            or reference.get('physical_inputs_end') != raw['physical_inputs']):
        raise ValueError('diagnostic pilot reference transfer is stale')
    pins.append(pin(reference_path))
    retained = sum(state.get('admitted') is True for state in states)
    if retained != expected_admitted:
        raise ValueError('diagnostic pilot retained count disagrees with saved states')
    adapter = dict(kind=necessary.EDGE_KIND,complete=True,seed=0,
        physical_inputs_start=record['physical_inputs_start'],physical_inputs_end=record['physical_inputs_end'],
        physical_inputs_unchanged=True,calibration=raw['diagnostics'],
        station_x_m=np.asarray(model.station_x_m).tolist(),states=states,samples=retained,attempts=8,
        coverage=dict(required_strata=['prescribed-diagnostic-faces'],strata={
            'prescribed-diagnostic-faces':dict(requested=8,evaluated=8,retained=retained)}))
    normalized = necessary.edge_population(adapter,raw,model,
        transfer=necessary.reference_optimistic(reference,len(model.station_x_m)))
    return normalized,pins,rejected


def archived_probe_diagnostics(root,model,reserves):
    """Report small-slice core scalar observations under their original scope.

    The probe's source epoch was not globally frozen, and its SF/BM station map
    is not an archived full-family map. Do not retag it as fresh qualification.
    The source-independent full-family review/contrasts are checked separately.
    """
    output = dict(records=0,valid_domain_records=0,covered=True,failures=[],sources=[],
        scope='Archived small-slice core scalar residuals only; not a full-family/current-epoch qualification',
        limitations='No independent reference-transfer pin or full-family SF/BM station mapping; full source fingerprints changed in some runs.')
    for folder in sorted(root.glob('claude-edge-seed*')):
        if not folder.is_dir() or not (folder/'summary.json').exists():
            continue
        summary = json.loads((folder/'summary.json').read_text())
        files = sorted(folder.glob('state-*.json'))
        source = dict(summary=pin(folder/'summary.json'),files=[pin(path) for path in files],
            original_inputs_unchanged=summary.get('inputs_unchanged'),samples=len(files),valid_domain_samples=0)
        for path in files:
            state = json.loads(path.read_text());output['records'] += 1
            point,domain,band,exact = state['point_load'],state['family_domain'],state['family_band'],state['exact']
            valid = (domain['fixed_x_min_m'] <= point['x_m'] <= domain['fixed_x_max_m']
                and abs(point['y_m']) <= domain['fixed_y_abs_max_m']
                and domain['kg_min_m'] <= point['z_m'] <= domain['kg_max_m']
                and band['displacement_min_kg'] <= exact['displacement_kg'] <= band['displacement_max_kg']
                and band['draught_min_m'] <= exact['draught_m'] <= band['draught_max_m']
                and abs(exact['trim_m']) <= band['max_abs_trim_m']
                and abs(exact['list_rad']) <= band['max_abs_heel_rad'])
            if not valid:
                continue
            output['valid_domain_records'] += 1;source['valid_domain_samples'] += 1
            affine = state['affine']
            residuals = {metric:float(affine[metric])-float(exact[metric])
                         for metric in ('gm_m','trim_m','draught_m')}
            residuals['draught_aft_m'] = residuals['draught_m']+.5*residuals['trim_m']
            residuals['draught_forward_m'] = residuals['draught_m']-.5*residuals['trim_m']
            for metric,residual in residuals.items():
                for side,sign in (('lower_unsafe',-1.),('upper_unsafe',1.)):
                    if metric == 'gm_m' and side == 'upper_unsafe':
                        continue
                    observed = max(0.,sign*residual);reserve = float(reserves[side][metric])
                    if observed > reserve:
                        output['failures'].append(dict(source=pin(path),metric=metric,side=side,
                            observed=observed,reserve=reserve,excess=observed-reserve))
        output['sources'].append(source)
    output['covered'] = not output['failures']
    return output


def main():
    from stowage.linear import margin_policy as policy
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bank',type=Path,required=True)
    parser.add_argument('--prepared',type=Path,default=HERE/'epoch-claude')
    parser.add_argument('--edge-input',type=Path,nargs='+',default=[
        HERE/'epoch-claude',HERE/'epoch-claude-v2',HERE/'epoch-claude-v3'])
    parser.add_argument('--output',type=Path,default=HERE/'claude-prior-holdout-gate.json')
    parser.add_argument('--controlled-diagnostics',type=Path,default=HERE/'claude-edge-cause.json')
    parser.add_argument('--probe-root',type=Path,default=HERE)
    args = parser.parse_args()
    start = time.monotonic()
    before_bank = pin(args.bank)
    bank = json.loads(args.bank.read_text())
    necessary = module('n1_all_observations_necessary',HERE.parent/'epoch_requalification/necessary.py')
    promote = module('n1_all_observations_promote',HERE.parent/'epoch_requalification/promote.py')
    driver = promote._driver()
    cases = []
    archived_probes = None
    for ship in promote.SHIPS:
        family,instance,_,diagnostics,before = driver.prepare(ship,args.prepared)
        raw = promote.read(args.prepared/ship/'raw.json')
        certificate = policy.qualify_policy(family,instance,diagnostics,bank=bank)
        model = replace(family.template,measurement_diagnostics=dict(diagnostics,margin_policy=certificate))
        reserves = policy.default_reserves(model,direction='necessary')
        saw_review = False
        for edge_root in args.edge_input:
            sources = [(phase,edge_root/ship/f'necessary-edges-{phase}.json') for phase in ('train','holdout')]
            if (edge_root/ship/'necessary-review.json').exists():
                sources.append(('review',edge_root/ship/'necessary-review.json'))
            for phase,path in sources:
                record,normalized,pins = normalize(necessary,promote,path,phase,raw,model)
                checked = compare(necessary,policy,model,reserves,normalized)
                enriched = any(item.get('source') == pin(path) for item in
                    certificate['necessary'].get('calibration_enrichment',[]))
                cases.append(dict(ship=ship,campaign=str(edge_root),phase=phase,seed=record['seed'],
                    samples=normalized['samples'],attempts=record['attempts'],source_pins=pins,
                    original_source_phase=record['phase'],
                    statistical_use=('explicit former holdout calibration enrichment'
                        if enriched else 'fresh independent edge holdout' if phase == 'holdout'
                        and record['seed'] == certificate['necessary']['edge_holdout_qualification']['holdout_seed']
                        else 'archived observed regression'),
                    policy_certificate_sha256=certificate['sha256'],**checked))
                saw_review |= phase == 'review'
        if ship == 'feeder_500' and not saw_review:
            raise ValueError('frozen N1 review point omitted from residual gate')
        if ship == 'feeder_500':
            normalized,pins = controlled_diagnostics(necessary,promote,args.controlled_diagnostics,raw,model)
            cases.append(dict(ship=ship,campaign='controlled-diagnostic',phase='diagnostic',
                samples=normalized['samples'],attempts=7,source_pins=pins,
                scope='Known controlled regressions only; not training or independent qualification',
                **compare(necessary,policy,model,reserves,normalized)))
            reserve = float(reserves['upper_unsafe']['draught_aft_m'])
            cases.append(dict(ship=ship,campaign='review-reported-scalar',phase='diagnostic',samples=0,
                comparisons=1,reported_aft_residual_m=.6864,reserve_m=reserve,passed=reserve >= .6864,
                failures=[] if reserve >= .6864 else [dict(metric='draught_aft_m',side='upper_unsafe',
                    observed=.6864,reserve=reserve,excess=.6864-reserve)],
                reproduction_limitation='Original reported aft witness not located; scalar coverage check only',
                source_pins=[pin(ROOT/'experiments/review/fixes/rev-claude-physics.md')]))
            archived_probes = archived_probe_diagnostics(args.probe_root,model,reserves)
        if ship == 'feeder_1700':
            for name,expected_admitted in (('claude-face-pilot-artifacts',8),
                    ('claude-face-budget-pilot-artifacts',4)):
                normalized,pins,rejected = pilot_diagnostics(necessary,promote,HERE/name,raw,model,
                    expected_admitted=expected_admitted,
                    reference_path=args.prepared/ship/'train-light.json')
                cases.append(dict(ship=ship,campaign=name,phase='diagnostic',
                    samples=normalized['samples'],attempts=8,source_pins=pins,
                    excluded_centroid_states=rejected,
                    scope='Prescribed diagnostic faces only; not training or independent qualification',
                    **compare(necessary,policy,model,reserves,normalized)))
        if driver.snapshot(ship,family.parameters) != before:
            raise ValueError('numerical inputs changed during all-observation residual audit')
    if pin(args.bank) != before_bank:
        raise ValueError('candidate bank changed during all-observation residual audit')
    result = dict(passed=all(case['passed'] for case in cases) and archived_probes['covered'],
        cases=cases,bank=before_bank,archived_probe_diagnostics=archived_probes,
        samples=sum(case['samples'] for case in cases),
        comparisons=sum(case['comparisons'] for case in cases),elapsed_s=time.monotonic()-start,
        previously_failed_holdouts=dict(seeds=[54932,54934],
            status='Immutable former v1/v2 holds; explicit builder enrichment is recorded per case and never called fresh qualification. This read-only audit fits no reserve.'),
        warning='Finite observed residual coverage; public necessary-row inclusion and full-checker certification remain separate.')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    temporary = args.output.with_suffix('.tmp')
    temporary.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    temporary.replace(args.output)
    print(json.dumps(dict(passed=result['passed'],samples=result['samples'],comparisons=result['comparisons'],
        elapsed_s=result['elapsed_s'],bank=before_bank,archived_probe_records=archived_probes['records'],
        archived_valid_domain_records=archived_probes['valid_domain_records'],
        archived_scalar_residuals_covered=archived_probes['covered'])))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
