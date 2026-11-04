"""Explicit, resumable preparation of production physics caches.

Run ``uv run python -m stowage.cache_warm --workers 3`` after changing
physics. Each ship is an independent process shard; completed tables and
certificates survive interruption. A normal checker never builds these tables.
The numerical holdouts use the same direct-reference validators as the published
stability experiments. Reusing a matching certificate does not repeat holdouts.

The linear-owner public hook is ``stowage.linear.warm_cache(vessel, workers=1)``:
it receives each loaded full/slice Vessel and returns a JSON-compatible report.
Without that hook, linear warm-up is explicitly incomplete (exit status 2).
The explicit MVP option ``--c2-off`` marks linear profiles as unused because
the C2 global screen is disabled (D78/D79); physics qualification still runs.
``build_linear_model`` is instance-specific and cannot promise to prepare all
geometry families without a manifest and an agreed calibration condition.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "experiments/cache/stability/warmup"
from stowage.conventions import canonical_vessel_id

PRODUCTION_SHIPS = ("feeder_500", "feeder_800", "feeder_1700")


def _validator(relative):
    """Load a repository-owned reference validator, never downloaded code."""
    path = ROOT / "experiments/stability" / relative
    spec = importlib.util.spec_from_file_location(
        "stowage_cache_warm_" + path.stem + str(abs(hash(str(path)))), path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"reference validator unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    # The original standalone validators import adjacent numerical helpers.
    # Workers are processes, so temporarily extending sys.path has no thread
    # race and the caller's import search path is restored even on failure.
    search_path = sys.path[:]
    try:
        sys.path.insert(0, str(path.parent))
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = search_path
    return module


def _save(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    temporary.replace(path)


def _vessel_cache_snapshot(name, preset):
    directory = Path(os.environ.get("STOWAGE_VESSEL_CACHE_DIR", ROOT / "experiments/cache/vessels"))
    return {str(path): (path.stat().st_mtime_ns, path.stat().st_size)
            for path in directory.glob(f"{name}.{preset}.hull.*.json")}


def _production_tiny(vessel):
    """Use precisely the six-cell layout used by both production generators.

    This deliberate private import bridges a missing public generator export:
    the public select_tiny_region currently selects a different six-deck-cell
    layout and is unused by production. Replace this import when the generator
    owner exports its four-hold/two-deck selector without changing its geometry.
    """
    from stowage.generate.random_yard import _tiny_region
    return _tiny_region(vessel)


def _manifest_valid(params, solids):
    from stowage.checker import stability_liquid, stability_liquid_tables
    try:
        value = json.loads(stability_liquid.manifest_path(params).read_text())
        return (value["metadata"] == stability_liquid.metadata(params)
                and stability_liquid._valid_statistics(value["validation"])
                and value["tank_table_keys"] == {
                    name: stability_liquid_tables._identity(solid)
                    for name, solid in solids.items()})
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _covers_domain(actual, required):
    """A valid old certificate cannot qualify newly requested coverage.

    Grid density is established by the grid's own measured certificate; this
    check compares physical domains, retaining denser compatible grids.
    """
    import numpy as np
    try:
        for name, wanted in required.items():
            axis=np.asarray(actual[name],dtype=float)
            wanted=np.asarray(wanted,dtype=float)
            if (axis.ndim!=1 or len(axis)<4 or not np.isfinite(axis).all()
                    or np.any(np.diff(axis)<=0)
                    or axis[0]>wanted[0]+1e-12 or axis[-1]<wanted[-1]-1e-12):
                return False
        return True
    except (KeyError,TypeError,ValueError,IndexError):
        return False


def _liquid_required_grid(raw, liquid):
    import numpy as np
    heels, trims=liquid.DEFAULT_HEELS, liquid.DEFAULT_TRIMS
    if raw["id"] in ("DB-M-P", "DB-M-S"):
        heels=np.unique(np.r_[heels,np.radians([-12.5,-7.5,-2.5,2.5,7.5,12.5])])
        trims=np.sort(np.r_[trims,[-.03,-.015,-.01,-.005,.005,.01,.015,.03]])
        # Decimal knots from linspace and literals can differ by one ulp;
        # avoid nearly coincident nodes in the cubic Lagrange denominator.
        trims=trims[np.r_[True,np.diff(trims)>1e-12]]
    return dict(fills=liquid.DEFAULT_FILLS,heels=heels,trims=trims)


def _liquid_covers_domain(table, required):
    return table is not None and _covers_domain(
        {name:getattr(table,name,None) for name in required},required)


def _liquid_budget_ready(solid, liquid, kn_evidence):
    """Require room for measured KN error before costly loaded holdouts.

    Individual liquid certificates bound component interpolation. The loaded
    certificate also budgets the worst matching-density KN error, so separately
    passing 0.2 limits do not establish a passing combined budget. Certificates
    supplied here have already passed each builder's identity/SHA validation.
    """
    import math
    try:
        certificate=json.loads(liquid._path(solid).with_suffix('.qualified.json').read_text())
        if not kn_evidence:
            return False
        for name in ('gz','area','gm'):
            component=float(certificate[name])
            kn_errors=[float(row['validation']['max_l5_fraction'][name]) for row in kn_evidence]
            if (not math.isfinite(component) or component<0 or
                    any(not math.isfinite(value) or value<0 for value in kn_errors) or
                    component+max(kn_errors)>.2):
                return False
        return True
    except (OSError,ValueError,TypeError,KeyError,OverflowError):
        return False


def _warm_stability(name, params):
    import numpy as np
    from stowage.checker import stability_tables as kn
    from stowage.checker import stability_liquid_tables as liquid
    from stowage.checker import stability_liquid
    from stowage.checker import stability_sounding
    from stowage.checker.stability_geometry import StabilityGeometry
    from stowage.vessel.tanks import TankGeometry

    directory = REPORTS / name
    directory.mkdir(parents=True, exist_ok=True)
    rows = []
    kn_evidence = []
    direct = StabilityGeometry(params)
    required_kn = kn.default_grid(direct)
    for rho in (1025., 1000.):
        table = kn.cached_geometry(direct, params, rho)
        status = "reused"
        if table is not None and not _covers_domain(table.grid,required_kn):
            table = None
        if table is None:
            migrate = getattr(kn, "migrate_legacy", None)
            if migrate is not None:
                migrate(direct, params, rho)
                table = kn.cached_geometry(direct, params, rho)
                if table is not None and not _covers_domain(table.grid,required_kn):
                    table = None
                if table is not None:
                    status = "migrated"
        if table is None:
            evidence = _validator("interpolation/validate.py").validate(
                name, rho, samples=16, build=True, qualify=True)
            _save(directory / f"kn-{rho:g}.json", evidence)
            table = kn.cached_geometry(direct, params, rho)
            if table is None or not _covers_domain(table.grid,required_kn):
                raise RuntimeError(f"KN qualification did not pass: {name}, {rho}")
            status = "built and qualified"
        rows.append(dict(cache="KN", density_kg_m3=rho, status=status,
                         path=str(table.cache_path)))
        certificate = json.loads(table.cache_path.with_suffix(".certificate.json").read_text())
        kn_evidence.append(dict(vessel=name, density_kg_m3=rho,
            metadata=table.metadata, cache_path=str(table.cache_path),
            validation=certificate["validation"],
            cache_sha256=certificate["cache_sha256"]))
    kn_evidence_path = directory / "kn-evidence.json"
    _save(kn_evidence_path, dict(validation=kn_evidence))

    needs_qualification = False
    solids = {}
    for raw in params.get("tanks", []):
        solid = TankGeometry.from_params(raw, ship_params=params)
        if getattr(solid, "shape", None) != "hull_sections":
            continue
        solids[raw["id"]] = solid
        sounding_path = stability_sounding.CACHE_DIRECTORY / ("sounding-" + stability_sounding._key(solid) + ".npz")
        sounding_exists = sounding_path.exists()
        stability_sounding.lookup(solid)
        rows.append(dict(cache="upright sounding", tank=raw["id"],
                         status="reused" if sounding_exists else "built", path=str(sounding_path)))
        required_liquid = _liquid_required_grid(raw,liquid)
        table = liquid.lookup(solid)
        if (not _liquid_covers_domain(table,required_liquid) or
                not _liquid_budget_ready(solid,liquid,kn_evidence)):
            table = None
        status = "reused"
        if table is None:
            migrate = getattr(liquid, "migrate_legacy", None)
            if migrate is not None:
                migrate(solid)
                table = liquid.lookup(solid)
                if (not _liquid_covers_domain(table,required_liquid) or
                        not _liquid_budget_ready(solid,liquid,kn_evidence)):
                    table = None
                if table is not None:
                    status = "migrated"
        if table is None:
            liquid.build(solid, **required_liquid)
            needs_qualification = True
            status = "built; qualification pending"
        rows.append(dict(cache="liquid transfer", tank=raw["id"], status=status,
                         path=str(liquid._path(solid))))

    manifest = stability_liquid.manifest_path(params)
    if not _manifest_valid(params, solids):
        migrate = getattr(stability_liquid, "migrate_legacy", None)
        if migrate is not None:
            migrate(params)
    if needs_qualification or not _manifest_valid(params, solids):
        component_path = directory / "liquid-component.json"
        _validator("method1_tables/qualify_tanks.py").run(
            name, component_path, samples=16, envelope_samples=4, certify=True)
        _validator("method1_tables/qualify.py").run(
            name, directory / "liquid-loaded.json", samples=8,
            tank_evidence=component_path, certify=True, full_direct_reference=True,
            kn_evidence=kn_evidence_path)
        if not _manifest_valid(params, solids):
            raise RuntimeError(f"liquid qualification did not create manifest for {name}")
        for row in rows:
            if row["status"] == "built; qualification pending":
                row["status"] = "built and qualified"
        rows.append(dict(cache="liquid vessel qualification", status="qualified", path=str(manifest)))
    else:
        rows.append(dict(cache="liquid vessel qualification", status="reused", path=str(manifest)))
    return rows


def _warm_ship(name, c2_off=False):
    name = canonical_vessel_id(name)
    # Spawned workers also get these limits before importing numerical modules.
    for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ[key] = "1"
    from stowage.vessel import load_vessel, load_fleet_preset, load_ship_params, SLICE_PRESETS
    import stowage.linear as linear
    started = perf_counter()
    result = dict(ship=name, caches=[], warnings=[], errors=[])
    try:
        public_linear_hook = getattr(linear, "warm_cache", None)
        # Build dependency-independent physics before expensive vessel assembly.
        result["caches"].extend(_warm_stability(name, load_ship_params(name)))
        vessels = []
        for preset in SLICE_PRESETS:
            before = _vessel_cache_snapshot(name, preset)
            vessel = (load_vessel(name, with_hull=True) if preset == "full"
                      else load_fleet_preset(name, preset, with_hull=True))
            after = _vessel_cache_snapshot(name, preset)
            status = "built" if after != before else "reused"
            if not after:
                status = "loaded; disk cache unavailable"
            result["caches"].append(dict(cache="vessel", geometry=preset, status=status))
            vessels.append((preset, vessel))
        full = next(vessel for preset, vessel in vessels if preset == "full")
        vessels.append(("tiny_production", _production_tiny(full)))
        result["caches"].append(dict(cache="vessel", geometry="tiny_production",
            status="derived from cached full vessel"))
        # Linear calibration's reference oracle requires the independently
        # qualified actual-transfer tables. Establish this dependency first,
        # even when the public linear hook was added after an earlier warm-up.
        for preset, vessel in vessels:
            if c2_off:
                # APPROXIMATION: D78/D79 disable the optional C2 linear screen
                # for the MVP. Its profiles are unused, not qualified; the
                # independent stability qualification and full checker remain.
                result["caches"].append(dict(cache="linear", geometry=preset,
                    status="not used: C2 off (D78/D79)"))
            elif public_linear_hook is not None:
                try:
                    report = public_linear_hook(vessel, workers=1)
                    result["caches"].append(dict(cache="linear", geometry=preset,
                        status=report.get("status", "prepared"), report=report))
                except Exception as error:
                    # Geometry families are independent after physics has
                    # qualified. A stale calibration must remain an explicit
                    # failure while every other public hook is still called.
                    diagnostic=f"{type(error).__name__}: {error}"
                    result["caches"].append(dict(cache="linear", geometry=preset,
                        status="failed", error=diagnostic))
                    result["errors"].append(f"linear {preset}: {diagnostic}")
        if public_linear_hook is None and not c2_off:
            result["warnings"].append("linear cache warm-up unavailable: stowage.linear has no public warm_cache(vessel) hook")
    except Exception as error:
        result["errors"].append(f"{type(error).__name__}: {error}")
    result["elapsed_seconds"] = perf_counter() - started
    result["complete"] = not result["errors"] and not result["warnings"]
    _save(REPORTS / name / "summary.json", result)
    return result


def _workers(value):
    number = int(value)
    if not 1 <= number <= 8:
        raise argparse.ArgumentTypeError("workers must be between 1 and 8")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ships", nargs="+", type=canonical_vessel_id, choices=PRODUCTION_SHIPS, default=list(PRODUCTION_SHIPS))
    parser.add_argument("--workers", type=_workers, default=3,
                        help="independent single-threaded ship processes (1–8, default 3)")
    parser.add_argument("--c2-off", action="store_true",
                        help="MVP D78/D79: mark unused C2 linear profiles without warming them; still qualify physics")
    options = parser.parse_args(argv)
    ships = list(dict.fromkeys(options.ships))
    started = perf_counter()
    results = []
    if options.workers == 1:
        for name in ships:
            result = _warm_ship(name, c2_off=options.c2_off)
            results.append(result)
            print(json.dumps(result, default=str), flush=True)
    else:
        with ProcessPoolExecutor(max_workers=min(options.workers, len(ships)),
                                 mp_context=multiprocessing.get_context("spawn")) as pool:
            pending = {pool.submit(_warm_ship, name, c2_off=options.c2_off): name for name in ships}
            for future in as_completed(pending):
                result = future.result()
                results.append(result)
                print(json.dumps(result, default=str), flush=True)
    summary = dict(complete=all(row["complete"] for row in results),
                   elapsed_seconds=perf_counter()-started, ships=results)
    _save(REPORTS / "summary.json", summary)
    print(json.dumps(dict(complete=summary["complete"], elapsed_seconds=summary["elapsed_seconds"],
                         report=str(REPORTS / "summary.json"))), flush=True)
    return 0 if summary["complete"] else 2


if __name__ == "__main__":
    sys.exit(main())
