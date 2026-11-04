"""Independent positive controls and raw interval audits for Cycle 4.

No search oracle or checker wrapper is imported. JSON outputs are printed to
stdout; the executive owns artifact writes. No sealed input is admissible.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
LAB = HERE.parents[1]
LAYOUT = LAB / 'c3/c2-layout'
BENDERS = LAB / 'c3/c2-benders/results/verified-independent'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _repo_path(path):
    p = Path(path)
    p = p if p.is_absolute() else ROOT / p
    p.resolve().relative_to(ROOT)
    return p


def _plan_digest(plan):
    return hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def approved_inputs():
    # Explicit Cycle 3 TRAIN authority, never a valsplit/unseen discovery glob.
    manifest = json.loads((LAYOUT/'MANIFEST.json').read_text())
    allowed = {r['input']:r['input_sha256'] for r in manifest if r['rung'] in ('f60','f70')}
    verified = json.loads((BENDERS/'verification.json').read_text())
    allowed[verified['input']] = verified['input_sha256']
    for line in (LAB/'sets/medium12.txt').read_text().splitlines():
        if line and not line.startswith('#'):
            allowed.setdefault(line,None)
    return allowed


def load_raw(path):
    p = _repo_path(path)
    rel = p.resolve().relative_to(ROOT).as_posix()
    allowed = approved_inputs()
    if rel not in allowed:
        raise ValueError('input outside explicit approved Cycle 3 manifests')
    data = p.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if allowed[rel] is not None and allowed[rel] != sha:
        raise ValueError('input changed since Cycle 3 authority manifest')
    return json.loads(data),sha


def positive_controls():
    """Every distinct saved passing f60/f70 plan, plus the D185 witness.

    Inputs must occur in the explicit Cycle 3 manifest. Only D192 result-row
    filenames are scanned, never raw map trees, cache files or sealed sets.
    Duplicate canonical plans share one fresh check and retain every alias.
    """
    allowed = approved_inputs()
    sources = set()
    for r in json.loads((LAYOUT/'audit.json').read_text()):
        if r.get('passed'):
            sources.add(_repo_path(r['evidence']))
    for r in json.loads((LAYOUT/'ladder_comparison.json').read_text()):
        if r.get('passed'):
            sources.add(_repo_path(r['evidence']))
    for p in (LAYOUT/'results').glob('*/D192*.json'):
        sources.add(p)
    distinct = {}
    for path in sorted(sources):
        row = json.loads(path.read_text())
        if not row.get('passed') or not row.get('plan'):
            continue
        inp = row['input']
        if inp not in allowed or '/f60-' not in inp and '/f70-' not in inp:
            continue
        if row.get('input_sha256') != allowed[inp]:
            raise ValueError('saved passing row has inconsistent input identity')
        key = _plan_digest(row['plan'])
        evidence = str(path.relative_to(ROOT))
        if key in distinct:
            distinct[key]['evidence_aliases'].append(evidence)
            continue
        distinct[key] = dict(control_id=row['instance_id']+'-'+key[:12], instance_id=row['instance_id'],
            input=inp,input_sha256=allowed[inp],plan=row['plan'],plan_canonical_sha256=key,
            expected_lifts=row['lifts'],evidence_aliases=[evidence],scope='preselected passing TRAIN f60/f70')
    verified = json.loads((BENDERS/'verification.json').read_text())
    plan = json.loads((BENDERS/'plan.json').read_text())
    if digest(BENDERS/'plan.json') != verified['plan_sha256']:
        raise ValueError('D185 positive-control plan hash mismatch')
    key = _plan_digest(plan)
    distinct[key] = dict(control_id=verified['instance_id']+'-'+key[:12],instance_id=verified['instance_id'],
        input=verified['input'],input_sha256=verified['input_sha256'],plan=plan,plan_canonical_sha256=key,
        expected_lifts=verified['lifts'],evidence_aliases=[str((BENDERS/'plan.json').relative_to(ROOT))],
        scope='warm targeted passing D185 positive control')
    return sorted(distinct.values(),key=lambda r:r['control_id'])


def phase_states(instance,plan):
    """Reconstruct exactly the checker's three native D57 liquid states."""
    from stowage.checker.full import departure_condition
    from stowage.model import arrival_remaining_fraction,derive_voyage_liquid_state
    departure = departure_condition(instance,plan)
    leg = instance.voyage
    r = arrival_remaining_fraction(departure,leg,vessel=instance.vessel)
    states = {}
    for phase,fraction,identity in (('departure',1.,leg.departure),
                                   ('mid_voyage',(1.+r)/2,leg.departure),
                                   ('arrival',r,leg.arrival)):
        liquid = derive_voyage_liquid_state(departure,leg,fraction,vessel=instance.vessel)
        ship = departure.model_copy(deep=True,update={'tank_masses_kg':dict(liquid.tank_masses_kg)})
        port = instance.port if phase=='departure' else instance.port.model_copy(update={
            'locode':identity.locode,'water_density_kg_m3':identity.water_density_kg_m3,
            'max_draught_m':None,'port_rules':[]})
        context = instance.model_copy(update={'call_index':identity.call_index,'port':port,'onboard':ship})
        states[phase] = (context,ship)
    return states


def phase_metrics(report):
    """Keep qualified condition parts; legacy aggregates describe departure."""
    phases = {}
    for key,part in report.parts.items():
        if not key.startswith('condition:') or not key.endswith(':stability'):
            continue
        phase = next((s.split(': ',1)[1] for s in part.metrics if s.startswith('condition.phase: ')),None)
        if phase in phases or phase not in ('departure','mid_voyage','arrival'):
            raise ValueError('missing or ambiguous native voyage phase')
        m = dict(part.metrics)
        measured = m['area_0_flood_mrad']
        required = measured-part.margins['containership_area_0_flood_mrad']
        phases[phase] = dict(metrics=m,measured_positive_area_mrad=measured,
            required_positive_area_mrad=required,area_margin_mrad=measured-required,
            kg_physical_m=m['kg_m'],kg_virtual_m=m['kg_m']+m['fsc_m'],
            draft_mean_m=m['draught_mean_m'],draft_aft_m=m['draught_aft_m'],
            draft_fwd_m=m['draught_fwd_m'],trim_m=m['trim_m'])
    if set(phases) != {'departure','mid_voyage','arrival'}:
        raise ValueError('full checker did not retain all three phase stability reports')
    for key,part in report.parts.items():
        if not key.startswith('condition:') or not key.endswith(':lashing'):
            continue
        phase = next((s.split(': ',1)[1] for s in part.metrics if s.startswith('condition.phase: ')),None)
        if phase in phases:
            phases[phase]['securing_metrics'] = dict(part.metrics)
            phases[phase]['rod_ratio'] = part.metrics.get('max_lashing_ratio')
            phases[phase]['gm_lashing_m'] = part.metrics.get('gm_m')
    return phases


def native_sources():
    sources = {}
    for module in tuple(sys.modules.values()):
        filename = getattr(module,'__file__',None)
        if filename:
            p = Path(filename).resolve()
            if p.is_relative_to(ROOT/'src') and p.is_file():
                sources[str(p.relative_to(ROOT))] = digest(p)
    return sources


def check_control(control):
    """Fresh ordinary full check; no numerical search or monkeypatches."""
    from stowage.checker import check
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from stowage.moves import apply_plan
    from stowage.frozen import active_stubs
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    if active_stubs():
        raise ValueError('active frozen overrides invalidate independent control check')
    started = monotonic()
    raw,sha = load_raw(control['input'])
    instance = load_instance(control['input'])
    plan = MovePlan.model_validate(control['plan'])
    replay = apply_plan(instance,plan)
    aboard = {p.container_id for p in replay.final_state.ship.placements}
    report = check(instance,plan)
    passed = bool(report.passed and replay.legal and set(raw['load_list']) <= aboard
                  and report.count.loads==len(instance.load_list)
                  and report.count.weighted_nonproductive==control['expected_lifts'])
    result = {k:v for k,v in control.items() if k!='plan'}
    result.update(input_sha256=sha,replay_legal=replay.legal,full_checker_passed=report.passed,
        accepted=passed,all_requested_aboard=set(raw['load_list'])<=aboard,
        requested_boxes=len(instance.load_list),loads=report.count.loads,
        lifts=report.count.weighted_nonproductive,violations=len(report.violations),
        rule_freeze_v1=rule_freeze_v1_enabled(),elapsed_s=monotonic()-started,
        phases=phase_metrics(report) if report.passed else None,
        full_report=report.model_dump(mode='json'),native_source_sha256=native_sources())
    return result


def enrich_control(control,observation):
    """Add raw cargo shares and exact native phase tank states; no recheck."""
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from stowage.moves import apply_plan
    raw,_ = load_raw(control['input'])
    instance = load_instance(control['input'])
    plan = MovePlan.model_validate(control['plan'])
    boxes = {b['id']:b for b in raw['containers']}
    replay = apply_plan(instance,plan)
    counts,masses = {},{}
    deck_mass = 0.
    for p in replay.final_state.ship.placements:
        if int(p.slot[-2:])<80:
            continue
        b = boxes[p.container_id]
        bay = int(p.slot[:3])
        bays = [bay] if bay%2 else [bay-1,bay+1]
        deck_mass += b['gross_mass_kg']
        for odd in bays:
            counts[str(odd)] = counts.get(str(odd),0.)+1/len(bays)
            masses[str(odd)] = masses.get(str(odd),0.)+b['gross_mass_kg']/len(bays)
    observation['cargo'] = dict(deck_mass_kg=deck_mass,deck_boxes_by_odd_bay=counts,
                                deck_mass_by_odd_bay_kg=masses)
    states = phase_states(instance,plan)
    for phase,(context,state) in states.items():
        row = observation['phases'][phase]
        row['tank_masses_kg'] = dict(state.tank_masses_kg)
        row['water_density_kg_m3'] = context.port.water_density_kg_m3
        row['mass_kg'] = row['metrics']['displacement_kg']
    return observation


def exact_interval(value):
    if len(value)!=2:
        raise ValueError('interval requires two endpoints')
    lo,hi = (Fraction(str(v)) for v in value)
    if lo>hi:
        raise ValueError('inverted interval')
    return lo,hi


def contains(interval,value):
    lo,hi = exact_interval(interval)
    return lo <= Fraction(str(value)) <= hi


def audit_area_enclosure(upper,measured,*,allowance=0.):
    """Independent observed-area check; no allowance may authorize a proof."""
    if not all(math.isfinite(v) for v in (upper,measured,allowance)) or allowance<0:
        raise ValueError('nonfinite area or negative allowance')
    gap = Fraction(str(upper))-Fraction(str(measured))
    return dict(encloses=gap>=-Fraction(str(allowance)),
                upper_area_mrad=upper,measured_area_mrad=measured,spare_mrad=float(gap),
                allowance=allowance,physical_proof=False)


def audit_window_containment(window,observation):
    """Ensure a surviving map window does not exclude a known legal control.

    Check the map-level intervals only; a union of spatial bins is handled by
    the executive. This is a regression check, not a theorem for other stows.
    """
    if window['instance_id'] != observation['instance_id']:
        raise ValueError('window/control map identity mismatch')
    violations,checked = [],[]
    for phase,limits in window.get('phase_windows',{}).items():
        measured = observation['phases'][phase]
        values = dict(mass_kg=measured['mass_kg'],physical_kg_m=measured['kg_physical_m'],
                      virtual_kg_m=measured['kg_virtual_m'],mean_draught_m=measured['draft_mean_m'],
                      upright_trim_m=measured['trim_m'],gm_lashing_m=measured.get('gm_lashing_m'))
        if 'lcg_m' in measured:
            values['lcg_m']=measured['lcg_m']
        for key,value in values.items():
            if key in limits and value is not None:
                checked.append(phase+'.'+key)
                if not contains(limits[key],value):
                    violations.append(dict(phase=phase,key=key,value=value,interval=limits[key]))
        if limits.get('area_upper_mrad') is not None:
            checked.append(phase+'.area_upper_mrad')
            if not audit_area_enclosure(limits['area_upper_mrad'],measured['measured_positive_area_mrad'])['encloses']:
                violations.append(dict(phase=phase,key='area_upper_mrad',value=measured['measured_positive_area_mrad'],
                                       upper=limits['area_upper_mrad']))
    cargo = window.get('cargo',{})
    if 'deck_mass_kg' in cargo:
        checked.append('cargo.deck_mass_kg')
        if not contains(cargo['deck_mass_kg'],observation['cargo']['deck_mass_kg']):
            violations.append(dict(key='cargo.deck_mass_kg',value=observation['cargo']['deck_mass_kg'],
                                   interval=cargo['deck_mass_kg']))
    for bay,limits in cargo.get('by_bay',{}).items():
        for key,source in (('count','deck_boxes_by_odd_bay'),('mass_kg','deck_mass_by_odd_bay_kg')):
            if key not in limits:
                continue
            value=observation['cargo'][source].get(str(bay),0.)
            checked.append('cargo.by_bay.'+str(bay)+'.'+key)
            if not contains(limits[key],value):
                violations.append(dict(key='cargo.by_bay.'+str(bay)+'.'+key,value=value,interval=limits[key]))
    for tid,interval in window.get('tanks',{}).get('shared_departure_mass_kg',{}).items():
        value=observation['phases']['departure']['tank_masses_kg'].get(tid,0.)
        checked.append('tanks.'+tid)
        if not contains(interval,value):
            violations.append(dict(key='tanks.'+tid,value=value,interval=interval))
    return dict(control_id=observation['control_id'],contained=not violations,
                violations=violations,checked=checked,physical_proof=False)


def audit_raw_mass_windows(raw,window):
    """Recompute static three-phase mass boxes from raw input, no builder.

    The comparison verifies the published *static* enclosure, before any
    independently certified coupled tightening. It does not validate an LP
    infeasibility status or turn a witness into a physical feasibility proof.
    """
    Q=lambda v:Fraction(float(v))
    h=raw['vessel']['hull']; boxes={b['id']:b for b in raw['containers']}
    base=Q(h['lightship_mass_kg'])
    base+=sum((Q(load['mass_kg']) for load in raw['vessel']['fixed_loads']),Fraction())
    base+=sum((Q(boxes[bid]['gross_mass_kg']) for bid in raw['load_list']),Fraction())
    leg=raw['voyage']
    if leg.get('consumption') is not None or leg.get('ballast_operations'):
        return dict(verified=False,reason='explicit consumption/at-sea ballast unsupported by static audit')
    remaining=raw['vessel']['voyage_policy']['default_arrival_remaining_fraction']
    consumables={'fuel_oil','diesel_oil','lube_oil','fresh_water','stores'}
    policy=raw.get('rule_overrides',{}).get('iscode.loading.displacement',{})
    manual_lo=Q(policy.get('minimum',h['min_displacement_kg']))
    manual_hi=Q(policy.get('maximum',h['max_displacement_kg']))
    manual_guard=max(Q(1e-5),Q(2e-10)*manual_hi)
    observations=[]
    for phase,r in (('departure',1.),('mid_voyage',(1.+remaining)/2),('arrival',remaining)):
        lo=hi=base
        for tank in raw['vessel']['tanks']:
            factor=Q(r if tank['kind'] in consumables else 1.)
            cap=Q(tank['capacity_m3'])*Q(tank['content_density_kg_m3'])
            if tank['adjustable']:
                hi+=factor*cap
            else:
                value=Q(raw['onboard']['tank_masses_kg'].get(tank['id'],0.))*factor
                lo+=value;hi+=value
        lo=max(lo-Q(1e-5),manual_lo-manual_guard)
        hi=min(hi+Q(1e-5),manual_hi+manual_guard)
        published=window['phase_windows'][phase]['mass_kg']
        # Native raw products may round once before the model retains them.
        # This recorded check allowance cannot authorize an infeasibility proof.
        rounding=Q(1e-5)
        encloses=Q(published[0])<=lo+rounding and Q(published[1])>=hi-rounding
        observations.append(dict(phase=phase,raw_mass_kg=[float(lo),float(hi)],published=published,
                                 encloses=encloses,comparison_allowance_kg=float(rounding)))
    return dict(verified=all(r['encloses'] for r in observations),phases=observations,
                arithmetic='exact rationals over raw decoded binary floats',physical_proof=False)


def audit_support_certificate(matrix,certificate):
    """Independently verify finite-box Lagrangian support, without a solver.

    Ax<=b has nonpositive multiplier y, so c*x >= y*b +
    min_{lo<=x<=hi}(c-y*A)*x. Equality multipliers have either sign.
    This proves a bound only for the serialized matrix; physical outwardness
    of its rows remains a separate source-and-geometry obligation.
    """
    lower,upper=matrix['lower'],matrix['upper']
    if len(lower)!=len(upper):
        raise ValueError('bound dimensions differ')
    n=len(lower)
    def q(value):
        if isinstance(value,bool) or not math.isfinite(float(value)):
            raise ValueError('nonfinite certificate value')
        return Fraction(value)
    def index(j):
        if not isinstance(j,int) or isinstance(j,bool) or not 0<=j<n:
            raise ValueError('certificate index outside variable domain')
        return j
    residual=[Fraction() for _ in lower]
    seen=set()
    for j,value in certificate['objective']:
        j=index(j)
        if j in seen:
            raise ValueError('duplicate objective coefficient')
        seen.add(j);residual[j]=q(value)
    constant=Fraction()
    for key,multiplier_key,inequality in (('ub_rows','ub_multipliers',True),('eq_rows','eq_multipliers',False)):
        if len(matrix[key])!=len(certificate[multiplier_key]):
            raise ValueError('row/multiplier dimensions differ')
        for row,weight in zip(matrix[key],certificate[multiplier_key]):
            y=q(weight)
            if inequality and y>0:
                raise ValueError('positive inequality multiplier')
            constant+=y*q(row['rhs'])
            for j,value in row['coefficients']:
                residual[index(j)]-=y*q(value)
    bound=constant
    for r,lo,hi in zip(residual,lower,upper):
        lo,hi=q(lo),q(hi)
        if lo>hi:
            raise ValueError('inverted variable bounds')
        bound+=min(r*lo,r*hi)
    rounded=float(bound)
    if Fraction(rounded)>bound:
        rounded=math.nextafter(rounded,-math.inf)
    return dict(verified=True,lower_bound=rounded,exact_numerator=str(bound.numerator),
                exact_denominator=str(bound.denominator),physical_proof=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list-controls',action='store_true')
    parser.add_argument('--control-index',type=int)
    args = parser.parse_args()
    controls = positive_controls()
    if args.list_controls:
        print(json.dumps([{k:v for k,v in r.items() if k!='plan'} for r in controls],indent=2))
    if args.control_index is not None:
        print(json.dumps(check_control(controls[args.control_index]),allow_nan=False))


if __name__=='__main__':
    main()
