"""Independent raw-JSON checks for aggregate witnesses and saved legal plans.

This module does not import the relaxation builder. A successful aggregate
audit is not a stowage feasibility verdict; omitted physical rules still apply.
All numerical comparisons below are witness residual checks, never proofs of
infeasibility. No solver status is promoted to a mathematical certificate.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from fractions import Fraction
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
from time import monotonic
import tomllib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]


def approved_inputs():
    paths = set()
    for name in ("medium12", "quick_medium"):
        for line in (ROOT / "experiments/mvp/frontier/lab/sets" / (name + ".txt")).read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                paths.add(line.strip())
    return paths


def load_raw(path):
    p = Path(path)
    if p.is_absolute():
        p = p.resolve().relative_to(ROOT)
    if p.as_posix() not in approved_inputs():
        raise ValueError("Input is outside the fixed approved medium inputs")
    data = (ROOT / p).read_bytes()
    return json.loads(data), hashlib.sha256(data).hexdigest()


def _q(value):
    """Exact rational value of the JSON's decoded binary float."""
    if isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError("nonfinite or Boolean numerical witness")
    return Fraction(value)


def raw_census(raw):
    boxes = {b["id"]: b for b in raw["containers"]}
    cargo = [boxes[bid] for bid in raw["load_list"]]
    if len(set(raw["load_list"])) != len(cargo):
        raise ValueError("duplicate load-list id")
    stacks = raw["vessel"]["stacks"]
    return dict(boxes=len(cargo), cells=sum(1 if b["length_ft"] == 20 else 2 for b in cargo),
                mass_kg=float(sum((_q(b["gross_mass_kg"]) for b in cargo), Fraction())),
                hold_cells=sum(len(s["cell_ids"]) for s in stacks if not s["on_deck"]),
                deck_cells=sum(len(s["cell_ids"]) for s in stacks if s["on_deck"]))


@lru_cache(maxsize=4)
def _raw_tank_geometry(reference):
    path = Path(reference.split('#')[0])
    if path.parent.as_posix() != 'params/fleet' or path.suffix != '.toml':
        raise ValueError('Unsupported fleet geometry reference')
    params = tomllib.loads((ROOT / path).read_text())
    return {t['id']: t.get('geometry', t) for t in params['tanks']}


@lru_cache(maxsize=4)
def _checker_cb(reference, lpp, beam, scantling):
    # Reuse only the unchanged checker's normative scantling hull integral.
    # The acceleration/force inequalities below are derived independently.
    from stowage.checker.visibility import _geometry
    geometry, _ = _geometry(Path(reference.split('#')[0]).stem)
    return geometry.submerged(scantling, scantling).volume_m3/(lpp*beam*scantling)


def _securing_raw(raw, box, *, deck, support_lower, gm_lower):
    """Independent, deliberately slightly weaker own/aggregate motion rows."""
    h = raw['vessel']['hull']
    L, B, depth = h['lpp_m'], h['beam_m'], h['depth_m']
    cb = _checker_cb(h['hull_offsets_ref'], L, B, h['scantling_draught_m'])
    a0 = min(.52, (1.58-.47*cb)*(2.4/math.sqrt(L)+34/L-600/L**2))
    surge, sway = .2*a0*9.81, .3*a0*9.81
    period = min(30., 2.3*math.pi*.35*B/math.sqrt(9.81*gm_lower)) if gm_lower > 0 else 30.
    phi = 9000*(1.25-.025*period)*(1. if h['bilge_keel'] else 1.2)/((B+75)*math.pi)
    if B < 32.2:
        phi = max(phi, 25.)
    phi = math.radians(phi)
    theta = math.radians(min(20.,1350*L**(-.94)*(1+(2.57/math.sqrt(9.81*L))**1.2)))
    oog = box.get('oog') or {}
    height = box['height_m']+oog.get('over_height_m',0.)
    lever = support_lower+.45*height-depth/2
    al, at = .7*abs(surge), abs(sway)
    if lever >= 0:
        al = .7*math.hypot(surge,math.sqrt(max(L,110.)/325)*9.81*(theta-theta**3/6))
        at = math.hypot(sway,9.81*(phi-phi**3/6)+phi*(2*math.pi/period)**2*lever)
    # The larger guard keeps this independently evaluated floating expression
    # below the rational/Taylor builder coefficients; no proof uses this guard.
    al *= 1.-1e-7
    at *= 1.-1e-7
    length = {20:6.058,40:12.192,45:13.716}[box['length_ft']]
    expanded_length = length+oog.get('over_length_fore_m',0.)+oog.get('over_length_aft_m',0.)
    expanded_width = 2.438+oog.get('over_width_port_m',0.)+oog.get('over_width_starboard_m',0.)
    dx = (oog.get('over_length_fore_m',0.)-oog.get('over_length_aft_m',0.))/2
    securing_length = 12.192 if box['length_ft'] == 45 else length
    shares = [abs(.5-dx/securing_length),abs(.5+dx/securing_length)]
    m = max(box['gross_mass_kg'],2500. if box['length_ft']==20 else 3500.)
    if deck:
        wt = max(15700. if box['length_ft']==20 else 31600., expanded_length*height*1000.)
        wl = max(6400., expanded_width*height*1000.)
        trans, longitudinal = (m*at+wt)*(1.-1e-7), (m*al+wl)*(1.-1e-7)
        own = math.hypot(max(shares)*trans/2,longitudinal/4)/(.85*210000.)
        coefficient = trans*min(1.,2*min(shares))
    else:
        own = math.hypot(m*at,m*al)/(4*.85*210000.)
        coefficient = 0.
    return own, box['gross_mass_kg']/30500., coefficient


def verify_farkas(A, b, y):
    """Verify exact A*x<=b contradiction; caller owns outwardness of rows.

    Free variables are assumed; all original variable bounds must have been
    included in A. Decimal strings are accepted as exact certificate numbers.
    Returning True proves only these serialized inequalities inconsistent.
    """
    if len(A) != len(b) or len(y) != len(b) or not A:
        return False
    n = len(A[0])
    if any(len(row) != n for row in A):
        return False
    try:
        weights = [Fraction(str(v)) for v in y]
        if any(v < 0 for v in weights):
            return False
        zero = all(sum((weights[i] * Fraction(str(A[i][j])) for i in range(len(A))), Fraction()) == 0 for j in range(n))
        rhs = sum((v * Fraction(str(r)) for v, r in zip(weights, b)), Fraction())
        return zero and rhs < 0
    except (ValueError, ZeroDivisionError):
        return False


def verify_witness(raw, witness, *, tolerance=2e-6):
    """Rebuild count/cell/mass/height/prefix/moment rows from raw map numbers.

    Residuals use exact rational arithmetic on decoded floats. Tolerance only
    qualifies the approximate primal witness; it can never authorize a proof.
    This audit deliberately never calls packing.build_model.
    """
    errors = []
    boxes = {b['id']: b for b in raw['containers']}
    load_list = raw['load_list']
    cells = {c['id']: c for c in raw['vessel']['cells']}
    stacks = {s['id']: s for s in raw['vessel']['stacks']}
    index = {cid: (s, k) for s in stacks.values() for k, cid in enumerate(s['cell_ids'])}
    minimum_height = min(_q(boxes[bid]['height_m']) for bid in load_list)
    capacity = Counter((int(cid[:3]), bool(s['on_deck']), k) for cid, (s, k) in index.items())
    declared_capacity = {(r['bay'], r['deck'], r['k']): r['cells'] for r in witness['capacities']}
    if declared_capacity != dict(capacity) or len(declared_capacity) != len(witness['capacities']):
        errors.append('serialized capacity differs from raw physical cells')
    members = [bid for c in witness['classes'] for bid in c['members']]
    if Counter(members) != Counter(load_list):
        errors.append('classes do not partition load list')
    for ci, c in enumerate(witness['classes']):
        for bid in c['members']:
            b = boxes[bid]
            physical = b['height_m'] + ((b.get('oog') or {}).get('over_height_m', 0))
            expected = dict(length_ft=b['length_ft'], reefer=b['reefer'], mass_kg=b['gross_mass_kg'],
                            height_m=b['height_m'], physical_height_m=physical,
                            units=1 if b['length_ft'] == 20 else 2,
                            under_deck='under_deck' in b['special_stowage'],
                            on_deck='on_deck' in b['special_stowage'])
            if any(c[key] != value for key, value in expected.items()):
                errors.append(f'class {ci} differs from raw box {bid}')
    counts, occupied, mass, heights = defaultdict(Fraction), defaultdict(Fraction), defaultdict(Fraction), defaultdict(Fraction)
    moment = Fraction()
    deck_boxes, deck_mass = defaultdict(Fraction), defaultdict(Fraction)
    for a in witness['allocations']:
        ci = a['class_index']
        c = witness['classes'][ci]
        amount = _q(a['count'])
        if amount < 0:
            errors.append('negative allocation')
        counts[ci] += amount
        slot = a['representative_slot']
        bay, row, tier = int(slot[:3]), int(slot[3:5]), int(slot[5:])
        physical = [slot] if bay % 2 else [f'{bay-1:03d}{row:02d}{tier:02d}', f'{bay+1:03d}{row:02d}{tier:02d}']
        if any(cid not in cells for cid in physical):
            errors.append('representative slot has missing physical support')
            continue
        sig = Counter((int(cid[:3]), bool(index[cid][0]['on_deck']), index[cid][1]) for cid in physical)
        expected_signature = sorted((*reg, n) for reg, n in sig.items())
        if [tuple(item) for item in a['signature']] != expected_signature:
            errors.append('representative slot has wrong signature')
        if len(physical) != c['units'] or any(not cells[cid]['accepts_' + str(c['length_ft'])] for cid in physical):
            errors.append('representative length incompatible')
        if c['reefer'] and not any(cells[cid]['reefer_plug'] for cid in physical):
            errors.append('representative reefer incompatible')
        if c['physical_height_m'] > min(index[cid][0]['max_height_m'] for cid in physical):
            errors.append('representative height incompatible')
        deck = tier >= 80
        if (c['under_deck'] and deck) or (c['on_deck'] and not deck):
            errors.append('representative special request incompatible')
        crane = raw['port']['quay_crane']['max_on_deck_tiers']
        if deck and crane is not None and max(index[cid][1] + 1 for cid in physical) > crane:
            errors.append('representative crane incompatible')
        support = sum((_q(index[cid][0]['base_z_m']) + index[cid][1]*minimum_height for cid in physical), Fraction()) / c['units']
        z = support + _q(c['height_m'])/2
        if abs(_q(a['z_lower_m']) - z) > _q(1e-9):
            errors.append('incorrect cargo moment coefficient')
        moment += amount * _q(c['mass_kg']) * _q(a['z_lower_m'])
        for bay, deck, k, n in a['signature']:
            occupied[bay, deck, k] += amount*n
            mass[bay, deck] += amount*n*_q(c['mass_kg'])/c['units']
            heights[bay, deck] += amount*n*_q(c['physical_height_m'])
            if deck:
                deck_boxes[bay] += amount*n/c['units']
                deck_mass[bay] += amount*n*_q(c['mass_kg'])/c['units']
    max_violation = Fraction()
    def residual(value, message):
        nonlocal max_violation
        max_violation = max(max_violation, value)
        if value > _q(tolerance):
            errors.append(message)
    for ci, c in enumerate(witness['classes']):
        residual(abs(counts[ci] - len(c['members'])), f'class {ci} count not conserved')
    for reg, cap in capacity.items():
        residual(occupied[reg] - cap, f'cell capacity {reg}')
        if reg[2]:
            residual(occupied[reg] - occupied[reg[0], reg[1], reg[2]-1], f'prefix capacity {reg}')
    regions = defaultdict(list)
    for s in stacks.values():
        if s['cell_ids']:
            regions[int(s['cell_ids'][0][:3]), bool(s['on_deck'])].append(s)
    for reg, sts in regions.items():
        mass_cap = sum((max(_q(s['max_mass_kg']), _q(s.get('max_mass_20_kg') or s['max_mass_kg']),
                            _q(s.get('max_mass_40_kg') or s['max_mass_kg'])) for s in sts), Fraction())
        height_cap = sum((_q(s['max_height_m']) for s in sts), Fraction())
        residual(mass[reg] - mass_cap, f'mass capacity {reg}')
        residual(heights[reg] - height_cap, f'height capacity {reg}')
    residual(abs(moment - _q(witness['cargo_vertical_moment_lower_kg_m'])) / max(1, abs(moment)), 'reported moment differs')
    for bay, value in deck_boxes.items():
        residual(abs(value - _q(witness['deck_boxes_by_odd_bay'][str(bay)])), 'reported deck counts differ')
        residual(abs(deck_mass[bay] - _q(witness['deck_mass_by_odd_bay_kg'][str(bay)])), 'reported deck masses differ')
    if 'physics_outer_bounds' in witness:
        p = witness['physics_outer_bounds']
        hull = raw['vessel']['hull']
        tank_geometry = _raw_tank_geometry(hull['hull_offsets_ref'])
        tank_models = {t['id']: t for t in raw['vessel']['tanks']}
        fixed_mass = _q(hull['lightship_mass_kg'])
        fixed_moment = fixed_mass*_q(hull['lightship_vcg_m'])
        for load in raw['vessel']['fixed_loads']:
            fixed_mass += _q(load['mass_kg'])
            fixed_moment += _q(load['mass_kg'])*_q(load['z_m'])
        cargo_mass = sum((_q(boxes[bid]['gross_mass_kg']) for bid in load_list), Fraction())
        total_mass = fixed_mass+cargo_mass
        lower_moment = fixed_moment+moment
        if set(witness['tank_masses_kg']) != set(tank_models):
            errors.append('tank witness does not cover raw tank catalog')
        for tank in p['tanks']:
            tid = tank['id']
            geometry = tank_geometry[tid]
            actual_mass = _q(witness['tank_masses_kg'][tid])
            cap = _q(tank_models[tid]['capacity_m3'])*_q(tank_models[tid]['content_density_kg_m3'])
            residual(-actual_mass, f'negative tank {tid}')
            residual(actual_mass-cap, f'tank capacity {tid}')
            if tank['z_lower_m'] > geometry['z_min_m'] or tank['z_upper_m'] < geometry['z_max_m']:
                errors.append(f'tank centroid enclosure {tid} too narrow')
            total_mass += actual_mass
            lower_moment += actual_mass*_q(tank['z_lower_m'])
        policy = raw.get('rule_overrides', {}).get('iscode.loading.displacement', {})
        lo = policy.get('minimum', hull['min_displacement_kg'])
        hi = policy.get('maximum', hull['max_displacement_kg'])
        residual(_q(lo)-total_mass, 'displacement below lower limit')
        residual(total_mass-_q(hi), 'displacement above upper limit')
        draft_hi = min(hull['max_draught_m'], raw['port']['max_draught_m'] or hull['max_draught_m'])
        residual(lower_moment/total_mass - _q(1.5)*_q(draft_hi), 'weather KG necessary condition')
        residual(abs(total_mass-_q(witness['total_mass_kg'])) / total_mass, 'reported total mass differs')
        residual(abs(lower_moment/total_mass-_q(witness['physical_kg_lower_m'])), 'reported lower KG differs')
        force_use = defaultdict(Fraction)
        gm_lower = witness['gm_lashing_interval_m'][0]
        for a in witness['allocations']:
            c = witness['classes'][a['class_index']]
            deck = any(item[1] for item in a['signature'])
            support = a['z_lower_m']-c['height_m']/2
            own = [_securing_raw(raw,boxes[bid],deck=deck,support_lower=support,gm_lower=gm_lower)
                   for bid in c['members']]
            if min(item[0] for item in own) > 1.+1e-8 or min(item[1] for item in own) > 1.+1e-8:
                errors.append('allocation violates independently weakened securing own-box condition')
            coefficient = min(item[2] for item in own)
            for bay, deck, k, n in a['signature']:
                if deck:
                    force_use[bay] += _q(a['count'])*n*_q(coefficient)/c['units']
        deck_columns = Counter(int(s['cell_ids'][0][:3]) for s in stacks.values() if s['on_deck'] and s['cell_ids'])
        for bay, force in force_use.items():
            residual(force-deck_columns[bay]*4*_q(.85)*210000, f'necessary securing force in bay {bay}')
    return dict(verified=not errors, errors=errors, max_row_violation=float(max_violation),
                census=raw_census(raw), cargo_vertical_moment_lower_kg_m=float(moment),
                exact_arithmetic='fractions over decoded binary floats', tolerance=tolerance,
                interpretation='approximate aggregate primal witness; physical feasibility remains unknown')


def verify_row(row):
    raw, digest = load_raw(row['input'])
    if row.get('input_sha256', digest) != digest:
        raise ValueError('Input hash does not match')
    packing = row.get('packing', row)
    witness = packing.get('witness', row.get('packing_witness'))
    if witness is None:
        return dict(instance_id=raw['id'], verified=False, reason='no primal witness to verify')
    answer = verify_witness(raw, witness)
    answer.update(instance_id=raw['id'], input_sha256=digest)
    return answer


def check_saved_plan(evidence):
    """Fresh unchanged full checker; run only while holding team's CPU token."""
    from stowage.checker import check
    from stowage.io import load_instance
    from stowage.model.plan import MovePlan
    from stowage.moves import apply_plan
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    started = monotonic()
    row = json.loads(Path(evidence).read_text())
    raw, digest = load_raw(row["input"])
    instance = load_instance(row["input"])
    plan = MovePlan.model_validate(row["plan"])
    replay = apply_plan(instance, plan)
    report = check(instance, plan)
    loaded = {p.container_id for p in replay.final_state.ship.placements}
    answer = dict(instance_id=instance.id, input=row["input"], input_sha256=digest,
                  evidence=evidence, evidence_sha256=hashlib.sha256(Path(evidence).read_bytes()).hexdigest(),
                  replay_legal=replay.legal, passed=report.passed,
                  all_load_list_aboard=set(raw["load_list"]).issubset(loaded),
                  loads=report.count.loads, boxes=len(instance.load_list),
                  lifts=report.count.weighted_nonproductive,
                  violations=len(report.violations), rule_freeze_v1=rule_freeze_v1_enabled(),
                  elapsed_s=monotonic()-started)
    validity = HERE / 'validity'
    validity.mkdir(exist_ok=True)
    (validity / (instance.id + '.report.json')).write_text(report.model_dump_json(indent=2) + '\n')
    (validity / (instance.id + '.plan.json')).write_text(plan.model_dump_json(indent=2) + '\n')
    answer['plan_sha256'] = hashlib.sha256(plan.model_dump_json().encode()).hexdigest()
    answer['source_sha256'] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                               for directory in ('src/stowage/checker', 'src/stowage/vessel')
                               for path in (ROOT / directory).glob('*.py')}
    if not (answer["replay_legal"] and answer["passed"] and answer["all_load_list_aboard"]
            and answer["loads"] == answer["boxes"] and answer["lifts"] == row["lifts"]):
        raise AssertionError(answer)
    return answer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-check-validation", action="store_true")
    parser.add_argument("--results")
    parser.add_argument("--out", default=str(HERE / "audit.json"))
    args = parser.parse_args()
    output = {}
    if args.full_check_validation:
        comparison = json.loads((ROOT / "experiments/mvp/frontier/lab/c2/c2-layout/comparison.json").read_text())
        output["validation_plans"] = []
        for chosen in comparison["maps"][:2]:
            checked = check_saved_plan(chosen["evidence"])
            output["validation_plans"].append(checked)
            print(json.dumps(checked), flush=True)
            Path(args.out).write_text(json.dumps(output, indent=2) + "\n")
    if args.results:
        result_path = Path(args.results)
        if result_path.is_dir():
            rows = [row for path in sorted(result_path.glob('*.json'))
                    if isinstance(row := json.loads(path.read_text()), dict) and 'input' in row]
        else:
            rows = json.loads(result_path.read_text())
        if isinstance(rows, dict):
            rows = rows["maps"]
        output["aggregate_witnesses"] = [verify_row(row) for row in rows]
    Path(args.out).write_text(json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    main()
