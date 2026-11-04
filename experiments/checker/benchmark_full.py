"""Reproducible fresh-process and warm full-check timings, without vessel setup.

APPROXIMATION: throughput fixture fills every 20-foot cell with a synthetic
empty dry20 box (2,400 kg). This is a loading-condition benchmark, not a plan
quality or feasibility claim. Whole-fleet runs use current production KCS
parameters and offsets, not archived task 3.5 hull geometry. Model-only
hydrostatic/cross-curve tables are omitted; numerical checker parts integrate
the current offsets or use independently qualified stability tables. See the frozen
checker contract and ISO 668 box geometry; no physical limits are relaxed.
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import os
from pathlib import Path
import pstats
import statistics
import subprocess
import sys
from time import perf_counter

# Keep the experiment within one CPU core's numerical-library budget.
for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"

ROOT = Path(__file__).resolve().parents[2]
SIZES = ("tiny", "small", "medium", "full")
FLEET = ("feeder_500", "feeder_800", "feeder_1700")


def homogeneous_departure(name):
    """Schema-current version of task 3.1's documented dry loading condition.

    ASSUMPTION: all holds and the bottom two deck tiers carry equal-mass 20ft
    boxes; target is 90% of upright design displacement, all tanks dry. This
    preserves production hull/lightship/fixed loads and is no safety certificate.
    """
    from stowage.conventions import make_container_id
    from stowage.model import Instance
    from stowage.model.fixtures import build_instance
    from stowage.vessel.fleet import load_ship_params, load_vessel
    from stowage.vessel.hydrostatics import HullGeometry

    vessel = load_vessel(name, with_hull=True)
    payload = build_instance().model_dump(mode="python")
    geometry = HullGeometry.from_params(load_ship_params(name))
    displacement = geometry.submerged(vessel.hull.design_draught_m,
        vessel.hull.design_draught_m,
        density_kg_m3=payload["port"]["water_density_kg_m3"]).displacement_kg
    selected = {cid for stack in vessel.stacks
                for cid in (stack.cell_ids[:2] if stack.on_deck else stack.cell_ids)}
    cells = [cell for cell in vessel.cells if cell.id in selected]
    mass = (.9 * displacement - vessel.hull.lightship_mass_kg
            - sum(load.mass_kg for load in vessel.fixed_loads)) / len(cells)
    if not 2200 <= mass <= 32000:
        raise ValueError(f"homogeneous mass outside benchmark box range: {mass}")
    containers, placements = [], []
    for serial, cell in enumerate(cells, 1):
        cid = make_container_id("QZXU", serial)
        containers.append(dict(id=cid, iso_type="22G1", length_ft=20, height_m=2.591,
            tare_kg=2200, gross_mass_kg=mass, pol="THLCH", pod="THBKK"))
        placements.append(dict(slot=cell.id, container_id=cid))
    payload.update(id=f"kcs_homogeneous_departure_{name}", vessel=vessel,
        containers=containers, load_list=[], discharge_list=[],
        onboard=dict(vessel_id=vessel.id, placements=placements,
                     tank_masses_kg={tank.id: 0. for tank in vessel.tanks}))
    payload["yard"]["stacks"] = []
    payload["port"]["max_draught_m"] = vessel.hull.max_draught_m
    return Instance.model_validate(payload)


def condition(size):
    from stowage.conventions import make_container_id
    from stowage.io import load_instance
    from stowage.model import BallastCondition, Container, FixedLoad, FixedLoadSilhouette, HullModel, Instance, MovePlan, Placement, ShipState
    from stowage.model.yard import YardState
    from stowage.checker.strength_limits import permissible_stations
    from stowage.vessel.fleet import load_ship_params

    if os.environ.get('STOWAGE_BENCH_LOADING') == 'homogeneous':
        if size not in FLEET:
            raise ValueError('homogeneous benchmark requires a whole fleet vessel')
        # Reuse the stability owner's documented production benchmark without
        # altering its loading distribution, lightship, fixed mass or geometry.
        instance = homogeneous_departure(size)
        plan = MovePlan(id=f'benchmark-homogeneous-{size}', instance_id=instance.id,
                        call_index=instance.call_index,
                        ballast=BallastCondition(source='planner',
                            tank_masses_kg={tank.id: instance.onboard.tank_masses_kg.get(tank.id, 0.)
                                            for tank in instance.vessel.tanks if tank.adjustable}))
        return instance, plan

    original = load_instance(ROOT / "experiments" / "instances" / ("full" if size in FLEET else size) / "seed-00000001.json")
    if size in FLEET:
        from stowage.vessel.fleet import load_vessel
        from stowage.vessel.tanks import build_tanks
        # Exact parts integrate offsets afresh: no placeholder tables are used.
        # APPROXIMATION: benchmark condition has empty tanks and a balanced
        # synthetic background mass at z=3m to reach design displacement.
        original.vessel = load_vessel(size, with_hull=os.environ.get('STOWAGE_BENCH_PRODUCTION_MODEL') == '1')
        raw = load_ship_params(size)
        if os.environ.get('STOWAGE_BENCH_PRODUCTION_MODEL') != '1':
            fields = {**raw['hull'], **raw['operating_limits']}
            fields = {k:v for k,v in fields.items() if k in HullModel.model_fields}
            fields.setdefault('max_draught_m', fields['scantling_draught_m'])
            original.vessel.hull = HullModel(**fields, hydrostatics=[], cross_curves=[], stations=[])
            original.vessel.tanks = build_tanks(raw)
        original.onboard = ShipState(vessel_id=size)
    # Archived task 3.5 shards predate the strength-envelope implementation.
    # Derive the current synthetic S11A limits rather than timing missing data.
    params = load_ship_params(original.vessel.slice_of or original.vessel.id)
    original.vessel.hull.stations = permissible_stations(params, original.vessel.hull)
    cells = sorted((c for c in original.vessel.cells if c.accepts_20), key=lambda c: c.id)
    boxes = [Container(id=make_container_id("QZXU", i + 1), iso_type="22G1", length_ft=20,
                       height_m=2.591, tare_kg=2400., gross_mass_kg=2400., empty=True,
                       pol=original.port.locode, pod=original.rotation.calls[original.call_index+1].locode)
             for i in range(len(cells))]
    if size in FLEET:
        from stowage.checker.stability_geometry import StabilityGeometry
        geometry = StabilityGeometry(params)
        s = geometry.full_submerged(geometry.design_draught_m, geometry.design_draught_m)
        hull = original.vessel.hull
        mass = s.displacement_kg-hull.lightship_mass_kg-sum(b.gross_mass_kg for b in boxes)
        x = (s.displacement_kg*s.lcb_m-hull.lightship_mass_kg*hull.lightship_lcg_m
             -sum(c.lcg_m*b.gross_mass_kg for c,b in zip(cells,boxes)))/mass
        # APPROXIMATION: this artificial background is cargo with an explicitly
        # submerged occupied envelope, not unclassified stores or zero windage
        # inferred from a legacy point mass. Its concentrated strength load is
        # unchanged; the envelope is not a reconstructed cargo distribution.
        half_length = .9 * min(x, hull.lpp_m - x)
        original.vessel.fixed_loads = [FixedLoad(
            x_m=x, y_m=0., z_m=3., mass_kg=mass, kind="cargo",
            silhouette=FixedLoadSilhouette(
                x_min_m=x-half_length, x_max_m=x+half_length,
                y_min_m=-.4*hull.beam_m, y_max_m=.4*hull.beam_m,
                z_min_m=2., z_max_m=4.))]
    ship = ShipState(vessel_id=original.vessel.id,
                     placements=[Placement(slot=c.id, container_id=b.id) for c,b in zip(cells, boxes)],
                     tank_masses_kg=dict(original.onboard.tank_masses_kg))
    payload = original.model_dump()
    payload.update(stage="B", containers=boxes, load_list=[], discharge_list=[], onboard=ship,
                   yard=YardState(yard_id=original.yard.yard_id))
    instance = Instance.model_validate(payload)
    plan = MovePlan(id=f"benchmark-{size}", instance_id=instance.id, call_index=instance.call_index,
                    ballast=BallastCondition(source="planner", tank_masses_kg={}))
    return instance, plan


def worker(args):
    from stowage.checker.full import check
    from stowage.versions import component_versions
    from stowage.vessel.hydrostatics import clear_hydrostatic_caches, hydrostatic_cache_info

    setup_started = perf_counter()
    instance, plan = condition(args.worker)
    setup_seconds = perf_counter()-setup_started
    # A cold measurement must not inherit numerical results from input setup.
    clear_hydrostatic_caches()
    source_paths = sorted({*ROOT.joinpath('src/stowage/checker').glob('*.py'),
                    *ROOT.joinpath('src/stowage/model').glob('*.py'),
                    *ROOT.joinpath('src/stowage/vessel').glob('*.py'),
                    ROOT / 'src/stowage/versions.py', Path(__file__).resolve(),
                    ROOT / f'params/fleet/{instance.vessel.slice_of or instance.vessel.id}.toml'})
    def source_hashes():
        return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in source_paths}
    source_before = source_hashes()
    cache_snapshots = []
    timings, reference = [], None
    for iteration in range(args.repeats+1):
        started = perf_counter()
        report = check(instance, plan)
        timings.append(perf_counter()-started)
        cache_snapshots.append(hydrostatic_cache_info())
        dumped = report.model_dump(mode="json")
        if reference is None:
            reference = dumped
        elif dumped != reference:
            raise AssertionError("cold/warm reports differ")
    row = dict(size=args.worker, vessel=instance.vessel.id,
               cells=len(instance.vessel.cells), boxes=len(instance.onboard.placements),
               setup_seconds=setup_seconds, cold_seconds=timings[0], warm_seconds=timings[1:],
               warm_median_seconds=statistics.median(timings[1:]),
               passed=report.passed, parts_ran={k: v.ran for k,v in report.parts.items()},
               part_errors={k: v.error for k,v in report.parts.items() if v.error},
               rule_ids=sorted({v.rule_id for v in report.violations}),
               versions=component_versions(), equal_reports=True,
               loading_condition=os.environ.get('STOWAGE_BENCH_LOADING', 'all-cells-balanced'),
               loading_details=dict(total_container_mass_kg=sum(c.gross_mass_kg for c in instance.containers),
                                    container_mass_range_kg=[min(c.gross_mass_kg for c in instance.containers),
                                                             max(c.gross_mass_kg for c in instance.containers)],
                                    fixed_loads=[load.model_dump(mode="json") for load in instance.vessel.fixed_loads],
                                    tank_masses_kg=instance.onboard.tank_masses_kg),
               cache_flags={key: os.environ.get(key, "default") for key in ("STOWAGE_VESSEL_CACHE", "STOWAGE_VESSEL_CACHE_DIR", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS")},
               repeats=args.repeats,
               production_model=os.environ.get('STOWAGE_BENCH_PRODUCTION_MODEL') == '1',
               report_sha256=hashlib.sha256(json.dumps(reference, sort_keys=True).encode()).hexdigest(),
               stability_table_metrics={k: v for k, v in report.parts['stability'].metrics.items()
                                        if 'table' in k},
               stability_margins=dict(report.parts['stability'].margins),
               hull_offsets_ref=instance.vessel.hull.hull_offsets_ref,
               condition_note=('ASSUMPTION: schema-current equivalent of production kcs_departures.build_departure; '
                               'all holds and lowest two deck tiers; identical dry boxes; '
                               '90% upright design displacement; zero tank masses; no background balancing; '
                               'cold setup excluded, result caches cleared after setup'
                               if os.environ.get('STOWAGE_BENCH_LOADING') == 'homogeneous' else
                               'APPROXIMATION: all 20-foot cells hold synthetic 2400 kg boxes; '
                              'empty tanks and balanced background cargo mass at z=3 m with an explicit submerged envelope; '
                              + ('complete production HullModel; ' if os.environ.get('STOWAGE_BENCH_PRODUCTION_MODEL') == '1'
                                 else 'model-only hydrostatic/crosscurve tables omitted; ')
                              +
                              'cold setup excluded, result caches cleared after setup'),
               hydrostatic_cache_after_calls=cache_snapshots,
               source_sha256=source_hashes(), source_sha256_before_calls=source_before)
    if args.profile:
        profile = cProfile.Profile()
        profile.enable()
        profiled = check(instance, plan)
        profile.disable()
        if profiled.model_dump(mode="json") != reference:
            raise AssertionError("profiled report differs")
        directory = Path(args.output).resolve().parent
        directory.mkdir(parents=True, exist_ok=True)
        prefix = directory / f"{Path(args.output).stem}-profile-{args.worker}"
        profile.dump_stats(str(prefix.with_suffix(".prof")))
        with prefix.with_suffix(".txt").open("w") as stream:
            stats = pstats.Stats(profile, stream=stream).strip_dirs().sort_stats("cumulative")
            stats.print_stats(45)
            stats.print_stats(r'_loading_condition|_solve|check_strength|check_visibility|check_lashing|check_stability')
        row["profile"] = str(prefix.with_suffix(".txt").relative_to(ROOT))
        row["hydrostatic_cache_after_profile"] = hydrostatic_cache_info()
    if (os.environ.get('STOWAGE_BENCH_PRODUCTION_MODEL') == '1' and args.worker in FLEET
            and os.environ.get('STOWAGE_BENCH_LOADING') != 'homogeneous'):
        # Confirm the setup shortcut used for baseline measurements cannot
        # change any report. This extra validation is outside all timed calls.
        validation_started = perf_counter()
        os.environ.pop('STOWAGE_BENCH_PRODUCTION_MODEL')
        try:
            projected_instance, projected_plan = condition(args.worker)
        finally:
            os.environ['STOWAGE_BENCH_PRODUCTION_MODEL'] = '1'
        projected = check(projected_instance, projected_plan).model_dump(mode='json')
        if projected != reference:
            raise AssertionError('production/projected-model reports differ')
        row['equal_production_projected_reports'] = True
        row['projection_validation_seconds'] = perf_counter()-validation_started
    row['source_sha256_after_calls'] = source_hashes()
    row['sources_stable_during_calls'] = source_before == row['source_sha256_after_calls']
    print(json.dumps(row, sort_keys=True), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", choices=SIZES+FLEET, default=list(SIZES))
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--output", default=str(Path(__file__).parent / "timings.json"))
    parser.add_argument("--worker", choices=SIZES+FLEET, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if args.worker:
        worker(args)
        return
    rows = []
    for size in args.sizes:
        command = [sys.executable, __file__, "--worker", size, "--repeats", str(args.repeats),
                   "--output", args.output]
        if args.profile:
            command.append("--profile")
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        if result.returncode:
            sys.stderr.write(result.stderr)
            raise SystemExit(result.returncode)
        row = json.loads(result.stdout)
        rows.append(row)
        print(json.dumps(row, sort_keys=True), flush=True)
        Path(args.output).write_text(json.dumps(rows, indent=2, sort_keys=True)+"\n")


if __name__ == "__main__":
    main()
