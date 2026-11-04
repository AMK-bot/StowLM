"""Resumable, admission-controlled development measurements; never reads sealed sets.

Run from the repository root with the prescribed PYTHONPATH. The live profile
caps additive stage budgets; Engine 0.13.0 retains documented search floors.
Outputs record the actual elapsed time, including overruns of those budgets.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict
import faulthandler
import hashlib
import importlib
import json
import marshal
import os
from pathlib import Path
import re
import shutil
import statistics
import sys
import time
import tomllib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RERUN_FAILURES = False
sys.path.insert(0, str(ROOT))
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
              "MKL_NUM_THREADS", "STOWAGE_IN_PORT_WORKERS"):
    os.environ[_name] = "1"
os.environ["STOWAGE_VESSEL_CACHE_DIR"] = str(HERE / "measurements" / "vessel-cache")


def admit():
    """Wait, without raising, for the fleet's current enabled-core admission."""
    while True:
        try:
            state = json.loads((ROOT / "experiments/fleet/core-state.json").read_text())
            age = time.time() - float(state["t"])
            mask = int(state["mask"], 0) if isinstance(state["mask"], str) else int(state["mask"])
            if mask >= 0 and mask.bit_count() >= 8 and float(state["load1"]) <= 45 and 0 <= age < 120:
                return state
        except (OSError, ValueError, KeyError, TypeError):
            pass
        print("WAIT fleet admission", flush=True)
        time.sleep(15)


def maps():
    frozen = HERE / "measurements" / "maps.json"
    if frozen.exists():
        return json.loads(frozen.read_text())
    sets = ROOT / "experiments/mvp/frontier/lab/sets"
    tiny = (sets / "quick_tiny.txt").read_text().splitlines()
    small = (sets / "quick_small.txt").read_text().splitlines()
    medium = (sets / "medium12.txt").read_text().splitlines()[:4]
    # These are explicitly named development maps in C6 c6-lifts/SPLIT.json.
    medium += [
        "experiments/mvp/strategy/fillsweep/maps/train/medium/feeder_500/f60-high/seed-192200000.json",
        "experiments/mvp/strategy/fillsweep/maps/train/medium/feeder_500/f60-low/seed-192200002.json",
    ]
    # Prefer the fresh 0.6 development cohort once it is published. Do not even
    # enumerate the sibling sealed directory.
    dev = ROOT / "experiments/mvp/pipeline/maps/dev"
    fresh = sorted(dev.rglob("*.json")) if dev.exists() else []
    usable = []
    for path in fresh:
        try:
            raw = json.loads(path.read_text())
            if "vessel" in raw and 60 < len(raw.get("load_list", [])) <= 195:
                usable.append(str(path.relative_to(ROOT)))
        except (OSError, ValueError):
            continue
    if len(usable) >= 6:
        medium = usable[:6]
    selected = {"tiny": [tiny[i] for i in (2, 4, 7)],
                "small": [small[i] for i in (2, 5, 8)], "medium": medium}
    frozen.parent.mkdir(parents=True, exist_ok=True)
    _write_json(frozen, selected)
    return selected


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n")
    temporary.replace(path)


def seed_physical_caches():
    """Copy only immutable vessel geometry and numerical acceleration artifacts."""
    admission = admit()
    runtime = ROOT / "experiments/mvp/pipeline/maps/runtime"
    snapshot = json.loads((runtime.parent / "runtime-snapshot.json").read_text())
    manifest = dict(started_at=time.time(), admission=admission,
                    source="experiments/mvp/pipeline/maps/runtime", files={})
    declared = snapshot["acceleration_cache_files"]
    for relative, expected in sorted(declared.items()):
        source = runtime / relative
        parts = Path(relative).parts
        if parts[:3] == ("experiments", "cache", "stability") and len(parts) == 4:
            target = HERE / "runtime-cache/stability" / source.name
        elif parts[:3] == ("experiments", "cache", "vessels") and (
                source.name.startswith("feeder_500.full.") or source.name.startswith("feeder_800.full.")):
            target = HERE / "measurements/vessel-cache" / source.name
        else:
            continue
        if source.suffix not in (".npz", ".json"):
            continue
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != expected:
            raise ValueError("physical cache snapshot mismatch: " + relative)
        copied = False
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = target.with_suffix(f".{os.getpid()}.tmp")
            shutil.copy2(source, temporary)
            temporary.replace(target)
            copied = True
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError("physical cache destination mismatch: " + str(target))
        # Native liquid-table dispatch chooses the newest grid for each solid.
        # File times therefore participate in dispatch, alongside SHA-bound
        # contents and qualification evidence. Preserve the source ordering.
        shutil.copystat(source, target)
        manifest["files"][str(target.relative_to(HERE))] = dict(sha256=actual,
            source=relative, copied=copied, bytes=source.stat().st_size)
    manifest.update(completed_at=time.time(), file_count=len(manifest["files"]),
                    bytes=sum(row["bytes"] for row in manifest["files"].values()),
                    plans_used=False, map_files_opened=False, timestamps_preserved=True)
    manifest_path = HERE / "measurements/cache-seeding.json"
    if manifest_path.exists():
        previous = manifest_path.read_bytes()
        archive = HERE / "measurements/cache-history"
        archive.mkdir(parents=True, exist_ok=True)
        (archive / (hashlib.sha256(previous).hexdigest() + ".json")).write_bytes(previous)
    _write_json(manifest_path, manifest)
    print(json.dumps({k: manifest[k] for k in ("file_count", "bytes", "plans_used", "map_files_opened")}), flush=True)


def verify_seed_dispatch():
    source = ROOT / "experiments/mvp/pipeline/maps/runtime/experiments/cache/stability"
    destination = HERE / "runtime-cache/stability"
    pattern = re.compile(r"liquid-([0-9a-f]{64})-[0-9a-f]{16}\.npz")
    def selected(directory):
        groups = defaultdict(list)
        for path in directory.glob("liquid-*.npz"):
            match = pattern.fullmatch(path.name)
            if match:
                groups[match[1]].append(path)
        return {base: sorted(paths, key=lambda path: path.stat().st_mtime_ns, reverse=True)[0].stem.removeprefix("liquid-")
                for base, paths in groups.items()}
    expected, actual = selected(source), selected(destination)
    mismatches = {base: dict(source=identity, destination=actual.get(base))
                  for base, identity in expected.items() if actual.get(base) != identity}
    manifests = []
    for path in source.glob("liquid-vessel-*.json"):
        value = json.loads(path.read_text())
        keys = value.get("tank_table_keys", {})
        source_matches = bool(keys) and all(expected.get(identity.split("-")[0]) == identity for identity in keys.values())
        if source_matches:
            destination_matches = all(actual.get(identity.split("-")[0]) == identity for identity in keys.values())
            manifests.append(dict(name=path.name, tanks=len(keys), source_grid_keys_match=True,
                                  destination_grid_keys_match=destination_matches))
    report = dict(passed=not mismatches and bool(manifests)
                  and all(row["destination_grid_keys_match"] for row in manifests),
                  selected_grid_groups=len(expected), mismatches=mismatches,
                  source_matching_manifests=manifests,
                  source=str(source.relative_to(ROOT)), destination=str(destination.relative_to(ROOT)),
                  method="Native newest-mtime grid selection and every matching vessel manifest tank key; no qualification override")
    from experiments.mvp.pipeline.binding.runtime import runtime_cache_scope
    from stowage.checker import stability_liquid
    requested = []
    with runtime_cache_scope():
        for vessel in ("feeder_500", "feeder_800"):
            params = tomllib.loads((ROOT / "params/fleet" / (vessel + ".toml")).read_text())
            metadata = stability_liquid.metadata(params)
            path = stability_liquid._manifest_path(metadata)
            value = json.loads(path.read_text()) if path.exists() else {}
            keys = value.get("tank_table_keys", {})
            matches = bool(keys) and value.get("metadata") == metadata and all(
                actual.get(identity.split("-")[0]) == identity for identity in keys.values())
            requested.append(dict(vessel=vessel, manifest=path.name,
                                  metadata_and_selected_tank_keys_match=matches, tanks=len(keys)))
    report["requested_vessels"] = requested
    report["passed"] = report["passed"] and all(row["metadata_and_selected_tank_keys_match"] for row in requested)
    _write_json(HERE / "measurements/cache-dispatch-verification.json", report)
    if not report["passed"]:
        raise ValueError("physical cache grid dispatch differs from source")
    print(json.dumps(dict(passed=True, selected_grid_groups=len(expected), matching_manifests=len(manifests))), flush=True)


def profile(name):
    from engine.config import Config
    if name == "default":
        return None, None, None
    options = dict(work_count_search=False, warm_polish=False) if name == "live-v2" else {}
    return 90., 30., Config(assignment_budget_s=60., assignment_finalize_s=20.,
        assignment_tries=1, lift_polish_s=5., peel_proposal_s=20.,
        peel_finalize_s=10., peel_candidates=1, proof_outer_s=10.,
        post_pass_exchange_s=5., area_securing_s=10., **options)


_BINDING_SNAPSHOT = None


def binding_snapshot():
    """Fingerprint imported callable bytecode and preserve source for each worker."""
    global _BINDING_SNAPSHOT
    if _BINDING_SNAPSHOT is not None:
        return _BINDING_SNAPSHOT
    code = hashlib.sha256()
    source_files = {}
    for name in ("adapter", "hooks", "admission", "runtime", "allocation"):
        module = importlib.import_module("experiments.mvp.pipeline.binding." + name)
        path = Path(module.__file__)
        raw = path.read_bytes()
        source_files[name + ".py"] = raw
        for symbol, value in sorted(vars(module).items()):
            if getattr(value, "__module__", None) == module.__name__ and hasattr(value, "__code__"):
                while hasattr(value, "__code__"):
                    code.update((module.__name__ + "." + symbol).encode())
                    code.update(marshal.dumps(value.__code__))
                    value = getattr(value, "__wrapped__", None)
    source = hashlib.sha256()
    for name, raw in sorted(source_files.items()):
        source.update(name.encode())
        source.update(raw)
    directory = HERE / "measurements/snapshots" / source.hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    for name, raw in source_files.items():
        (directory / name).write_bytes(raw)
    _BINDING_SNAPSHOT = dict(source_sha256=source.hexdigest(),
        imported_callable_sha256=code.hexdigest(), source_snapshot=str(directory.relative_to(ROOT)))
    return _BINDING_SNAPSHOT


def _key(cell, path, name, answer):
    raw = json.dumps([cell, path, name, answer], sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def expected_identity(cell, path, name, envelope, instance=None, *, engine=None, implementation=None):
    from stowage.io import load_instance
    from experiments.mvp.pipeline.binding.adapter import verify_manifest
    from experiments.mvp.pipeline.binding.measurement_identity import (
        build_identity, implementation_identity, resolved_settings)
    instance = instance if instance is not None else load_instance(ROOT / path)
    budget, finish, config = profile(name)
    return build_identity(input_path=ROOT / path, cell=cell, profile=name, rule=envelope,
        settings=resolved_settings(instance, config, budget, finish), seed=0,
        engine_identity=verify_manifest() if engine is None else engine,
        implementation=implementation_identity(HERE) if implementation is None else implementation)


def current_rows(out, *, persist=True):
    """Current compatible evidence only; finished JSON rows remain immutable."""
    from experiments.mvp.pipeline.binding.adapter import verify_manifest
    from experiments.mvp.pipeline.binding.measurement_identity import compatibility, implementation_identity
    files = sorted(out.glob("*.json"))
    engine, implementation = verify_manifest(), implementation_identity(HERE)
    eligible, stale = [], []
    for path in files:
        row = json.loads(path.read_text())
        expected = expected_identity(row["cell"], row["map"], row["profile"],
                                     row.get("canonical_rule"), engine=engine, implementation=implementation)
        valid, reasons = compatibility(row, expected)
        if not engine.get("passed"):
            valid, reasons = False, reasons + ["current pinned engine/native verification failed"]
        if valid:
            eligible.append(row)
        else:
            stale.append(dict(key=row["key"], passed=bool(row.get("passed")), reasons=reasons,
                              row_file=path.name, row_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    latest = {}
    for row in sorted(eligible, key=lambda row: (row.get("finished_at", 0), row["key"])):
        logical = _key(row["cell"], row["map"], row["profile"], row.get("canonical_rule"))
        if logical in latest:
            prior = latest[logical]
            stale.append(dict(key=prior["key"], passed=bool(prior.get("passed")),
                              reasons=["superseded compatible attempt"], row_file=prior["key"] + ".json"))
        latest[logical] = row
    report = dict(observed_at=time.time(), historical_rows=len(files), current_rows=len(latest),
                  stale_rows=stale, stale_keys=sorted(row["key"] for row in stale),
                  engine_identity=engine, implementation=implementation,
                  note="Historical row files are immutable. Legacy/running old implementations are not current-compatible. No automatic reruns.")
    if persist:
        _write_json(out.parent / "current-compatibility.json", report)
    return list(latest.values()), report


def candidates(instance, kind):
    from experiments.mvp.reader.schema import context_from_instance, validate_answer
    from llm.instructions.apply import _dg_classes
    context = context_from_instance(instance)
    out = []
    if kind == "close_bay":
        out = [dict(type=kind, bay=b) for b in context["bays"] if b % 2]
    elif kind == "max_tiers":
        out = [dict(type=kind, bay=b, zone=z, max_tiers=t)
               for b in context["bays"] if b % 2
               for z in ("hold", "deck")
               for t in range(context["max_tiers_by_bay"][str(b)][z])]
    elif kind == "dg_keep_away":
        active = set(instance.load_list) | {p.container_id for p in instance.onboard.placements}
        classes = sorted({c for box in instance.containers if box.id in active for c in _dg_classes(box)})
        out = [dict(type=kind, imdg_class=c, min_bays=k, away_from=dict(kind="bay", bay=b))
               for c in classes for b in context["bays"] if b % 2 for k in (1, 2, 3)]
        if context["has_accommodation"]:
            out += [dict(type=kind, imdg_class=c, min_bays=k, away_from=dict(kind="accommodation"))
                    for c in classes for k in range(1, 21)]
    elif kind == "one_port_per_hatch":
        out = [dict(type=kind)]
    answers = []
    for value in out:
        try:
            answers.append(validate_answer(json.dumps(dict(schema_version="stowlm.reader/2", constraint=value)), context))
        except ValueError:
            pass
    return answers


def select_rules(instance, baseline_plan, kind, count=1):
    from experiments.mvp.reader.pipeline import intrinsic_violations
    from experiments.mvp.reader.occupancy import apply_reader_constraint
    values = []
    for answer in candidates(instance, kind):
        errors = intrinsic_violations(instance, answer, baseline_plan) if baseline_plan is not None else []
        shortfall = None
        removed = 0
        if kind != "one_port_per_hatch":
            compiled = apply_reader_constraint(instance, answer)
            shortfall = compiled.capacity_shortfall()
            removed = len(compiled._all_boxes) + sum(len(cells) for box, cells in compiled._per_box.items()
                                                     if box in instance.load_list)
        # Prefer a binding instruction with the fewest occupied destinations
        # excluded. An instruction that only proves infeasibility is kept last.
        signal = bool(errors) if baseline_plan is not None else bool(removed)
        rank = (bool(shortfall), not signal, len(errors) if baseline_plan is not None else removed,
                json.dumps(answer.model_dump(), sort_keys=True))
        values.append((rank, answer, errors, shortfall))
    values.sort(key=lambda row: row[0])
    return [(a, errors, shortfall) for _, a, errors, shortfall in values[:count]]


def run_one(cell, path, name, answer=None, baseline=None, out=None):
    from stowage.io import load_instance
    from experiments.mvp.reader.schema import answer_envelope
    from experiments.mvp.pipeline.binding.measurement_identity import compatibility, attempt_key
    from experiments.mvp.pipeline.binding.measurement_lifecycle import Attempt, durable_json, preserve_unfinished
    input_digest = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
    instance = load_instance(ROOT / path)
    envelope = answer_envelope(answer) if answer is not None else None
    logical = _key(cell, path, name, envelope)
    identity = expected_identity(cell, path, name, envelope, instance)
    if identity["payload"]["input_sha256"] != input_digest:
        raise ValueError("measurement input changed while loading; no attempt started")
    compatible = []
    for candidate in out.glob("*.json"):
        cached = json.loads(candidate.read_text())
        valid, _ = compatibility(cached, identity)
        if valid and identity["payload"]["engine"].get("passed"):
            compatible.append(cached)
    if compatible:
        cached = max(compatible, key=lambda row: (row.get("finished_at", 0), row["key"]))
        if not RERUN_FAILURES or cached.get("passed"):
            return cached
    # This function is invoked only by an explicit measurement command. A stale
    # success has exactly the same rejection behavior as a stale failure.
    recovered = preserve_unfinished(out.parent, logical, out.parent / "progress")
    state = admit()
    budget, finish, config = profile(name)
    attempt = Attempt(out.parent, logical, identity, dict(cell=cell, map=path, profile=name))
    key = attempt_key(identity, attempt.id)
    row = dict(key=key, logical_key=logical, attempt_id=attempt.id, measurement_identity=identity,
               map=path, id=instance.id, cell=cell, profile=name,
               rule_type=answer.type if answer is not None else "baseline",
               canonical_rule=envelope, budget_s=budget, finalize_s=finish,
               config=asdict(config) if config is not None else None,
               admission=state, baseline_key=baseline.get("key") if baseline else None,
               pid=os.getpid(), started_at=attempt.started_at, recovered_prior_evidence=recovered,
               attempt_directory=str(attempt.path.relative_to(ROOT)))
    trace = None
    print(json.dumps(dict(event="started", key=key, attempt_id=attempt.id, cell=cell,
                          profile=name, rule_type=row["rule_type"], id=instance.id, pid=os.getpid())), flush=True)
    def checked_incumbent(event):
        attempt.incumbent(event)
        row.setdefault("first_checked_wall_s", event["wall_s"])
        row["checked_incumbents"] = row.get("checked_incumbents", 0) + 1
        print(json.dumps(dict(event="checked_incumbent", key=key, attempt_id=attempt.id,
                              lifts=event["lifts"], wall_s=event["wall_s"])), flush=True)
    try:
        seeding = HERE / "measurements/cache-seeding.json"
        row["physical_cache_seeded_at_start"] = seeding.exists()
        if seeding.exists():
            seeded = json.loads(seeding.read_text())
            row["cache_seeding"] = dict(manifest_sha256=hashlib.sha256(seeding.read_bytes()).hexdigest(),
                completed_at=seeded["completed_at"], files=seeded["file_count"], bytes=seeded["bytes"])
        trace = (attempt.path / "stack.log").open("x")
        faulthandler.dump_traceback_later(60., repeat=True, file=trace)
        from experiments.mvp.pipeline.binding import plan_with_rules
        row["binding_snapshot"] = binding_snapshot()
        if answer is None:
            row["baseline_method"] = "adapter with no rules; checked incumbent stream; original-instance release gate"
        result, evidence = plan_with_rules(instance, [] if answer is None else [answer], budget_s=budget,
            finalize_s=finish, config=config, seed=0, on_incumbent=checked_incumbent)
        row.update(evidence=evidence, passed=bool(evidence.get("passed")),
                   lifts=evidence.get("lifts"), lane=evidence.get("lane"))
        if result is not None and result.plan is not None:
            row["plan"] = result.plan.model_dump(mode="json")
            row["result_status"] = result.status
            row["result_shippable"] = getattr(result, "shippable", None)
        row["wall_s"] = time.monotonic() - attempt.started_monotonic
        row["finished_at"] = time.time()
        attempt.finish(row)
        durable_json(out / (key + ".json"), row)
    except BaseException as error:
        attempt.unknown(error)
        raise
    finally:
        faulthandler.cancel_dump_traceback_later()
        if trace is not None:
            trace.close()
    print(json.dumps({k: row.get(k) for k in ("key", "cell", "profile", "rule_type", "passed", "wall_s", "lifts")}), flush=True)
    return row


def summarize(out, *, report_name="RESULTS.md"):
    rows, compatibility_report = current_rows(out)
    by_key = {row["key"]: row for row in rows}
    by_logical = {_key(row["cell"], row["map"], row["profile"], row.get("canonical_rule")): row for row in rows}
    for row in rows:
        if row["rule_type"] == "baseline":
            continue
        base = by_logical.get(_key(row["cell"], row["map"], row["profile"], None))
        if not base or not base.get("passed") or not base.get("plan"):
            if row.get("baseline_instruction_violations"):
                row.update(baseline_instruction_violations=[], baseline_obeyed=None,
                           baseline_comparison_pending=True, baseline_plan_sha256=None)
            continue
        baseline_plan_sha256 = hashlib.sha256(json.dumps(base["plan"], sort_keys=True).encode()).hexdigest()
        if row.get("baseline_plan_sha256") == baseline_plan_sha256:
            continue
        from stowage.io import load_instance
        from stowage.model import MovePlan
        from experiments.mvp.reader.pipeline import intrinsic_violations
        from experiments.mvp.reader.schema import validate_answer, context_from_instance
        instance = load_instance(ROOT / row["map"])
        answer = validate_answer(json.dumps(row["canonical_rule"]), context_from_instance(instance))
        violations = intrinsic_violations(instance, answer, MovePlan.model_validate(base["plan"]))
        row.update(baseline_key=base["key"], baseline_instruction_violations=violations,
                   baseline_obeyed=not violations, baseline_comparison_pending=False,
                   baseline_plan_sha256=baseline_plan_sha256)
    groups = defaultdict(list)
    for row in rows:
        groups[(row["rule_type"], row["cell"], row["profile"])].append(row)
    lookup = {row["key"]: row for row in rows}
    lines = ["# Engine 0.13.0 instruction binding: development measurements", "",
        f"CURRENT COMPATIBLE EVIDENCE ONLY: {len(rows)} rows; {len(compatibility_report['stale_rows'])} historical/stale rows excluded from every denominator and required coverage. See `measurements/current-compatibility.json` for exact keys and reasons. Historical rows are immutable and remain evidence of their original implementation. Cached passes and failures require the same strict identity gate.", "",
        "The original campaign was interrupted before completion. Its recorded process handles no longer existed at the read-only status check; no stop or restart was issued. Four unfinished attempts and their incumbent/stack evidence were preserved as unknown. `RESULTS-HISTORICAL.md` preserves the earlier table; current coverage is in `measurements/current-coverage.json`. No fresh compatible campaign has been launched automatically.", "",
        "Measured development data only. `live` (shown as live-v1) and `live-v2` request 90 s extra work and 30 s finishing, with additive stage budgets recorded in each row. `live-v2` additionally sets `work_count_search=False,warm_polish=False` to remove the medium 900 s warm-polish floor; medium uses this profile. Tiny/small early live-v1 rows retain those medium-specific flags. These are not wall-clock deadlines: for at most 60 load boxes the engine preserves historical search and a 300 s finishing floor, potentially applied to multiple candidates; atomic checks can also overrun. `default` uses untouched engine Config and default budgets. The historical campaign used seed 0, BLAS threads 1 and fleet admission at each start, with at most four numerical jobs concurrently. Those are historical start conditions, not a claim that jobs remain active; source variants remain disclosed in the immutable rows and all are excluded from current coverage.", "",
        "| Rule type | Cell / budget | n | Passes | Screen refusals | Searched no-plan | Baseline violated | Binding passes | Median s | Max s | Median lifts / baseline |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    expected = [(kind, cell, name) for kind in
                ("baseline", "close_bay", "max_tiers", "dg_keep_away", "one_port_per_hatch")
                for cell in ("tiny", "small", "medium")
                for name in (("live-v2", "default") if cell == "medium" else ("live", "default"))]
    for kind, cell, name in expected:
        group = groups[(kind, cell, name)]
        times = [r["wall_s"] for r in group]
        passed = [r for r in group if r.get("passed")]
        pairs = [(float(r["lifts"]), float(lookup[r["baseline_key"]]["lifts"])) for r in passed
                 if r.get("lifts") is not None and r.get("baseline_key") in lookup
                 and lookup[r["baseline_key"]].get("passed")
                 and lookup[r["baseline_key"]].get("lifts") is not None]
        lifts, bases = [pair[0] for pair in pairs], [pair[1] for pair in pairs]
        def med(xs):
            return f"{statistics.median(xs):.1f}" if xs else "—"
        violated = sum(bool(r.get("baseline_instruction_violations")) for r in group)
        binding_passes = sum(bool(r.get("baseline_instruction_violations")) for r in passed)
        screen_refusals = sum(r.get("evidence", {}).get("status") == "constraint_unavailable"
                              and not r.get("evidence", {}).get("engine") for r in group)
        searched_no_plan = sum(r.get("evidence", {}).get("status") == "no_checked_plan" for r in group)
        maximum = f"{max(times):.1f}" if times else "—"
        label = "live-v1" if name == "live" else name
        comparison = f"{med(lifts)} / {med(bases)} (n={len(pairs)})"
        if kind == "baseline":
            comparison = f"{med([float(r['lifts']) for r in passed if r.get('lifts') is not None])} / —"
        lines.append(f"| {kind} | {cell} / {label} | {len(group)} | {len(passed)} | {screen_refusals} | {searched_no_plan} | {violated if kind != 'baseline' else '—'} | {binding_passes if kind != 'baseline' else '—'} | {med(times)} | {maximum} | {comparison} |")
    lines += ["", "Each JSON row stores its exact rule, input path, admission reading, original engine attribution, checker evidence and plan when released. Rules are selected deterministically after the corresponding baseline: prefer a rule that baseline violates, with the fewest excluded placements and no simple capacity shortfall. This is a development selection procedure, not a held-out comparison.", "",
        "Lift comparisons use only identical paired subsets where both the rule run and the matched same-profile baseline passed; paired n appears in the final column. Source snapshots and cache conditions are retained per row. New baseline runs use the adapter with an empty rule list to record checked incumbent events; earlier native-entry baseline rows remain unchanged. `first_checked_wall_s` and progress events describe the first legal publication, while table wall seconds describe the final returned plan after continuation and the mandatory release recheck.", "",
        "Quick tiny contains only one natural DG-bearing map; its DG cell uses three distinct anchor/radius rules on that map, explicitly repeated. Other types use three tiny maps. Small uses three naturally DG-bearing maps. The frozen medium cohort contains four in-envelope medium12 feeder_500 maps plus two explicitly named C6 development maps: the fresh pipeline development cohort was absent when selection was frozen. No sealed paths are enumerated or opened.", "",
        "The target is n=3 per rule/budget on tiny and small and n=6 on medium (96 constrained runs plus 24 shared baselines). `Binding passes` counts only passes whose corresponding no-rule plan violated that exact instruction. Baseline-obeyed instructions prove rechecking but do not demonstrate a binding replan. Rows still absent from the requested 24 rule/cell/budget groups are unmeasured, not successes. Full default measurements may be much slower than the live profile."]
    late_dev = HERE / "measurements/late-dev-cohort.json"
    if late_dev.exists():
        cohort = json.loads(late_dev.read_text())
        lines += ["", f"The fresh 0.6 development cohort was observed later at {cohort['observed_at_utc']} ({len(cohort['fresh_development_maps'])} maps). Its paths, SHA-256 digests and timestamps are recorded in `measurements/late-dev-cohort.json`; existing paired comparisons retain the frozen cohort. Supplemental development runs wait until the required matrix completes."]
    seeded = HERE / "measurements/cache-seeding.json"
    if seeded.exists():
        cache = json.loads(seeded.read_text())
        lines += ["", "## Cache conditions and provenance", "",
            f"The cache-seeding manifest records {cache['file_count']} SHA-verified physical acceleration artifacts ({cache['bytes']/1e9:.3f} GB), copied from the independent 0.6 runtime at Unix time {cache['completed_at']:.3f}. No cargo maps or plans were copied. New rows explicitly record `physical_cache_seeded_at_start` and the seed manifest digest; source snapshots include runtime cache path controls. Early rows predate complete cache path isolation and may have accessed pre-existing shared native numerical caches; no shared files were cleaned up. Their wall times remain recorded as observed, with no claim that all early runs were uniformly cold or warm. Active pre-seed processes may encounter newly copied artifacts later in a run."]
    interrupted = sorted((HERE / "measurements").glob("interrupted*.json"))
    from experiments.mvp.pipeline.binding.measurement_lifecycle import unknown_attempts
    unknown = unknown_attempts(out.parent)
    _write_json(out.parent / "unknown-attempts.json", unknown)
    if unknown:
        lines += ["", "## Automatic attempt lifecycle (excluded from final denominators)", ""]
        for item in unknown:
            lines.append(f"- `{item['attempt_id']}` / `{item['logical_key']}`: unknown; {item['detail']['reason']}. Evidence: `{item['evidence_directory']}`.")
    if interrupted:
        lines += ["", "## Interrupted diagnostics (excluded from n and pass rates)", ""]
        for path in interrupted:
            row = json.loads(path.read_text())
            elapsed = row.get("observed_wall_s")
            timing = f"{elapsed:.1f} s observed" if elapsed is not None else f">={row.get('observed_wall_lower_bound_s', 0)} s observed; exact duration unknown"
            lines.append(f"- `{path.name}`: {timing}; verdict unknown. {row['reason']}")
    refusals = [r for r in rows if r.get("evidence", {}).get("status") == "constraint_unavailable"]
    if refusals:
        lines += ["", "## Screen refusal reasons", ""]
        lines += ["`allocation: UNKNOWN` is an allocation solver timeout, not a proof that the instruction is impossible. `INFEASIBLE` and the explicit necessary capacity shortfall are recorded separately by their exact reason.", ""]
        for row in refusals:
            reason = row["evidence"].get("reason", "unspecified")
            lines.append(f"- {row['cell']} / {row['profile']} / {row['rule_type']} / `{row['id']}`: {reason} ({row['wall_s']:.2f} s).")
    temporary = HERE / f"RESULTS.{os.getpid()}.tmp"
    temporary.write_text("\n".join(lines) + "\n")
    temporary.replace(HERE / report_name)
    return rows


def campaign_groups(rows):
    """Audit each requested minimum, fixed map coverage and unique identities."""
    expected = {(kind, cell, name): (6 if cell == "medium" else 3)
                for kind in ("baseline", "close_bay", "max_tiers", "dg_keep_away", "one_port_per_hatch")
                for cell in ("tiny", "small", "medium")
                for name in (("live-v2", "default") if cell == "medium" else ("live", "default"))}
    groups = defaultdict(list)
    identity_errors = []
    selected = maps()
    keys = set()
    for row in rows:
        group = (row["rule_type"], row["cell"], row["profile"])
        groups[group].append(row)
        key = row["key"]
        if key in keys:
            identity_errors.append(dict(key=key, reason="duplicate key"))
        keys.add(key)
        canonical = row.get("canonical_rule") if row["rule_type"] != "baseline" else None
        from experiments.mvp.pipeline.binding.measurement_identity import attempt_key
        expected_key = (attempt_key(row["measurement_identity"], row["attempt_id"])
                        if row.get("measurement_identity") else _key(row["cell"], row["map"], row["profile"], canonical))
        if key != expected_key:
            identity_errors.append(dict(key=key, reason="key does not match canonical inputs"))
        if row["map"] not in selected.get(row["cell"], []):
            identity_errors.append(dict(key=key, reason="map outside frozen cell"))
        # Early completed rows predate explicit finished_at metadata. They have
        # observed final wall_s and a final evidence/error record, not a claimed
        # reconstructed timestamp.
        if not isinstance(row.get("wall_s"), (float, int)) or not (
                isinstance(row.get("evidence"), dict) or row.get("error")):
            identity_errors.append(dict(key=key, reason="row lacks final timing"))
    audits = []
    for group, n in expected.items():
        found = groups[group]
        required_maps = set(selected[group[1]])
        if group[:2] == ("dg_keep_away", "tiny"):
            # The documented DG cell repeats three distinct instructions on
            # tiny's sole naturally DG-bearing map.
            required_maps = {selected["tiny"][2]}
        observed_maps = {row["map"] for row in found}
        missing_maps = sorted(required_maps - observed_maps)
        audits.append(dict(rule_type=group[0], cell=group[1], profile=group[2],
            expected_n=n, finalized_n=len(found), exact_n=len(found) == n,
            minimum_met=len(found) >= n and not missing_maps,
            missing_maps=missing_maps, observed_maps=sorted(observed_maps),
            passes=sum(bool(row.get("passed")) for row in found),
            keys=sorted(row["key"] for row in found)))
    unexpected = [dict(rule_type=group[0], cell=group[1], profile=group[2], n=len(values))
                  for group, values in groups.items() if group not in expected]
    return dict(all_attempts_finalized=all(row["minimum_met"] for row in audits)
                and not identity_errors and not unexpected,
                expected_groups=30, expected_rows=120, finalized_rows=len(rows),
                unique_keys=len(keys), groups=audits, identity_errors=identity_errors,
                unexpected_groups=unexpected)


def monitor_campaign(out, *, poll_s=15.):
    """Non-numerical observer: no engine planning, subprocesses or signals."""
    own_pid = os.getpid()
    sources = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted(HERE.glob("*.py"))}
    started = time.time()
    status_path = out.parent / "campaign-progress.json"
    last_signature = None
    last_results_mtime = None
    print(json.dumps(dict(event="monitor_started", pid=own_pid, poll_s=poll_s,
                          numerical_workers=0)), flush=True)
    while True:
        files = sorted(out.glob("*.json"))
        signature = tuple((path.name, path.stat().st_mtime_ns, path.stat().st_size) for path in files)
        result_path = HERE / "RESULTS.md"
        result_mtime = result_path.stat().st_mtime_ns if result_path.exists() else None
        if signature != last_signature or result_mtime != last_results_mtime:
            rows = summarize(out)
            audit = campaign_groups(rows)
            audit.update(observed_at=time.time(), monitor_started_at=started,
                         monitor_pid=own_pid, numerical_workers=0,
                         write_fence=str(HERE.relative_to(ROOT)))
            _write_json(status_path, audit)
            last_signature = signature
            last_results_mtime = result_path.stat().st_mtime_ns
            print(json.dumps(dict(event="campaign_progress", finalized_rows=len(rows),
                                  complete_groups=sum(row["minimum_met"] for row in audit["groups"]),
                                  all_attempts_finalized=audit["all_attempts_finalized"])), flush=True)
            if audit["all_attempts_finalized"]:
                from experiments.mvp.pipeline.binding.adapter import verify_manifest
                identity = verify_manifest()
                final_sources = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in sorted(HERE.glob("*.py"))}
                changed = sorted(name for name in sources.keys() | final_sources.keys()
                                 if sources.get(name) != final_sources.get(name))
                variants = sorted({row.get("binding_snapshot", {}).get("source_sha256", "early native baseline")
                                   for row in rows})
                audit.update(completed_at=time.time(), pinned_manifest=identity,
                    binding_source_manifest_at_monitor_start=sources,
                    binding_source_manifest_at_completion=final_sources,
                    binding_source_changes_during_monitor=changed,
                    binding_source_variants=variants,
                    source_snapshot_note="Early workers and rows retain earlier source snapshots; final mandatory instruction and original-instance checks govern release.",
                    outputs_within_write_fence=["RESULTS.md", "measurements/campaign-progress.json",
                                               "measurements/campaign-complete.json"],
                    completion_verified=bool(identity["passed"]) and not changed)
                _write_json(out.parent / "campaign-complete.json", audit)
                print(json.dumps(dict(event="campaign_complete", finalized_rows=len(rows),
                                      completion_verified=audit["completion_verified"])), flush=True)
                return audit
        time.sleep(poll_s)


def main():
    global RERUN_FAILURES
    parser = argparse.ArgumentParser()
    parser.add_argument("--cells", nargs="+", choices=("tiny", "small", "medium"), default=["tiny", "small", "medium"])
    parser.add_argument("--profiles", nargs="+", choices=("live", "live-v2", "default"), default=["live-v2", "default"])
    parser.add_argument("--phase", choices=("all", "baseline", "rules", "rules-first", "summary", "current", "seed", "verify-seed", "monitor"), default="all")
    parser.add_argument("--rules", nargs="+", default=["close_bay", "max_tiers", "dg_keep_away", "one_port_per_hatch"])
    parser.add_argument("--map-indices", nargs="+", type=int, help="Optional zero-based indices within each cell for restarting a subset")
    parser.add_argument("--rerun-failures", action="store_true", help="Archive previous nonpassing rows and remeasure after an adapter change")
    args = parser.parse_args()
    RERUN_FAILURES = args.rerun_failures
    out = HERE / "measurements" / "rows"
    out.mkdir(parents=True, exist_ok=True)
    if args.phase == "monitor":
        monitor_campaign(out)
        return
    if args.phase == "seed":
        seed_physical_caches()
        return
    if args.phase == "verify-seed":
        verify_seed_dispatch()
        return
    if args.phase in ("summary", "current"):
        rows = summarize(out, report_name="RESULTS-CURRENT.md" if args.phase == "current" else "RESULTS.md")
        _write_json(out.parent / "current-coverage.json", campaign_groups(rows))
        return
    selected = maps()
    for name in args.profiles:
        for cell in args.cells:
            paths = selected[cell]
            if args.map_indices is not None:
                paths = [paths[i] for i in args.map_indices]
            for path in paths:
                if args.phase == "rules-first":
                    from stowage.io import load_instance
                    from stowage.model import MovePlan
                    instance = load_instance(ROOT / path)
                    eligible, _ = current_rows(out)
                    base = next((row for row in eligible if row["cell"] == cell and row["map"] == path
                                 and row["profile"] == name and row["rule_type"] == "baseline"), None)
                    plan = MovePlan.model_validate(base["plan"]) if base and base.get("passed") and base.get("plan") else None
                    for kind in args.rules:
                        count = 3 if cell == "tiny" and kind == "dg_keep_away" else 1
                        for answer, violations, shortfall in select_rules(instance, plan, kind, count):
                            row = run_one(cell, path, name, answer, base, out)
                            row.update(baseline_instruction_violations=violations,
                                       baseline_obeyed=not violations if plan is not None else None,
                                       baseline_comparison_pending=plan is None,
                                       simple_capacity_shortfall=shortfall)
                            if base:
                                row["baseline_key"] = base["key"]
                            _write_json(out.parent / "comparisons" / (row["key"] + ".json"), row)
                            summarize(out)
                    continue
                base = run_one(cell, path, name, out=out)
                summarize(out)
                if args.phase == "baseline":
                    continue
                from stowage.io import load_instance
                from stowage.model import MovePlan
                from experiments.mvp.reader.schema import answer_envelope
                instance = load_instance(ROOT / path)
                plan = MovePlan.model_validate(base["plan"]) if base.get("passed") and base.get("plan") else None
                for kind in args.rules:
                    count = 3 if cell == "tiny" and kind == "dg_keep_away" else 1
                    for answer, violations, shortfall in select_rules(instance, plan, kind, count):
                        row = run_one(cell, path, name, answer, base, out)
                        row.update(baseline_instruction_violations=violations,
                                   baseline_obeyed=not violations if plan is not None else None,
                                   baseline_key=base["key"], baseline_comparison_pending=plan is None,
                                   simple_capacity_shortfall=shortfall)
                        _write_json(out.parent / "comparisons" / (row["key"] + ".json"), row)
                        summarize(out)


if __name__ == "__main__":
    main()
