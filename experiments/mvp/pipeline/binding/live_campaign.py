"""Three-worker, medium-first diagnostic campaign. No sealed inputs are read."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import statistics
import time
import uuid

from . import measure
from .measurement_lifecycle import durable_json

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
ROWS = HERE / "measurements/rows"
KINDS = ("close_bay", "max_tiers", "dg_keep_away", "one_port_per_hatch")
PROFILE = "live-v2"


def file_identity(path):
    path = Path(path)
    raw = path.read_bytes()
    return dict(path=str(path.relative_to(ROOT)), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


def cohort():
    """Freeze all32 public development hashes; choose r0 in all8 strata."""
    listing = ROOT / "experiments/mvp/pipeline/maps/dev.txt"
    paths = [line.strip() for line in listing.read_text().splitlines() if line.strip()]
    if len(paths) != 32 or len(set(paths)) != 32:
        raise ValueError("expected32 unique development maps")
    pattern = re.compile(r"(feeder_500|feeder_800)-(random_yard|simulated_yard)-f(60|70)-r([0-3])\.json")
    all_inputs, selected, strata = [], [], set()
    for relative in paths:
        path = (ROOT / relative).resolve()
        if path.parent != (ROOT / "experiments/mvp/pipeline/maps/dev").resolve():
            raise ValueError("development path outside explicit dev directory")
        match = pattern.fullmatch(path.name)
        if not match:
            raise ValueError("unexpected development map name")
        ship, yard, fill, replicate = match.groups()
        record = dict(file_identity(path), ship=ship, yard=yard, fill=int(fill), replicate=int(replicate))
        all_inputs.append(record)
        if replicate == "0":
            stratum = (ship, fill, yard)
            if stratum in strata:
                raise ValueError("duplicate r0 stratum")
            strata.add(stratum)
            selected.append(dict(record, cell="medium"))
    if len(selected) != 8:
        raise ValueError("missing r0 development stratum")
    frozen = json.loads((HERE / "measurements/maps.json").read_text())
    opportunistic = [dict(file_identity(ROOT / path), cell=cell)
                     for cell in ("tiny", "small") for path in frozen[cell]]
    return dict(development_listing=file_identity(listing), all32_inputs=all_inputs,
                selected_medium=selected, opportunistic=opportunistic,
                diagnostic_only=True, sealed_calibration=False,
                selection="r0 in each ship×fill×yard stratum; no sealed inputs")


def dg_votes(instance):
    from llm.instructions.apply import _class_matches, _dg_classes
    from experiments.mvp.reader.schema import IMDG_CLASSES
    active = set(instance.load_list)
    votes = Counter()
    for box in instance.containers:
        if box.id not in active:
            continue
        classes = _dg_classes(box)
        for candidate in IMDG_CLASSES:
            if _class_matches(candidate, classes):
                votes[candidate] += 1  # exactly one vote per box/candidate
    return votes


def rank_candidate(instance, baseline_plan, answer):
    from experiments.mvp.reader.pipeline import intrinsic_violations
    from experiments.mvp.reader.occupancy import apply_reader_constraint
    violations = intrinsic_violations(instance, answer, baseline_plan) if baseline_plan is not None else None
    constrained = apply_reader_constraint(instance, answer)
    removed = len(constrained._all_boxes) + sum(len(cells) for box, cells in constrained._per_box.items()
                                              if box in instance.load_list)
    # First prefer actual baseline violations; among binding choices change the
    # fewest occupied placements. A missing baseline remains unknown.
    rank = (not bool(violations) if violations is not None else not bool(removed),
            len(violations) if violations else removed, json.dumps(answer.model_dump(), sort_keys=True))
    return rank, violations


def select_rule(instance, baseline_plan, kind):
    from experiments.mvp.reader.schema import context_from_instance, validate_answer
    context = context_from_instance(instance)
    if kind == "close_bay":
        from experiments.mvp.reader.occupancy import apply_reader_constraint
        candidates = []
        for answer in measure.candidates(instance, kind):
            rank, violations = rank_candidate(instance, baseline_plan, answer)
            shortfall = apply_reader_constraint(instance, answer).capacity_shortfall()
            candidates.append(((rank[0], bool(shortfall), *rank[1:]), answer, violations, shortfall))
        if not candidates:
            raise ValueError("no validated close_bay rule")
        _, answer, violations, shortfall = min(candidates, key=lambda row: row[0])
        return answer, dict(selection="close_bay baseline-binding first, capacity secondary", baseline_violations=violations,
                            simple_capacity_shortfall=shortfall)
    if kind == "one_port_per_hatch":
        answer = validate_answer(json.dumps(dict(schema_version="stowlm.reader/2", constraint=dict(type=kind))), context)
        return answer, dict(selection="one_port_per_hatch fixed instruction")
    values, details = [], {}
    if kind == "max_tiers":
        for bay in context["bays"]:
            if not bay % 2:
                continue
            for zone in ("deck" if context["max_tiers_by_bay"][str(bay)]["deck"] else "hold",):
                physical = context["max_tiers_by_bay"][str(bay)][zone]
                if physical:
                    values.append(dict(type=kind, bay=bay, zone=zone, max_tiers=max(0, physical - 2)))
        details = dict(selection="bay/zone physical depth minus2, floor0; prefer baseline violated")
    elif kind == "dg_keep_away":
        votes = dg_votes(instance)
        selected = min(votes, key=lambda value: (-votes[value], value)) if votes else "3"
        values = [dict(type=kind, imdg_class=selected, min_bays=1, away_from=dict(kind="bay", bay=bay))
                  for bay in context["bays"] if bay % 2]
        details = dict(selection="most numerous load-list primary/subsidiary/additional class; bay anchor min_bays1; prefer baseline violated",
                       class_votes=dict(sorted(votes.items())), selected_class=selected,
                       no_dg_targets=not bool(votes))
    else:
        raise ValueError("unknown rule type")
    ranked = []
    for value in values:
        answer = validate_answer(json.dumps(dict(schema_version="stowlm.reader/2", constraint=value)), context)
        rank, violations = rank_candidate(instance, baseline_plan, answer)
        ranked.append((rank, answer, violations))
    if not ranked:
        raise ValueError("no physical candidate for " + kind)
    _, answer, violations = min(ranked, key=lambda row: row[0])
    details["baseline_violations"] = violations
    if kind == "max_tiers":
        details["physical_depth"] = context["max_tiers_by_bay"][str(answer.bay)][answer.zone]
        details["cap"] = answer.max_tiers
    return answer, details


def comparison(instance, base, row, answer, selection):
    from stowage.model import MovePlan
    from experiments.mvp.reader.pipeline import intrinsic_violations
    plan = MovePlan.model_validate(base["plan"]) if base.get("passed") and base.get("plan") else None
    violations = intrinsic_violations(instance, answer, plan) if plan is not None else None
    return dict(row_key=row["key"], baseline_key=base["key"], map=row["map"], cell=row["cell"],
                rule_type=answer.type, canonical_rule=row["canonical_rule"], selection=selection,
                binding_classification="unknown" if violations is None else ("binding" if violations else "already_obeyed"),
                baseline_instruction_violations=violations, baseline_passed=bool(base.get("passed")),
                rule_passed=bool(row.get("passed")), lifts=row.get("lifts"), baseline_lifts=base.get("lifts"))


def run_map(job, output):
    from stowage.io import load_instance
    from stowage.model import MovePlan
    output = Path(output)
    path, cell = job["path"], job["cell"]
    if file_identity(ROOT / path)["sha256"] != job["sha256"]:
        raise ValueError("frozen campaign input changed")
    key = hashlib.sha256(path.encode()).hexdigest()[:24]
    checkpoint = output / "jobs" / (key + ".json")
    status = dict(job=job, worker_pid=os.getpid(), started_at=time.time(), state="running", finalized_keys=[])
    durable_json(checkpoint, status)
    try:
        base = measure.run_one(cell, path, PROFILE, out=ROWS)
        status["finalized_keys"].append(base["key"])
        durable_json(checkpoint, status)
        refresh(output)
        instance = load_instance(ROOT / path)
        baseline_plan = MovePlan.model_validate(base["plan"]) if base.get("passed") and base.get("plan") else None
        for kind in KINDS:
            answer, selection = select_rule(instance, baseline_plan, kind)
            row = measure.run_one(cell, path, PROFILE, answer, base, ROWS)
            durable_json(output / "comparisons" / (row["key"] + ".json"), comparison(instance, base, row, answer, selection))
            status["finalized_keys"].append(row["key"])
            durable_json(checkpoint, status)
            refresh(output)
        status.update(state="finished", finished_at=time.time())
        durable_json(checkpoint, status)
        return status
    except BaseException as error:
        status.update(state="unknown", interrupted_at=time.time(), reason=type(error).__name__ + ": " + str(error))
        durable_json(checkpoint, status)
        raise


def refresh(output):
    output = Path(output)
    manifest = json.loads((output / "campaign.json").read_text())
    allowed = {row["path"]: row["cell"] for row in manifest["selected_medium"] + manifest["opportunistic"]}
    current, compat = measure.current_rows(ROWS)
    rows = [row for row in current if row["map"] in allowed and row["profile"] == PROFILE
            and row["cell"] == allowed[row["map"]]]
    by_key = {row["key"]: row for row in rows}
    comparisons = {row["row_key"]: row for path in (output / "comparisons").glob("*.json")
                   if (row := json.loads(path.read_text()))["row_key"] in by_key}
    lines = ["# Current live diagnostic campaign", "",
             "Eight public r0 development strata, baseline plus four instructions each (40 medium rows). "
             "Three workers; all medium map jobs are queued before opportunistic tiny/small. "
             "Live-v2 only:90s extra,30s requested finish; native floors and fleet admission can make wall time hours. "
             "This diagnostic is not the32-map development calibration or a sealed evaluation.", "",
             "Only strictly compatible finalized rows count. Historical attempts stay immutable. "
             "Binding means the passed baseline violated that exact instruction; no passed baseline means unknown.", "",
             "| Cell | Type | Finalized | Passes | Binding passes | Already-obeyed passes | Unknown passes | Median s | Max s | Paired lifts / baseline |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    groups = defaultdict(list)
    for row in rows:
        groups[row["cell"], row["rule_type"]].append(row)
    for cell in ("medium", "tiny", "small"):
        for kind in ("baseline",) + KINDS:
            group = groups[cell, kind]
            passed = [row for row in group if row.get("passed")]
            counts = Counter(comparisons.get(row["key"], {}).get("binding_classification", "unknown") for row in passed)
            times = [row["wall_s"] for row in group]
            pairs = [(row.get("lifts"), by_key[comparisons[row["key"]]["baseline_key"]].get("lifts"))
                     for row in passed if row["key"] in comparisons and comparisons[row["key"]]["baseline_passed"]
                     and comparisons[row["key"]]["baseline_key"] in by_key]
            pairs = [(left, right) for left, right in pairs if left is not None and right is not None]
            med = lambda values: f"{statistics.median(values):.1f}" if values else "—"
            lift_text = f"{med([p[0] for p in pairs])} / {med([p[1] for p in pairs])} (n={len(pairs)})"
            lines.append(f"| {cell} | {kind} | {len(group)} | {len(passed)} | {counts['binding'] if kind != 'baseline' else '—'} | {counts['already_obeyed'] if kind != 'baseline' else '—'} | {counts['unknown'] if kind != 'baseline' else '—'} | {med(times)} | {max(times):.1f} | {lift_text} |" if times else
                         f"| {cell} | {kind} |0|0|0|0|0|—|—|—|")
    medium = [row for row in rows if row["cell"] == "medium"]
    coverage = {job["path"]: sorted(row["rule_type"] for row in medium if row["map"] == job["path"])
                for job in manifest["selected_medium"]}
    complete = all(set(kinds) == set(("baseline",) + KINDS) for kinds in coverage.values())
    status = dict(observed_at=time.time(), supervisor_pid=manifest["supervisor_pid"],
                  current_finalized_rows=len(rows), medium_finalized_rows=len(medium), medium_target=40,
                  medium_complete=complete, medium_coverage=coverage,
                  historical_stale_rows=len(compat["stale_rows"]), source_identity=compat["implementation"],
                  engine_identity=compat["engine_identity"], eta="4–12hours plus admission; coarse, not a deadline",
                  jobs=[json.loads(path.read_text()) for path in sorted((output / "jobs").glob("*.json"))])
    durable_json(output / "status.json", status)
    for path in (output / "RESULTS.md", HERE / "RESULTS-LIVE.md", HERE / "RESULTS.md"):
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        temporary.write_text("\n".join(lines) + "\n")
        temporary.replace(path)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=3, choices=(1, 2, 3))
    parser.add_argument("--out", type=Path, default=HERE / "measurements/live-campaign")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    output = args.out.resolve()
    if not output.is_relative_to(HERE):
        parser.error("--out must stay inside binding")
    output.mkdir(parents=True, exist_ok=True)
    ROWS.mkdir(parents=True, exist_ok=True)
    from .measurement_identity import implementation_identity
    manifest = dict(cohort(), supervisor_pid=os.getpid(), started_at=time.time(), workers=args.workers,
                    profile=PROFILE, budget_s=90, finalize_s=30, seed=0,
                    source_identity=implementation_identity(HERE), rows=str(ROWS.relative_to(ROOT)),
                    eta="4–12hours plus admission; coarse, not a deadline")
    durable_json(output / "launches" / (uuid.uuid4().hex + ".json"), manifest)
    durable_json(output / "campaign.json", manifest)
    refresh(output)
    print(json.dumps(dict(event="campaign_started", pid=os.getpid(), workers=args.workers,
                          selected_medium=8, target_medium_rows=40, output=str(output))), flush=True)
    if args.prepare_only:
        return
    jobs = manifest["selected_medium"] + manifest["opportunistic"]
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")) as pool:
        pending = {pool.submit(run_map, job, str(output)): job for job in jobs}
        while pending:
            done, _ = wait(pending, timeout=15, return_when=FIRST_COMPLETED)
            for future in done:
                job = pending.pop(future)
                try:
                    future.result()
                except BaseException as error:
                    durable_json(output / "job-errors" / (uuid.uuid4().hex + ".json"),
                                 dict(job=job, status="unknown", reason=repr(error), at=time.time()))
            refresh(output)
    final = refresh(output)
    durable_json(output / "complete.json", final)


if __name__ == "__main__":
    main()
