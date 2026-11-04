"""Independent ext-1 receipt/provenance checks. Never opens map payloads or reports.

verify() is read-only. Its closed-world conclusions concern published logs and
metadata; absence of an unlogged access is not observable from these artifacts.
No run, checker, planner or report implementation is imported.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from collections import Counter

PREREG_SHA256 = "ba3dcc46f4b773b484d156288d8ee5c5fe682b2fc8f4d5096ee2e37de211e206"
MANIFEST_SHA256 = "acf2f275efa73f45c43709f4a52807dd480390e1497f085cab11d3604265bbec"
SEEDS = [306320000 + 100 * cell + rep for cell in range(8) for rep in range(3)]
RULES = ["close_bay", "max_tiers", "dg_keep_away", "one_port_per_hatch"]


def object_sha256(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path, parent):
    return path.resolve().is_relative_to(parent.resolve())


def _repo_root(path):
    return next((p.parent for p in path.parents if p.name == "experiments"), Path.cwd())


def _prior_matches(path, seeds, digests):
    """Conservative token collision search, bounded memory, handles chunk edges."""
    needles = {str(x).encode() for x in seeds} | {x.encode() for x in digests}
    if not needles:
        return []
    pattern = re.compile(rb"(?<![0-9A-Za-z])(?:" + b"|".join(sorted(needles)) + rb")(?![0-9A-Za-z])")
    found, tail = set(), b""
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            buf = tail + chunk
            found.update(match.group().decode() for match in pattern.finditer(buf))
            tail = buf[-130:]
    return sorted(found)


def audit_evidence(job_dir, row, run_dir, prepass_authorization=None, expected_rule=None):
    """Validate an alleged pass against independently spawned audit artifacts."""
    job_dir, run_dir = Path(job_dir), Path(run_dir)
    errors = []
    def require(ok, message):
        if not ok:
            errors.append(message)
    def read(name):
        try:
            return json.loads((job_dir / name).read_text())
        except (OSError, ValueError) as exc:
            errors.append(f"{name}: {type(exc).__name__}")
            return {}
    job, audit_job = read("job.json"), read("audit-job.json")
    plan_pid, audit_pid = read("pid.json"), read("audit.pid.json")
    process, audit_process = read("process.json"), read("audit.process.json")
    audit_row, dispatch = read("audit-row.json"), read("AUDIT-DISPATCHED.json")
    plan_dispatch = read("DISPATCHED.json")
    key = (row.get("map_id"), row.get("arm"))
    for label, item in [("job", job), ("audit job", audit_job), ("audit row", audit_row)]:
        require((item.get("map_id"), item.get("arm")) == key, f"{label} identity mismatch")
    # Dispatch hashes the allocated job before replacing its prepass capability
    # with the consumed planning capability in job.json.
    before_authorization = dict(job)
    if prepass_authorization is not None:
        before_authorization["authorization"] = prepass_authorization
    require(plan_dispatch.get("job_sha256") == object_sha256(before_authorization), "planning dispatch job hash mismatch")
    target = Path(audit_job.get("target", "/nonexistent"))
    require(_inside(target, job_dir) and target.name in {"row.json", "deadline-row.json"}, "audit target escapes own job row artifacts")
    target_hash = None
    if _inside(target, job_dir) and target.is_file():
        target_hash = file_sha256(target)
    require(bool(target_hash), "audit target missing")
    for label, value in [("audit job", audit_job.get("target_sha256")),
                         ("audit dispatch", dispatch.get("target_sha256")),
                         ("audit row", audit_row.get("audited_target_sha256")),
                         ("published row", row.get("audited_target_sha256"))]:
        require(value == target_hash and bool(target_hash), f"{label} target hash mismatch")
    planning_pid, fresh_pid = plan_pid.get("pid"), audit_pid.get("pid")
    require(isinstance(fresh_pid, int) and fresh_pid > 0 and fresh_pid != planning_pid,
            "audit process PID missing or reused planning PID")
    require(audit_pid.get("start_new_session") is True, "audit was not recorded as separate session")
    require(audit_process.get("audit_pid") == fresh_pid == audit_row.get("audit_pid") == row.get("audit_pid"),
            "audit PID receipts disagree")
    require(process.get("child_pid") == planning_pid == row.get("planning_pid"), "planning PID receipts disagree")
    require(audit_pid.get("started", -1) >= plan_pid.get("started", float("inf")), "audit predates planning")
    require(audit_pid.get("started", -1) >= dispatch.get("t", float("inf")), "audit predates dispatch")
    require(audit_process.get("audit_returncode") == 0, "audit returned nonzero/missing")
    for name in ["audit_timed_out", "audit_cleanup_failed", "audit_watchdog_fired"]:
        require(audit_process.get(name) is False, f"{name} not false")
    for name in ["audit_cleanup_errors", "audit_watchdog_errors"]:
        require(audit_process.get(name) == [], f"{name} not empty")
    for item in [row, audit_row]:
        require(item.get("audit_verified") is True and item.get("audit_fresh_process") is True,
                "row lacks affirmative fresh audit")
    for field in ["passed", "full_checker_pass", "complete", "all_loaded", "replay_legal", "level", "plan_sha256", "map_sha256", "counterfactual_instruction_recheck", "counterfactual_instruction_violations", "counterfactual_rule", "counterfactual_rule_sha256", "instruction_recheck", "instruction_violations", "final_cells", "expected_rule", "reading_sha256", "checker", "classification", "plan", "envelope"]:
        require(row.get(field) == audit_row.get(field), f"published/audit {field} differs")
    if key[1] == "pipeline" and expected_rule is not None:
        # Binding's denominator must recheck the allocated intended rule, even
        # if published and audit rows agree on a consistently substituted rule.
        expected_rule_hash = object_sha256(expected_rule)
        for label, item in [("published", row), ("audit", audit_row)]:
            require(item.get("counterfactual_rule") == expected_rule,
                    f"{label} counterfactual rule differs from committed allocation")
            require(item.get("counterfactual_rule_sha256") == expected_rule_hash,
                    f"{label} counterfactual rule hash differs from committed allocation")
    is_deadline = (row.get("status") == "deadline_exceeded"
                   or audit_row.get("status") == "deadline_exceeded"
                   or row.get("deadline_exceeded") is True
                   or audit_row.get("deadline_exceeded") is True)
    if is_deadline:
        require(target.name == "deadline-row.json" and audit_job.get("deadline_recovery") is True,
                "deadline candidate lacks dedicated recovery target")
    if target.name == "deadline-row.json" and target_hash:
        target_row = read("deadline-row.json")
        observations = read("observed-incumbents.json")
        publication = target_row.get("deadline_publication", {})
        observed = observations.get("observed", {}).get(row.get("plan_sha256"), {})
        require(target_row.get("deadline_with_incumbent") is True, "deadline target lacks incumbent")
        require(audit_row.get("deadline_incumbent_verified") is True, "audit lacks deadline incumbent verification")
        require(observed and observed == publication == audit_row.get("deadline_publication"), "deadline publication/observation mismatch")
        require(observations.get("child_pid") == planning_pid and observations.get("map_id") == key[0]
                and observations.get("arm") == key[1], "deadline observation identity mismatch")
        wall, budget = publication.get("published_wall_s"), observations.get("hard_wall_s")
        registered_budget = {"pipeline": 1200, "instruction": 1200, "twostep": 180}.get(key[1])
        require(budget == registered_budget == target_row.get("hard_wall_s"), "deadline differs from registered arm hard wall")
        require(isinstance(wall, (int, float)) and isinstance(budget, (int, float))
                and 0 <= wall <= budget == target_row.get("hard_wall_s"), "incumbent not observed within hard deadline")
        require(object_sha256(publication.get("plan")) == row.get("plan_sha256"), "deadline observed plan hash mismatch")
        # Raw publication event corroborates observed publication bytes and timing.
        events_path = job_dir / "pipeline" / "events.jsonl"
        matching = []
        if events_path.is_file():
            for line in events_path.read_text().splitlines():
                try:
                    event = json.loads(line)
                    if event.get("kind") == "incumbent" and event.get("data", {}).get("plan_sha256") == row.get("plan_sha256"):
                        matching.append(event)
                except ValueError:
                    errors.append("invalid deadline publication event")
        require(any(object_sha256(e) == publication.get("publication_sha256") and
                    isinstance(e.get("t_s"), (int, float)) and e["t_s"] <= budget and
                    e.get("data", {}).get("checked") is True for e in matching),
                "no hash-matching checked predeadline publication event")
    auth = audit_job.get("authorization", {})
    require(auth.get("phase") == "audit", "audit phase authorization missing")
    auth_id = auth.get("authorization_id", "")
    if re.fullmatch(r"[0-9a-f]{64}", auth_id):
        receipts = []
        for sub in ["authorizations", "consumed"]:
            try:
                receipts.append(json.loads((run_dir / sub / (auth_id + ".json")).read_text()))
            except (OSError, ValueError):
                errors.append(f"{sub} audit authorization missing/invalid")
        if len(receipts) == 2:
            require(receipts[0] == receipts[1], "audit authorization/consumption differ")
            require(all(receipts[0].get(k) == v for k, v in auth.items()), "audit authorization identity mismatch")
            payload = dict(audit_job)
            payload.pop("authorization", None)
            require(receipts[0].get("payload_sha256") == object_sha256(payload), "audit payload hash mismatch")
    else:
        errors.append("audit authorization id invalid")
    return {"map_id": key[0], "arm": key[1], "job_dir": str(job_dir),
            "ok": not errors, "errors": errors, "audit_pid": fresh_pid,
            "planning_pid": planning_pid, "target_sha256": target_hash}


def verify(run_dir, measurement_dir, rows):
    """Return JSON-compatible findings; rows are the caller's raw merged rows."""
    run_dir, measurement_dir = Path(run_dir).resolve(), Path(measurement_dir).resolve()
    root = _repo_root(measurement_dir)
    issues, checks, evidence, inputs = [], {}, {}, {}
    def check(name, condition, detail=None):
        checks[name] = bool(condition)
        if not condition:
            issues.append({"severity": "P1", "check": name, "detail": detail})
    def read(path):
        path = Path(path)
        # Hard read boundary: only declared metadata and run artifacts, no map payloads.
        if not (_inside(path, run_dir) or _inside(path, measurement_dir)):
            raise ValueError(f"Refusing out-of-scope input: {path}")
        if _inside(path, measurement_dir / "maps") or path.name in {"REPORT.md", "report.json"}:
            raise ValueError(f"Refusing payload/report: {path}")
        try:
            inputs[str(path)] = file_sha256(path)
            return json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            check(f"read:{path.name}", False, str(exc))
            return {}
    # Check the prescribed bytes before reading other run metadata.
    prereg = measurement_dir / "PREREG.md"
    observed = file_sha256(prereg) if prereg.is_file() else None
    inputs[str(prereg)] = observed
    check("preregistration_sha256", observed == PREREG_SHA256, observed)
    if observed != PREREG_SHA256:
        return {"ok": False, "checks": checks, "issues": issues, "inputs": inputs}
    config, started = read(run_dir / "config.json"), read(run_dir / "STARTED.json")
    config_hash = object_sha256(config)
    check("started_config_hash", started.get("config_sha256") == config_hash)
    check("registered_manifest", config.get("manifest_sha256") == started.get("manifest_sha256") == MANIFEST_SHA256)
    check("registered_recipe", config.get("expected_maps") == 24 and config.get("expected_rows") == 72
          and config.get("arms") == ["pipeline", "twostep", "instruction"]
          and config.get("profile") == "standard" and config.get("standing") == ""
          and config.get("twostep_s") == config.get("twostep_finalize_s") == 15
          and config.get("arm_deadlines_s") == {"pipeline": 1200, "twostep": 180, "instruction": 1200}
          and config.get("audit_timeout_s") == 300)
    manifest = read(measurement_dir / "maps-manifest.json")
    maps = manifest.get("maps", [])
    check("maps_manifest_hash", config.get("maps_manifest_sha256") == inputs.get(str(measurement_dir / "maps-manifest.json")))
    check("maps_config_identity", maps == config.get("maps"))
    seeds = read(measurement_dir / "map-seeds.json")
    proof = read(measurement_dir / "map-seed-proof.json")
    check("registered_seed_sequences", seeds.get("seeds") == proof.get("proposed_seeds") == [m.get("seed") for m in maps] == SEEDS)
    check("seed_proof_bound", manifest.get("seed_proof_sha256") == inputs.get(str(measurement_dir / "map-seed-proof.json")))
    check("seed_proof_coverage", proof.get("no_seed_collision") is True and proof.get("collisions") == []
          and proof.get("errors") == [] and proof.get("required_metadata_missing") == []
          and proof.get("structural_seed_coverage_complete") is True and proof.get("ready_for_generation") is True)
    recipe_errors = []
    for idx, item in enumerate(maps):
        cell, rep = divmod(idx, 3)
        expected = {"seed": SEEDS[idx], "parent_root_seed": SEEDS[idx], "cell_index": cell, "rep": rep,
                    "ship": ["feeder_500", "feeder_800"][cell // 4],
                    "fill_target": [60, 70][(cell // 2) % 2], "generator": ["random_yard", "simulated_yard"][cell % 2],
                    "map_id": f"ext-1:{SEEDS[idx]}", "prereg_sha256": PREREG_SHA256}
        recipe_errors.extend(f"{idx}:{k}" for k, v in expected.items() if item.get(k) != v)
    check("map_cell_recipe", len(maps) == 24 and not recipe_errors, recipe_errors)
    digest_proof = read(measurement_dir / "map-digest-proof.json")
    prior = measurement_dir / "map-prior-identities.json"
    if prior.is_file():
        inputs[str(prior)] = file_sha256(prior)
        check("prior_identity_inventory_hash", inputs[str(prior)] == proof.get("prior_identities_sha256") == digest_proof.get("prior_identity_inventory_sha256"))
        digests = [m.get(k) for m in maps for k in ["sha256", "canonical_instance_sha256"] if m.get(k)]
        hits = _prior_matches(prior, SEEDS, digests)
        check("prior_inventory_no_collision", not hits, hits)
        evidence["prior_identity_bytes"] = prior.stat().st_size
    else:
        check("prior_identity_inventory_hash", False, "Missing prior identity inventory")
    for interval in proof.get("recorded_seed_ranges", []):
        lo, hi = interval.get("start"), interval.get("end_inclusive")
        check("range_no_collision:" + str(interval.get("path")), isinstance(lo, int) and isinstance(hi, int) and not any(lo <= s <= hi for s in SEEDS))
    check("digest_proof_clear", digest_proof.get("ready") is True and digest_proof.get("collisions") == []
          and digest_proof.get("old_map_payloads_opened") == digest_proof.get("seeds_redrawn") == digest_proof.get("planners_run") == 0)
    for key in ["sha256", "canonical_instance_sha256"]:
        values = [item.get(key) for item in maps]
        check("unique:" + key, len(set(values)) == len(values) and None not in values)
    # Only paths/hashes in published open log; no map bytes are opened here.
    by_id = {m.get("map_id"): m for m in maps}
    open_path = run_dir / "opened-maps.jsonl"
    opened, open_errors = [], []
    if open_path.is_file():
        inputs[str(open_path)] = file_sha256(open_path)
        for index, line in enumerate(open_path.read_text().splitlines()):
            try:
                event = json.loads(line)
                item = by_id.get(event.get("map_id"), {})
                actual_path = Path(event.get("path", "/nonexistent"))
                expected_path = root / item.get("path", "nonexistent")
                if event.get("event") == "map_opened":
                    if not (item and actual_path.resolve() == expected_path.resolve() and
                            _inside(actual_path, measurement_dir / "maps") and event.get("sha256") == item.get("sha256")):
                        open_errors.append(index + 1)
                    opened.append(event)
                elif event.get("event") != "instruction_selected" or not item:
                    open_errors.append(index + 1)
            except (ValueError, TypeError):
                open_errors.append(index + 1)
    else:
        open_errors.append("missing log")
    check("only_registered_extension_map_opens", not open_errors, open_errors)
    evidence["opened_events"] = len(opened)
    evidence["opened_distinct_maps"] = len({x.get("map_id") for x in opened})
    evidence["seed_proof_coverage"] = {k: proof.get(k) for k in ["paths_checked", "metadata_files_checked", "prior_seed_count", "prior_digest_count", "metadata_cutoff_utc", "forbidden_raw_roots", "coverage_limits", "skipped"]}
    # Freeze attestations are verified for internal hashes; no planner imports.
    freeze = [read(measurement_dir / name) for name in ["verify-before.json", "verify-after.json"]]
    check("freeze_receipts", all(f.get("ok") is True and f.get("manifest_sha256") == MANIFEST_SHA256 and f.get("tree_digest") == config.get("tree_digest") for f in freeze) and freeze[0].get("full_weights") is True)
    identity = read(measurement_dir / "planning-code-identity.json")
    check("planning_code_identity_attestation", identity.get("all_declared_recipe_objects_identical") is True and identity.get("all_protected_hashes_identical") is True)
    # Prepass commit must bind all 24 terminal readings before dispatch.
    commit = read(run_dir / "PREPASS-COMMIT.json")
    allocation = read(run_dir / "instruction-allocation.json")
    draw = read(run_dir / "prepass" / "instruction-draw.json")
    prepass_row = read(run_dir / "prepass" / "row.json")
    prepass_job = read(run_dir / "prepass" / "job.json")
    prepass_process = read(run_dir / "prepass" / "process.json")
    for key, path in [("allocation_sha256", run_dir / "instruction-allocation.json"), ("draw_sha256", run_dir / "prepass/instruction-draw.json"), ("prepass_row_sha256", run_dir / "prepass/row.json"), ("job_sha256", run_dir / "prepass/job.json"), ("process_receipt_sha256", run_dir / "prepass/process.json")]:
        check("prepass_commit:" + key, commit.get(key) == inputs.get(str(path)))
    check("prepass_config", commit.get("config_sha256") == config_hash)
    reading_errors = []
    allocation_by_id = {a.get("map_id"): a for a in allocation} if isinstance(allocation, list) else {}
    draw_by_id = {a.get("map_id"): a for a in draw} if isinstance(draw, list) else {}
    reader_identity = config.get("parser_options", {}).get("reader_identity", {})
    for map_id, item in allocation_by_id.items():
        reading = item.get("recorded_reading", {})
        if not (item.get("reading_sha256") == object_sha256(reading)
                and reading.get("map_id") == map_id and reading.get("map_sha256") == by_id.get(map_id, {}).get("sha256")
                and item.get("reading_allocation_sha256") == reading.get("allocation_sha256")
                and item.get("expected_rule") == reading.get("expected_rule")
                and reading.get("status") in ["accepted", "reader_error", "rejected", "unavailable"]
                and draw_by_id.get(map_id, {}).get("recorded_reading") == reading):
            reading_errors.append(map_id + ":reading")
        parser = reading.get("parser", {})
        if any(parser.get(k) != reader_identity.get(k) for k in ["adapter_sha256", "decoder_sha256", "domain_words_sha256"]):
            reading_errors.append(map_id + ":parser")
        rank = item.get("cohort_index")
        if not isinstance(rank, int) or item.get("instruction_type") != RULES[rank % 4]:
            reading_errors.append(map_id + ":rule_rank")
    check("all_readings_bound", set(allocation_by_id) == set(by_id) == set(draw_by_id) and not reading_errors, reading_errors)
    check("unique_allocation_ranks", sorted(a.get("cohort_index", -1) for a in allocation_by_id.values()) == list(range(24)))
    evidence["allocation_rank_limit"] = "Rank/type and exact unique ranks are checked against committed raw draw; canonical public-facts ordering is not recomputed without opening map payloads."
    check("six_of_each_instruction", Counter(a.get("instruction_type") for a in allocation_by_id.values()) == Counter({x: 6 for x in RULES}))
    # Index original jobs without inferring ids from implementation-specific naming.
    jobs = {}
    for directory in sorted((run_dir / "jobs").glob("*")):
        path = directory / "job.json"
        if path.is_file():
            job = read(path)
            key = (job.get("map_id"), job.get("arm"))
            jobs.setdefault(key, []).append((directory, job))
    audit_checks, job_errors = [], []
    confirmation_errors = []
    for row in rows:
        key = (row.get("map_id"), row.get("arm"))
        candidates = jobs.get(key, [])
        # Broad alleged-pass selection cannot be bypassed by clearing one stored flag.
        from metrics import passed as independently_passed
        if independently_passed(row) or row.get("passed") is True or row.get("full_checker_pass") is True:
            if len(candidates) != 1:
                audit_checks.append({"map_id": key[0], "arm": key[1], "ok": False, "errors": ["No unique planning job"]})
            else:
                audit_checks.append(audit_evidence(candidates[0][0], row, run_dir,
                    allocation_by_id.get(key[0], {}).get("authorization"),
                    expected_rule=allocation_by_id.get(key[0], {}).get("expected_rule")))
        for directory, job in candidates:
            if job.get("instruction_allocation_sha256") != commit.get("allocation_sha256"):
                job_errors.append(str(directory) + ":allocation")
            allocated = allocation_by_id.get(key[0], {})
            if any(job.get(k) != allocated.get(k) for k in ["reading_sha256", "recorded_reading", "expected_rule"]):
                job_errors.append(str(directory) + ":reading")
            if key[1] == "instruction":
                events_path = directory / "pipeline" / "events.jsonl"
                events = []
                if events_path.is_file():
                    inputs[str(events_path)] = file_sha256(events_path)
                    try:
                        events = [json.loads(line) for line in events_path.read_text().splitlines()]
                    except ValueError:
                        confirmation_errors.append(str(directory) + ":invalid events")
                engine_starts = [e for e in events if e.get("stage") == "engine" and e.get("kind") == "stage_start"]
                alleged_pass = independently_passed(row) or row.get("passed") is True or row.get("full_checker_pass") is True
                if alleged_pass or engine_starts:
                    reading = job.get("recorded_reading", {})
                    expected = job.get("expected_rule")
                    if not (reading.get("status") == "accepted" and reading.get("expected_match") is True
                            and reading.get("parsed") == reading.get("expected_rule") == expected
                            and expected == allocated.get("expected_rule")
                            and job.get("confirmation_source") == "sealed_expected_match"):
                        confirmation_errors.append(str(directory) + ":exact expected-rule confirmation")
                    if alleged_pass and not (row.get("expected_rule") == expected
                            and row.get("reading_sha256") == job.get("reading_sha256")
                            and row.get("confirmation_source") == "sealed_expected_match"):
                        confirmation_errors.append(str(directory) + ":published confirmation differs")
                    confirms = [e for e in events if e.get("stage") == "confirmation" and e.get("kind") == "stage_end"]
                    valid_confirms = [e for e in confirms if e.get("data", {}).get("confirmed") == e.get("data", {}).get("required") == 1
                        and e.get("data", {}).get("confirmation_source") == "sealed_expected_match"
                        and e.get("data", {}).get("confirmation", {}).get("source") == "sealed_expected_match"]
                    if not engine_starts or not valid_confirms or not all(any(c.get("seq", float("inf")) < e.get("seq", -1)
                            and c.get("t_s", float("inf")) <= e.get("t_s", -1) for c in valid_confirms) for e in engine_starts):
                        confirmation_errors.append(str(directory) + ":confirmation does not precede engine")
            if row.get("manifest_sha256") not in [None, MANIFEST_SHA256] or row.get("tree_digest") not in [None, config.get("tree_digest")]:
                job_errors.append(str(directory) + ":freeze")
    check("all_candidate_passes_freshly_audited", all(a["ok"] for a in audit_checks), [a for a in audit_checks if not a["ok"]])
    check("worker_reading_replay_bound", not job_errors, job_errors)
    check("exact_confirmation_precedes_instruction_execution", not confirmation_errors, confirmation_errors)
    anchor_errors, unanchored = [], []
    for path, observed_hash in inputs.items():
        candidate = Path(path)
        if _inside(candidate, measurement_dir):
            name = str(candidate.relative_to(root))
            expected_hash = config.get("artifacts", {}).get(name)
            if expected_hash is None:
                unanchored.append(name)
            elif expected_hash != observed_hash:
                anchor_errors.append({"path": name, "expected": expected_hash, "observed": observed_hash})
    check("registered_metadata_artifact_hashes", not anchor_errors, anchor_errors)
    evidence["unanchored_metadata_receipts"] = unanchored
    evidence["candidate_audits"] = audit_checks
    evidence["fresh_candidate_audits"] = sum(a["ok"] for a in audit_checks)
    evidence["closed_world_limit"] = "Published open logs show only registered ext-1 maps. This cannot detect unlogged file access, unrecorded prior seeds/digests or forbidden sealed payload contents; no map payload was opened by this verifier. Freeze and seed census coverage are independently hash-bound attestations, not a new execution of their original census/full-weight verifier."
    return {"ok": not issues, "checks": checks, "issues": issues, "evidence": evidence, "inputs": inputs}
