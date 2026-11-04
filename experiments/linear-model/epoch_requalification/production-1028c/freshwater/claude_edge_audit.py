"""Replay public necessary rows on freshly captured exact edge equilibria.

No exact quantity is inferred from the affine model. This checks individual
row inclusion, including states that fail another exact row, and is finite
empirical evidence rather than a domain-wide proof or departure approval.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def per_row_exact(model, table, state, exact, grouped):
    """Keep station/heel-side failures from masking another exact-feasible row."""
    result = []
    heel = min(model.max_list_rad, model.band.max_abs_heel_rad)
    indices = {'sf': 0, 'bm': 0}
    for name in table.names:
        if name.startswith(('sf@', 'bm@')):
            kind = name[:2]
            i = indices[kind]
            if i >= len(model.station_x_m) or name != f'{kind}@{model.station_x_m[i]:g}':
                raise ValueError('strength row station order differs from its coefficient array')
            indices[kind] += 1
            value = exact['sf_n' if kind == 'sf' else 'bm_nm'][i]
            low = getattr(model, kind+'_min_'+('n' if kind == 'sf' else 'nm'))[i]
            high = getattr(model, kind+'_max_'+('n' if kind == 'sf' else 'nm'))[i]
            passed = low-1e-7 <= value <= high+1e-7
        elif name == 'list_port':
            passed = exact['list_rad'] <= heel+1e-7
        elif name == 'list_starboard':
            passed = exact['list_rad'] >= -heel-1e-7
        elif name.startswith('tank_mass@'):
            key = name.split('@',1)[1]
            low, high = model._tank_mass_bounds_kg[key]
            value = state['tank_masses_kg'][model.tank_ids.index(key)]
            passed = low-1e-7 <= value <= high+1e-7
        else:
            passed = grouped[name]
        result.append(bool(passed))
    if any(count not in (0, len(model.station_x_m)) for count in indices.values()):
        raise ValueError('incomplete strength row population')
    return result


def main():
    # Nested audit imports also resolve the explicit freshwater profile.
    from profile_run import install
    install()
    from stowage.linear import linear_constraints
    from stowage.linear.margin_policy import qualify_policy
    from stowage.linear.operations import public_loading_policy
    from stowage.linear.reusable import assemble_instance
    from stowage.model import FixedLoad, ShipState

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bank', type=Path)
    parser.add_argument('--output', type=Path, default=HERE/'claude-edge-row-audit.json')
    parser.add_argument('--additional-fresh-population', type=Path, action='append', default=[])
    args = parser.parse_args()
    bank = json.loads(args.bank.read_text()) if args.bank else None
    audit = module("claude_original_row_audit", HERE/"d49_holdout_audit.py")
    driver = audit.driver()
    cases = []
    for ship in audit.SHIPS:
        family, instance, errors, diagnostics, before = driver.prepare(ship, HERE/"ordinary")
        if ship == "feeder_500":
            from necessary_adapter import validate_exclusion
            from types import SimpleNamespace
            raw = json.loads((HERE/"ordinary"/ship/"raw.json").read_text())
            assert validate_exclusion(SimpleNamespace(read=lambda path:json.loads(path.read_text())), HERE/"edges-v3"/ship, raw, family.template)
        certificate = qualify_policy(family, instance, diagnostics, bank=bank)
        tanks = sorted(instance.vessel.tanks, key=lambda tank: tank.id)
        sources = [(phase, HERE/epoch/ship/f"necessary-edges-{phase}.json")
                   for epoch in ('edges-v1', 'edges-v2', 'edges-v3')
                   for phase in ('train', 'holdout')]
        if (HERE/'edges-v3'/ship/'necessary-review.json').exists():
            sources.append(('review', HERE/'edges-v3'/ship/'necessary-review.json'))
        for path in args.additional_fresh_population:
            if json.loads(path.read_text()).get('ship') == ship:
                sources.append(('diagnostic', path))
        for phase, source in sources:
            record = json.loads(source.read_text())
            if (record["complete"] is not True or record["physical_inputs_start"] != before
                    or record["physical_inputs_end"] != before):
                raise ValueError("incomplete or stale edge observations")
            details = []
            rejected_boundary_roundoff = []
            for observed in record["list_row_samples"]:
                if observed.get("admitted") is not True or observed.get("qualified_domain") is not True:
                    raise ValueError("edge audit received an unqualified point")
                fixed = FixedLoad(**observed["fixed_point"])
                onboard = ShipState(vessel_id=instance.vessel.id, tank_masses_kg={
                    tank.id: float(value) for tank, value in zip(tanks,
                        observed["all_tank_masses_kg"], strict=True) if not tank.adjustable})
                state = instance.model_copy(update={"containers": [], "load_list": [],
                    "discharge_list": [], "onboard": onboard,
                    "vessel": instance.vessel.model_copy(update={"fixed_loads": [fixed]})})
                current_diagnostics = dict(diagnostics, margin_policy=certificate,
                    loading_policy=public_loading_policy(state))
                try:
                    model = assemble_instance(family, state, family.template.band,
                        errors=errors, diagnostics=current_diagnostics)
                except ValueError as exc:
                    # The experiment retains +/-1e-7 boundary observations;
                    # production's strict centroid guard remains authoritative.
                    # Never change an observed point or relax that guard to
                    # replay a z=0 corner recorded a few ULPs below zero.
                    domain = diagnostics['family_domain']
                    violations = [max(domain['kg_min_m']-fixed.z_m, 0.),
                        max(fixed.z_m-domain['kg_max_m'], 0.),
                        max(domain['fixed_x_min_m']-fixed.x_m, 0.),
                        max(fixed.x_m-domain['fixed_x_max_m'], 0.),
                        max(abs(fixed.y_m)-domain['fixed_y_abs_max_m'], 0.)]
                    if (phase not in ('train', 'diagnostic') or 'centroid is outside' not in str(exc)
                            or not 0 < max(violations) <= 1e-7):
                        raise
                    rejected_boundary_roundoff.append(dict(state_id=observed['state_id'],
                        max_centroid_excess_m=max(violations), rejection=str(exc)))
                    continue
                table = linear_constraints(model, direction="necessary")
                cells, ballast = (np.asarray(observed[key]) for key in
                                   ("cell_masses_kg", "tank_masses_kg"))
                values = table.a_cell@cells+table.a_tank@ballast+table.const
                passes = (values >= table.lb-1e-7)&(values <= table.ub+1e-7)
                exact = {name: observed["exact_"+name] for name in
                    ("gm_m", "trim_m", "list_rad", "draught_m", "sf_n", "bm_nm")}
                exact["displacement_kg"] = observed["mass_kg"]
                exact_rows = audit.exact_tests(model, observed, exact)
                individual = per_row_exact(model, table, observed, exact, exact_rows)
                failures = [name for name, passed, exact_passed in zip(
                    table.names, passes, individual, strict=True) if exact_passed and not passed]
                details.append(dict(state_id=observed["state_id"],
                    necessary_rejects_exact_pass=failures, exact_all=all(exact_rows.values()),
                    necessary_all=bool(passes.all())))
            if driver.snapshot(ship, family.parameters) != before:
                raise ValueError("numerical inputs changed during edge row audit")
            cases.append(dict(ship=ship, phase=phase, source=audit.pin(source),
                samples=len(details), states=details,
                production_rejected_boundary_roundoff=rejected_boundary_roundoff,
                passed=not any(row["necessary_rejects_exact_pass"] for row in details),
                policy_certificate_sha256=certificate["sha256"]))
    result = dict(cases=cases, samples=sum(case["samples"] for case in cases),
        holdout_samples=sum(case["samples"] for case in cases if case["phase"] == "holdout"),
        passed=all(case["passed"] for case in cases),
        bank=audit.pin(args.bank or audit.ROOT/"src/stowage/linear/margin_policy_data.json"))
    audit.write(args.output, result)
    print(json.dumps({key:value for key,value in result.items() if key != "cases"}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
