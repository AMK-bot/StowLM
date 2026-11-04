"""Validate and freeze the switch-OFF MVP corpus without rewriting instances.

Test and showcase must be complete before release. Train/val may be incomplete;
the manifest records every requested row, including zero emissions. A generator
gate is evidence about an instance, never checker approval of a loading plan.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "experiments/mvp/corpus"
VESSELS = ("feeder_500", "feeder_800", "feeder_1700")
RUNGS = ("tiny", "small", "medium")
FAMILIES = ("random_yard", "simulated_yard")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text())


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _build_module():
    spec = importlib.util.spec_from_file_location("mvp_corpus_build_for_freeze", Path(__file__).with_name("build.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_instance(path):
    from stowage.io import load_instance
    return load_instance(path)


def _requires_gate(instance):
    from stowage.generate.background_acceptance import requires_background_gate
    return requires_background_gate(instance.vessel, size=instance.size_class)


def _canonical_full_cell_ids(vessel_name):
    """Read full geometry directly; no hull construction or nominal-size guess."""
    from stowage.vessel.fleet import load_ship_params
    return {cell["id"] for cell in load_ship_params(vessel_name)["cells"]}


def _presentation_counts(instance):
    targets = set(instance.load_list)
    cargo = [box for box in instance.containers if box.id in targets]
    buried = {box_id for stack in instance.yard.stacks
              for index, box_id in enumerate(stack.boxes)
              if box_id in targets and any(above not in targets for above in stack.boxes[index + 1:])}
    return {"dg_targets": sum(box.dg is not None for box in cargo),
            "reefer_targets": sum(box.reefer for box in cargo), "buried_targets": len(buried)}


def _require_clean(instance):
    from stowage.frozen import require_clean
    require_clean(instance, what="frozen MVP corpus")


def _identity(config):
    return config["vessel"], config.get("rung", config.get("size")), config["generator"]


def load_release_registry(root):
    """Verify byte-frozen v1 exceptions before accepting a mixed-epoch release."""
    root = Path(root)
    path = root / "releases.json"
    if not path.exists():
        return None, {}
    release = _read(path)
    _require(release.get("format") == "stowlm-mvp-releases-v1", "unknown release registry format")
    _require(release.get("current_release") in {"v2", "v2.1"}, "unknown corpus release")
    _require(release.get("status") in {"pending_regeneration", "partial_qualified", "complete"}, "invalid release status")
    for ship, settings in release.get("ship_settings", {}).items():
        _require(ship in VESSELS and all(isinstance(settings.get(key), (int, float))
                 and 0 <= settings[key] < 1 for key in ("booking_slack", "booking_mass_fraction")),
                 "invalid per-ship booking settings")
    if release["current_release"] == "v2.1":
        _require(release["status"] == "complete"
                 and set(release.get("ship_settings", {})) == set(VESSELS)
                 and not release.get("pending"),
                 "v2.1 must qualify all three ships with no pending evaluation slots")
    pins = release.get("preserved_v1", [])
    _require(len(pins) == 16 and Counter((p["split"], p["rung"]) for p in pins)
             == Counter({("test", "tiny"): 15, ("showcase", "full"): 1}),
             "release must pin 15 tiny tests and one full hero")
    by_path = {}
    for pin in pins:
        _require(pin["path"] not in by_path, "duplicate preserved path")
        by_path[pin["path"]] = pin
        for name, digest in ((pin["path"], pin["sha256"]), (pin["audit_path"], pin["audit_sha256"])):
            candidate = root / name
            _require(candidate.resolve().is_relative_to(root.resolve()) and candidate.is_file()
                     and sha256(candidate) == digest, f"preserved v1 checksum mismatch: {name}")
    return release, by_path


def _release_record(record, config, release, pins):
    """Keep pending v1 rows visible without advertising them as regenerated v2."""
    record["booking_slack"] = config.get("booking_slack")
    record["booking_mass_fraction"] = config.get("booking_mass_fraction")
    record["active_for_current_release"] = True
    if release is None:
        record["release_epoch"] = "v1"
        return
    path, split = record["path"], record["split"]
    if path in pins:
        _require(record["seed"] == pins[path]["seed"], f"preserved seed mismatch: {path}")
        record["release_epoch"] = "v1_preserved"
        record["source_binding_exemption"] = "immutable v1 instance and audit hashes"
    elif release.get("ship_settings") and record["rung"] in {"small", "medium"}:
        setting = release["ship_settings"].get(config.get("vessel", record.get("ship")))
        matches = bool(setting) and record["rung"] == "small" and all(
            config.get(key) == value for key, value in setting.items()
            if key in {"booking_slack", "booking_mass_fraction"})
        if split in {"test", "showcase"}:
            base = release["replacements"][split]["seed_base"]
            matches = matches and base <= record["seed"] < base + 1_000_000
            _require(matches, f"v2 per-ship booking setting or seed mismatch: {path}")
        record["active_for_current_release"] = matches and release["status"] in {"partial_qualified", "complete"}
        epoch = release["current_release"]
        record["release_epoch"] = (f"{epoch}_training" if split in {"train", "val"} else epoch) if matches else "v1_pending_replacement"
        record["generation_settings"] = dict(setting or {})
    elif split in {"test", "showcase"} and record["rung"] in {"small", "medium"}:
        replacement = release["replacements"][split]
        base = replacement["seed_base"]
        is_v2 = base <= record["seed"] < base + 1_000_000
        record["release_epoch"] = "v2" if is_v2 else "v1_pending_replacement"
        record["active_for_current_release"] = is_v2
        if is_v2 or release["status"] == "complete":
            setting = release.get("booking_slack", {})
            _require(is_v2, f"v2 release still contains old small/medium row: {path}")
            _require(setting.get("value") is not None
                     and config.get(setting.get("setting_name", "booking_slack")) == setting["value"],
                     f"v2 booking slack setting mismatch: {path}")
    elif split in {"train", "val"} and record["rung"] in {"small", "medium"}:
        adopted = release.get("booking_slack", {}).get("value")
        active = (release["status"] == "complete" and adopted is not None
                  and config.get("booking_slack") == adopted)
        record["release_epoch"] = "v2_training" if active else "v1_pending_replacement"
        record["active_for_current_release"] = active
    else:
        record["release_epoch"] = "tiny_training"


def _validate_config(config, *, split):
    ship, rung, family = _identity(config)
    _require(ship in VESSELS, f"unknown vessel: {ship}")
    _require(rung in (*RUNGS, "full") if split == "showcase" else rung in RUNGS, f"invalid rung: {rung}")
    _require(family in FAMILIES, f"unknown generator family: {family}")
    _require(type(config.get("seed")) is int and config["seed"] >= 0, "invalid root seed")
    if split != "showcase":
        _require(config.get("split") == split, f"request split mismatch: {config}")
    return ship, rung, family


def validate_artifact(path, *, current_sources, strict_sources=False):
    """Load one committed shard and verify its producer and D58 evidence."""
    path = Path(path)
    audit_path = path.with_suffix(".audit.json")
    audit = _read(audit_path)
    _require(audit.get("instance_sha256") == sha256(path), f"instance checksum mismatch: {path}")
    _require(audit.get("accepted") is True, f"producer did not accept: {path}")
    _require(audit.get("rule_freeze_v1_enabled") is False, f"switch-OFF evidence missing: {path}")
    instance = _load_instance(path)
    _require_clean(instance)
    config = audit["config"]
    ship, rung, family = _identity(config)
    _require(instance.seed == config["seed"] and instance.size_class == rung, f"instance seed/rung mismatch: {path}")
    _require(instance.family.get("vessel") == ship, f"instance vessel mismatch: {path}")
    _require(instance.family.get("port") == config["port"], f"instance port mismatch: {path}")
    _require(instance.voyage is not None, f"missing voyage declaration: {path}")
    _require(audit.get("requested_targets") == len(instance.load_list), f"target count mismatch: {path}")
    _require(audit.get("instance_versions") == dict(instance.provenance.versions), f"version audit mismatch: {path}")
    if "schema_version" in audit:
        _require(audit["schema_version"] == instance.schema_version, f"schema audit mismatch: {path}")
    gate = _requires_gate(instance)
    if gate:
        witness = audit.get("background_last", {})
        _require(audit.get("background_checks", 0) > 0 and witness.get("accepted") is True
                 and bool(witness.get("physics_sha256")), f"missing accepted D58 witness: {path}")
        if "physics" in audit:
            physics = audit["physics"]
            _require(witness["physics_sha256"] == physics.get("input_sha256", physics.get("sha256")),
                     f"D58 physics binding mismatch: {path}")
    bound_sources = audit.get("source_files", audit.get("physics", {}).get("sources"))
    if not isinstance(bound_sources, dict) or not bound_sources:
        source_status, changed = "missing", []
    else:
        changed = sorted(key for key in set(bound_sources) | set(current_sources)
                         if bound_sources.get(key) != current_sources.get(key))
        source_status = "drift" if changed else "current"
    if strict_sources:
        _require(source_status == "current", f"source binding {source_status}: {path}; {changed[:5]}")
    elapsed = audit.get("elapsed_generation_s")
    background = audit.get("background_seconds", 0.0)
    _require(isinstance(elapsed, (int, float)) and elapsed >= 0, f"invalid generation timing: {path}")
    _require(isinstance(background, (int, float)) and background >= 0, f"invalid background timing: {path}")
    record = dict(seed=instance.seed, ship=ship, rung=rung, family=family,
        c2_screened=audit.get("c2_screened"), global_screen=audit.get("global_screen"),
        port=config["port"], schema_version=instance.schema_version,
        versions=dict(instance.provenance.versions), generator_components=audit.get("generation_components", {}),
        requested_targets=audit["requested_targets"], emitted_targets=len(instance.load_list),
        generator_gate_passed=True, plan_checker_passed=None,
        generation_seconds=elapsed, background_gate_seconds=background,
        background_gate_checks=audit.get("background_checks", 0),
        background_gate_cache_hits=audit.get("background_cache_hits", 0),
        background_gate_required=gate,
        background_physics_sha256=audit.get("background_last", {}).get("physics_sha256"),
        source_binding_status=source_status, changed_source_files=changed)
    if "showcase_features" in audit:
        record.update(showcase_features=audit["showcase_features"], hero_view_only=audit.get("hero_view_only", False),
                      selection_total_seconds=audit.get("selection_total_seconds", elapsed))
    return audit, instance, record


def _reserve_seed(reservations, seed, owner):
    _require(seed not in reservations, f"root seed {seed} reused by {reservations.get(seed)} and {owner}")
    reservations[seed] = owner


def _temporary_path(path, directory):
    return any(part.startswith(".") for part in path.relative_to(directory).parts) or path.name.endswith(".partial.json")


def _audit_paths(directory):
    return sorted(p for p in directory.rglob("*.audit.json") if not _temporary_path(p, directory))


def _instance_paths(directory):
    return sorted(p for p in directory.rglob("*.json")
                  if not _temporary_path(p, directory)
                  and not any(p.name.endswith(suffix) for suffix in
                             (".audit.json", ".failure.json", ".selection.json"))
                  and p.name not in {"showcase-manifest.json", "manifest.json"})


def _test_inventory(specs, release):
    qualified = set(release.get("ship_settings", {})) if release else set()
    coverage = ({(v, "tiny"): 5 for v in VESSELS} |
                {(v, "small"): 5 for v in qualified}) if qualified else {
                    (v, r): 5 for v in VESSELS for r in RUNGS}
    _require(Counter((s["vessel"], s["rung"]) for s in specs) == Counter(coverage),
             "test inventory must contain five instances per qualified ship/rung")
    _require(all(2 <= sum(s["vessel"] == v and s["rung"] == r and s["generator"] == f
                          for s in specs) <= 3
                 for v, r in coverage for f in FAMILIES),
             "test inventory must balance generator families within each ship/rung")
    if qualified:
        families = Counter(s["generator"] for s in specs if s["rung"] == "small")
        _require(abs(families[FAMILIES[0]] - families[FAMILIES[1]]) <= 1,
                 "qualified small test families must balance overall")


def _pending_rows(release):
    """Deferred D84 slots remain requests, without inventing instances or seeds."""
    if not release:
        return []
    pending = release.get("pending", {})
    result = []
    if isinstance(pending, list):
        for row in pending:
            split, ship, rung, family, count = (row.get(k) for k in ("split", "ship", "rung", "family", "count"))
            _require(split in {"test", "showcase"} and ship in VESSELS and rung in RUNGS
                     and family in FAMILIES and type(count) is int and count > 0, "invalid pending release slot")
            result.append((split, ship, rung, family, count))
        return result
    for split in ("test", "showcase"):
        for row in pending.get(split, []) if isinstance(pending, dict) else []:
            ship = row.get("vessel", row.get("ship"))
            count = row.get("requested", row.get("count"))
            _require(ship in VESSELS and row.get("rung") in RUNGS and type(count) is int and count > 0,
                     "invalid pending release slot")
            families = row.get("family_counts", {"random_yard": (count + 1) // 2,
                                                   "simulated_yard": count // 2})
            _require(set(families) <= set(FAMILIES) and sum(families.values()) == count
                     and all(type(n) is int and n >= 0 for n in families.values()),
                     "invalid pending family counts")
            for family, n in families.items():
                if n:
                    result.append((split, ship, row["rung"], family, n))
    return result


def assemble_manifest(root=CORPUS, *, partial=False, strict_sources=False, current_sources=None,
                      booking_slack=None):
    """Return a validated release payload; uncommitted train shards stay pending."""
    root = Path(root)
    build = _build_module()
    release, preserved = load_release_registry(root)
    qualified = set(release.get("ship_settings", {})) if release else set()
    if booking_slack is not None:
        _require(release is not None and release.get("booking_slack", {}).get("value") == booking_slack,
                 "requested booking slack differs from release registry")
    current_sources = build.source_snapshot() if current_sources is None else current_sources
    requested = Counter()
    emitted = Counter()
    failed = Counter()
    reservations = {}
    files, metadata_paths, uncommitted = [], set(), []
    if release is not None:
        metadata_paths.add(root / "releases.json")
    if (root / "token_allocation.json").exists():
        metadata_paths.add(root / "token_allocation.json")
    requested_totals, missing_requests = {}, []
    for split in ("test", "train", "val"):
        request_path = root / f"{split}.requested.json"
        if not request_path.exists():
            _require(partial and split != "test", f"missing requested inventory: {request_path}")
            _require(not _audit_paths(root / split) and not _instance_paths(root / split),
                     f"existing {split} shards lack a requested inventory")
            missing_requests.append(split)
            requested_totals[split] = None
            continue
        metadata_paths.add(request_path)
        inventory = _read(request_path)
        _require(inventory.get("split_policy") == build.POLICY, f"wrong MVP split policy: {request_path}")
        specs = inventory["specs"]
        _require(Counter(s["rung"] for s in specs) == Counter({r: n for r, n in inventory["counts"].items() if n}),
                 f"requested count mismatch: {request_path}")
        requested_totals[split] = len(specs)
        expected_paths = {}
        for config in specs:
            ship, rung, family = _validate_config(config, split=split)
            _reserve_seed(reservations, config["seed"], f"{split}/{ship}/{rung}/{family}")
            path = build.shard_path(config, root)
            _require(path not in expected_paths, f"duplicate requested path: {path}")
            expected_paths[path] = config
            requested[(split, ship, rung, family)] += 1
        if split == "test":
            _test_inventory(specs, release)
        for path in _instance_paths(root / split):
            _require(path in expected_paths, f"unrequested instance: {path}")
        for audit_path in _audit_paths(root / split):
            path = audit_path.with_name(audit_path.name.removesuffix(".audit.json") + ".json")
            _require(path in expected_paths and path.exists(), f"orphan or unrequested audit: {audit_path}")
        for path, config in expected_paths.items():
            audit_path = path.with_suffix(".audit.json")
            failure_path = path.with_suffix(".failure.json")
            if failure_path.exists():
                metadata_paths.add(failure_path)
                _require(_read(failure_path).get("config") == config, f"failure config mismatch: {failure_path}")
            if not audit_path.exists():
                if path.exists():
                    uncommitted.append(str(path.relative_to(root)))
                if failure_path.exists():
                    failed[(split, *_identity(config))] += 1
                _require(split != "test", f"test shard is not committed: {path}")
                continue
            relative = str(path.relative_to(root))
            audit, _, record = validate_artifact(path, current_sources=current_sources,
                strict_sources=strict_sources and relative not in preserved)
            _require(audit["config"] == config, f"audit/request mismatch: {path}")
            record.update(split=split, path=str(path.relative_to(root)), sha256=sha256(path),
                          audit_path=str(audit_path.relative_to(root)), audit_sha256=sha256(audit_path))
            _release_record(record, config, release, preserved)
            files.append(record)
            emitted[(split, *_identity(config))] += 1

    showcase_root = root / "showcase"
    showcase_manifest = showcase_root / "showcase-manifest.json"
    _require(showcase_manifest.exists(), "showcase manifest is required before release")
    metadata_paths.add(showcase_manifest)
    showcase = _read(showcase_manifest)
    _require(showcase.get("rule_freeze_v1_enabled") is False, "showcase manifest lacks switch-OFF evidence")
    showcase_records = showcase.get("records", [])
    expected_showcases = len(qualified) + 1 if qualified else None
    _require((len(showcase_records) == expected_showcases if qualified else 4 <= len(showcase_records) <= 6)
             and showcase.get("instances") == len(showcase_records), "showcase instance count mismatch")
    paths_by_hash = defaultdict(list)
    for path in _instance_paths(showcase_root):
        paths_by_hash[sha256(path)].append(path)
    _require(sum(map(len, paths_by_hash.values())) == len(showcase_records), "showcase file/manifest count mismatch")
    showcase_coverage = set()
    hero = False
    showcase_audit_paths = set()
    for row in showcase_records:
        config = row["config"]
        ship, rung, family = _validate_config(config, split="showcase")
        _require(config["port"] == "laem_chabang_like", "showcase port must be laem_chabang_like")
        candidates = paths_by_hash.get(row["instance_sha256"], [])
        _require(len(candidates) == 1, "showcase instance hash is absent or ambiguous")
        path = candidates.pop()
        audit_path = path.with_suffix(".audit.json")
        showcase_audit_paths.add(audit_path)
        relative = str(path.relative_to(root))
        audit, instance, record = validate_artifact(path, current_sources=current_sources,
            strict_sources=strict_sources and relative not in preserved)
        _require(audit["config"] == config, f"showcase config mismatch: {path}")
        _require(row.get("instance_versions") == record["versions"], f"showcase version mismatch: {path}")
        # Selection candidates also reserve seeds; reusing an unselected draw in
        # fitting would leak information about this presentation-conditioned set.
        selected_seeds = {config["seed"]}
        selected_seeds.update(s["seed"] for s in audit.get("seed_selection", []))
        for seed in selected_seeds:
            _reserve_seed(reservations, seed, f"showcase/{path.name}")
        if rung in ("small", "medium"):
            features = audit.get("showcase_features", {})
            measured = _presentation_counts(instance)
            _require(all(value > 0 and features.get(key) == value for key, value in measured.items()),
                     f"showcase presentation features missing: {path}")
            showcase_coverage.add(ship)
        elif rung == "full":
            actual_ids = [cell.id for cell in instance.vessel.cells]
            # The feeder_1700 family has 1,604 enumerated cells. Reconditioning
            # sets nominal_teu to that measured geometry count; the family label
            # is not a requirement to invent another 96 cells or loaded TEU.
            _require(ship == "feeder_1700" and audit.get("hero_view_only") is True
                     and len(actual_ids) == len(set(actual_ids))
                     and set(actual_ids) == _canonical_full_cell_ids(ship)
                     and not any(load.kind == "cargo" and load.mass_kg > 0 for load in instance.vessel.fixed_loads),
                     "hero must use full feeder_1700 geometry without fixed background cargo")
            hero = True
        requested[("showcase", ship, rung, family)] += 1
        emitted[("showcase", ship, rung, family)] += 1
        record.update(split="showcase", path=str(path.relative_to(root)), sha256=sha256(path),
                      audit_path=str(audit_path.relative_to(root)), audit_sha256=sha256(audit_path))
        _release_record(record, config, release, preserved)
        files.append(record)
    _require(showcase_coverage == (qualified or set(VESSELS)) and hero,
             "showcase needs qualified port calls and a full 1700 hero")
    _require(set(_audit_paths(showcase_root)) == showcase_audit_paths, "orphan showcase audit")
    for name, digest in showcase.get("hashes", {}).items():
        path = showcase_root / name
        _require(path.parent == showcase_root and path.exists() and sha256(path) == digest,
                 f"showcase metadata checksum mismatch: {name}")
        metadata_paths.add(path)
    for path in showcase_root.glob("*.selection.json"):
        metadata_paths.add(path)

    pending_slots = _pending_rows(release)
    deferred = Counter()
    for split, ship, rung, family, count in pending_slots:
        requested[(split, ship, rung, family)] += count
        deferred[(split, ship, rung, family)] += count
    rows = []
    for split in ("test", "showcase", "train", "val"):
        for ship in VESSELS:
            for rung in (*RUNGS, "full") if split == "showcase" else RUNGS:
                for family in FAMILIES:
                    key = split, ship, rung, family
                    records = [r for r in files if (r["split"], r["ship"], r["rung"], r["family"]) == key]
                    times = [r["generation_seconds"] for r in records]
                    rows.append(dict(split=split, ship=ship, rung=rung, family=family,
                        requested=requested[key], emitted=emitted[key], failed=failed[key],
                        active_emitted=sum(r["active_for_current_release"] for r in records),
                        deferred_release_slots=deferred[key],
                        pending=requested[key] - emitted[key] - failed[key],
                        generation_seconds=sum(times), mean_generation_seconds=sum(times) / len(times) if times else None,
                        background_gate_seconds=sum(r["background_gate_seconds"] for r in records)))
    hashes = {str(p.relative_to(root)): sha256(p) for p in sorted(metadata_paths)}
    for record in files:
        hashes[record["path"]] = record["sha256"]
        hashes[record["audit_path"]] = record["audit_sha256"]
    counts = dict(Counter(r["split"] for r in files))
    active_counts = dict(Counter(r["split"] for r in files if r["active_for_current_release"]))
    for split in ("test", "showcase", "train", "val"):
        counts.setdefault(split, 0)
        active_counts.setdefault(split, 0)
    release_ready = release is None or release["status"] == "complete"
    complete = (release_ready and not missing_requests
                and all(row["requested"] == row["emitted"] for row in rows)
                and all(r["active_for_current_release"] for r in files))
    tools = Path(__file__).resolve().parent
    # Informational provenance: fitting workers and their tools can evolve after
    # an early release; these hashes never relax committed instance checks.
    tool_files = sorted({*tools.glob("*.py"), *tools.glob("README*")})
    return dict(format="stowlm-mvp-corpus-v2" if release else "stowlm-mvp-corpus-v1",
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        release=release, release_ready=release_ready,
        release_epoch_counts=dict(Counter(r["release_epoch"] for r in files)),
        split_policy=build.POLICY, rule_freeze_v1_enabled=False,
        partial_release=partial, complete=complete, counts=counts, active_counts=active_counts,
        tool_files_sha256={str(path.relative_to(ROOT)): sha256(path) for path in tool_files if path.is_file()},
        requested_counts={**requested_totals, "showcase": len(showcase_records)},
        pending_release_slots=release.get("pending", {}) if release else {},
        requested_counts_including_deferred={split: sum(n for (s, _, _, _), n in requested.items() if s == split)
                                             for split in ("test", "showcase", "train", "val")},
        missing_requested_inventories=missing_requests, uncommitted_instance_files=uncommitted,
        requested_vs_emitted=rows, files=sorted(files, key=lambda r: r["path"]), hashes=dict(sorted(hashes.items())),
        source_binding_counts=dict(Counter(r["source_binding_status"] for r in files)),
        schema_versions=sorted({r["schema_version"] for r in files}),
        generator_versions={key: sorted({r["versions"][key] for r in files if key in r["versions"]})
                            for key in sorted({k for r in files for k in r["versions"]})},
        seeds={split: sorted(r["seed"] for r in files if r["split"] == split) for split in counts},
        root_seed_reservations=len(reservations),
        caveats=["Producer acceptance and D58 background witnesses do not certify a loading plan; the checker gates every plan shown.",
                 "MVP split policy covers familiar ships and generator families with disjoint root seeds.",
                 "Showcases are presentation-conditioned and excluded from evaluation and fitting."])


def write_freeze(root=CORPUS, *, partial=False, strict_sources=False, booking_slack=None):
    root = Path(root)
    manifest = assemble_manifest(root, partial=partial, strict_sources=strict_sources, booking_slack=booking_slack)
    build = _build_module()
    for split in ("test", "showcase"):
        entries = [(path, digest) for path, digest in manifest["hashes"].items() if path.startswith(split + "/")]
        list_path = root / split / "SHA256SUMS"
        temporary = list_path.with_suffix(".partial")
        temporary.write_text("".join(f"{digest}  {path.removeprefix(split + '/')}\n" for path, digest in entries))
        temporary.replace(list_path)
        manifest["hashes"][str(list_path.relative_to(root))] = sha256(list_path)
    build.atomic_json(root / "manifest.json", manifest)
    return manifest


def verify_freeze(root=CORPUS, *, strict_sources=False, booking_slack=None):
    root = Path(root)
    manifest = _read(root / "manifest.json")
    for name, digest in manifest["hashes"].items():
        path = root / name
        _require(path.resolve().is_relative_to(root.resolve()) and path.is_file() and sha256(path) == digest,
                 f"frozen checksum mismatch: {name}")
    fresh = assemble_manifest(root, partial=manifest["partial_release"], strict_sources=strict_sources,
                              booking_slack=booking_slack)
    frozen_files = {r["path"]: r for r in manifest["files"]}
    current_files = {r["path"]: r for r in fresh["files"]}
    # Early releases lock evaluation/presentation membership while fitting shards
    # can finish asynchronously. Already recorded train/val bytes remain frozen.
    locked_splits = {"test", "showcase"} if not manifest["complete"] else {"test", "showcase", "train", "val"}
    _require({p for p, r in current_files.items() if r["split"] in locked_splits}
             == {p for p, r in frozen_files.items() if r["split"] in locked_splits},
             "frozen corpus membership changed")
    _require(all(path in current_files and current_files[path]["sha256"] == row["sha256"]
                 for path, row in frozen_files.items()), "frozen corpus membership changed")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=CORPUS)
    parser.add_argument("--partial", action="store_true", help="release test/showcase before train inventories exist")
    parser.add_argument("--verify", action="store_true", help="verify the existing release without writing")
    parser.add_argument("--strict-sources", action="store_true", help="reject drift except immutable pinned v1 tiny/hero")
    parser.add_argument("--booking-slack", type=float, help="assert adopted setting matches release registry")
    args = parser.parse_args()
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    _require(rule_freeze_v1_enabled() is False, "MVP freeze requires rule-freeze switch OFF")
    manifest = (verify_freeze(args.root, strict_sources=args.strict_sources, booking_slack=args.booking_slack)
                if args.verify else write_freeze(args.root, partial=args.partial,
                    strict_sources=args.strict_sources, booking_slack=args.booking_slack))
    print(json.dumps({"counts": manifest["counts"], "active_counts": manifest.get("active_counts", manifest["counts"]),
                      "release_ready": manifest.get("release_ready", True), "complete": manifest["complete"],
                      "source_binding_counts": manifest["source_binding_counts"],
                      "manifest": str(args.root / "manifest.json")}, sort_keys=True))


if __name__ == "__main__":
    main()
