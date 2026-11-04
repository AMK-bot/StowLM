"""Rule-scoped compatibility for the approved, already-running campaign.

Historical rows are never rewritten. This is an explicit migration of approved
planning dependencies, not acceptance based on a matching file path alone.
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
from pathlib import Path
import types

from .. import measure, measurement_identity as identity

HERE = Path(__file__).resolve().parents[1]
APPROVAL = HERE / "measurements/logic3-before.json"
APPROVAL_SHA256 = "7a88b2275e2c2f07f84faf5910bc94305118265cccdba6900dd0e13bf9fe6dcb"
EXPORT_DELTA_SHA256 = "2e7e36643ad2284a40a58efff11be0124fb47c86cb92bb7c55580ebb737e9c40"
APPROVED_INIT_HASHES = {"3430cdaa72c119550fa7704c2d90d17d0eccc378c31b0cd029a45a49d7622574",
                        "2e69540942e0b027be4b20062332e5c49ea08cac5cd6fc900de7d2b994e9fc37"}
POLICY_VERSION = "logic3-hatch-portfolio-v1"
CORE_FILES = ("__init__.py", "adapter.py", "hooks.py", "runtime.py", "admission.py",
              "confirmation.py", "provenance.py", "provenance-pins.json")
FUNCTIONS = {
    "measure.profile": "57aeccb3fb4240ab8809337c66d5eb1b81bce2373b8acbff16e1685bd9ce4812",
    "live_campaign.select_rule": "26f42e183b964689efd731503931ef9e7838f32b136fd7696f64df26ea85f547",
    "live_campaign.dg_votes": "ca9c292b4393577de6519af2f857f23f1fe9f8d71911d630e6b0d32c55203011",
    "live_campaign.rank_candidate": "7314d2a9761e62bf5fd7969ea50e9cc04a74e0e750a0103435ff7c788e339625",
}
CALLABLES = {
    "adapter": "ca085d25c5d848df5894a8a40b8147346196e1ff25eb48b084c948d6239d8356",
    "hooks": "f5ffaa98dae791fe258ea83ae08cd2c53433bf89563386c9d0d8b8a72662637b",
    "runtime": "cad2e276bad932e7e4f1260fa1bf8f23da125265d68cba64fff9b9ccf99036c9",
    "admission": "84ffa5e047ace1d4b4e845cce985bfdf48b52bb9e2baaec86e6571e835995b0e",
    "confirmation": "1bec118138c0128039798611de206fb5c84bac0a75e520c043a3ecb9d0396e23",
    "provenance": "d1593438983ba4903c7ffb010b6ed590cb762a0868d0eebabe1a99c5825179de",
}


def clone(function):
    copied = types.FunctionType(function.__code__, function.__globals__, function.__name__,
                                function.__defaults__, function.__closure__)
    copied.__kwdefaults__ = function.__kwdefaults__
    return copied


ORIGINAL_COMPATIBILITY = clone(identity.compatibility)
ORIGINAL_IMPLEMENTATION = clone(identity.implementation_identity)
ORIGINAL_BUILD = clone(identity.build_identity)


def approval_document():
    raw = APPROVAL.read_bytes()
    if hashlib.sha256(raw).hexdigest() != APPROVAL_SHA256:
        raise ValueError("reviewed logic3 approval snapshot changed")
    return json.loads(raw)


def approval():
    value = approval_document()
    files = dict(value["source_files"])
    first = next(iter(value["identities"].values()))
    files["provenance-pins.json"] = first["payload"]["implementation"]["source_files"]["provenance-pins.json"]
    return files


def callable_digest(module):
    found = {}
    for label, value in sorted(vars(module).items()):
        items = [(label, value)]
        if inspect.isclass(value) and getattr(value, "__module__", None) == module.__name__:
            items += [(label + "." + name, item.__func__ if isinstance(item, (staticmethod, classmethod)) else item)
                      for name, item in vars(value).items()]
        for name, item in items:
            if getattr(item, "__module__", None) != module.__name__:
                continue
            layers, seen = [], set()
            while hasattr(item, "__code__") and id(item) not in seen:
                seen.add(id(item))
                layers.append(identity.digest(identity._code_value(item.__code__)))
                item = getattr(item, "__wrapped__", None)
            if layers:
                found[name] = layers
    return identity.digest(found)


def core_scope():
    from .. import live_campaign
    files = {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest() for name in CORE_FILES}
    observed_init = files["__init__.py"]
    export_valid = (observed_init in APPROVED_INIT_HASHES and hashlib.sha256(
        (HERE / "review3/init-export-delta.json").read_bytes()).hexdigest() == EXPORT_DELTA_SHA256)
    functions = {}
    for qualified in FUNCTIONS:
        module_name, name = qualified.split(".")
        module = measure if module_name == "measure" else live_campaign
        functions[qualified] = identity.digest(identity._code_value(getattr(module, name).__code__))
    expected = approval()
    if export_valid:
        files["__init__.py"] = expected["__init__.py"]  # reviewed export-only delta
    callables = {name: callable_digest(importlib.import_module("experiments.mvp.pipeline.binding." + name)) for name in CALLABLES}
    valid = export_valid and all(files[name] == expected[name] for name in CORE_FILES) and functions == FUNCTIONS and callables == CALLABLES
    return dict(version=3, core_source_files=files, planning_functions=functions,
                planning_callables=callables, export_delta_sha256=EXPORT_DELTA_SHA256, approved_core=valid)


def hatch_policy():
    paths = [HERE / "allocation.py", HERE / "review3/api.py", HERE / "review3/hatch.py", HERE / "review3/init-export-delta.json"]
    files = {str(path.relative_to(HERE)): hashlib.sha256(path.read_bytes()).hexdigest()
             if path.exists() else None for path in paths}
    return dict(version=POLICY_VERSION, source_files=files, sha256=identity.digest(files))


def is_hatch(payload):
    return (payload.get("canonical_rule") or {}).get("constraint", {}).get("type") == "one_port_per_hatch"


def implementation_identity(binding_root):
    diagnostic = ORIGINAL_IMPLEMENTATION(binding_root)
    if Path(binding_root).resolve() == HERE:
        diagnostic["planning_scope"] = core_scope()
    return diagnostic


def build_identity(**kwargs):
    if "source_files" in kwargs.get("implementation", {}):
        implementation = dict(kwargs["implementation"])
        scope = core_scope()
        if (kwargs.get("rule") or {}).get("constraint", {}).get("type") == "one_port_per_hatch":
            scope["hatch_policy"] = hatch_policy()
        implementation["planning_scope"] = scope
        kwargs["implementation"] = implementation
    return ORIGINAL_BUILD(**kwargs)


def row_scope(implementation):
    if isinstance(implementation.get("planning_scope"), dict):
        return implementation["planning_scope"]
    files = implementation.get("source_files", {})
    expected = approval()
    names = CORE_FILES + ("measure.py", "live_campaign.py")
    normal = dict(files)
    if normal.get("__init__.py") in APPROVED_INIT_HASHES:
        normal["__init__.py"] = expected["__init__.py"]
    approved_loaded = {value["payload"]["implementation"]["loaded_code_sha256"]
                       for value in approval_document()["identities"].values()}
    if implementation.get("loaded_code_sha256") not in approved_loaded:
        return None
    if implementation.get("source_sha256") != identity.digest(files):
        return None
    if not all(normal.get(name) == expected.get(name) for name in names):
        return None
    return dict(version=3, core_source_files={name: normal[name] for name in CORE_FILES},
                planning_functions=dict(FUNCTIONS), planning_callables=dict(CALLABLES),
                export_delta_sha256=EXPORT_DELTA_SHA256, approved_core=True)


def compatibility(row, expected):
    found = row.get("measurement_identity")
    payload = found.get("payload") if isinstance(found, dict) else None
    if not isinstance(payload, dict):
        return ORIGINAL_COMPATIBILITY(row, expected)
    implementation = payload.get("implementation", {})
    if "source_files" not in implementation and "planning_scope" not in implementation:
        return ORIGINAL_COMPATIBILITY(row, expected)  # strict synthetic identities
    if found.get("sha256") != identity.digest(payload):
        return False, ["stored measurement identity is malformed"]
    if expected.get("sha256") != identity.digest(expected["payload"]):
        raise ValueError("invalid expected measurement identity")
    target = expected["payload"]
    changed = [key + " changed" for key in sorted(set(payload) | set(target))
               if key != "implementation" and payload.get(key) != target.get(key)]
    if changed:
        return False, changed
    current = core_scope()
    old = row_scope(implementation)
    if not current["approved_core"] or not old or not old.get("approved_core"):
        return False, ["planning core is not the approved unchanged implementation"]
    if any(old.get(key) != current[key] for key in ("core_source_files", "planning_functions", "planning_callables", "export_delta_sha256")):
        return False, ["scoped planning implementation changed"]
    if not target.get("engine", {}).get("passed"):
        return False, ["current pinned engine/native verification failed"]
    if is_hatch(payload):
        if old.get("hatch_policy") != hatch_policy():
            return False, ["old hatch allocation lacks current portfolio policy"]
        if row.get("evidence", {}).get("portfolio_policy_version") != POLICY_VERSION:
            return False, ["hatch attempt did not execute the current portfolio policy"]
    return True, []


def current_rows(out, *, persist=True):
    from ..adapter import verify_manifest
    out = Path(out)
    engine, implementation = verify_manifest(), implementation_identity(HERE)
    eligible, stale = [], []
    files = sorted(out.glob("*.json"))
    for path in files:
        row = json.loads(path.read_text())
        expected = measure.expected_identity(row["cell"], row["map"], row["profile"],
            row.get("canonical_rule"), engine=engine, implementation=implementation)
        valid, reasons = compatibility(row, expected)
        if valid:
            eligible.append(row)
        else:
            stale.append(dict(key=row["key"], rule_type=row["rule_type"], reasons=reasons,
                passed=bool(row.get("passed")), row_file=path.name,
                row_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    latest = {}
    for row in sorted(eligible, key=lambda value: (value.get("finished_at", 0), value["key"])):
        logical = measure._key(row["cell"], row["map"], row["profile"], row.get("canonical_rule"))
        if logical in latest:
            old = latest[logical]
            stale.append(dict(key=old["key"], rule_type=old["rule_type"], reasons=["superseded compatible attempt"]))
        latest[logical] = row
    report = dict(observed_at=__import__("time").time(), historical_rows=len(files), current_rows=len(latest),
                  stale_rows=stale, stale_keys=sorted(row["key"] for row in stale), engine_identity=engine,
                  implementation=implementation, migration="approved unchanged nonhatch planning core; hatch portfolio policy required")
    if persist:
        measure._write_json(out.parent / "current-compatibility.json", report)
        measure._write_json(out.parent / "logic3-scoped-compatibility.json", report)
    return list(latest.values()), report


def ladder_annotation(row):
    stages = row.get("evidence", {}).get("stages", {})
    levels = stages.get("peel_seam", {}).get("trials", [])
    trials = []
    absent = []
    searched = []
    for level in levels:
        if level.get("level", 0) == 0:
            continue
        search = level.get("search", {})
        found = list(search.get("proposal", {}).get("trials", []))
        found += list(search.get("partial_search", {}).get("proposal", {}).get("trials", []))
        trials.extend(found)
        if not found:
            absent.append(level["level"])
        else:
            searched.append(level["level"])
    return dict(ladder_work="trials_recorded" if trials else "none", ladder_trial_count=len(trials),
                ladder_levels_not_searched=absent,
                ladder_levels_searched=searched,
                ladder_levels_classified=[level["level"] for level in levels if "level" in level],
                ladder_note="No proposal trials recorded; relaxed candidate may only classify earlier L0 work" if absent and not trials else None)


def annotate(row, baseline=None):
    from .api import refusal_kind, planner_message
    evidence = row.get("evidence", {})
    first = row.get("first_checked_wall_s")
    if first is None:
        first = evidence.get("first_checked_wall_s")
    result = dict(row_key=row["key"], first_checked_wall_s=first,
                  refusal_kind=refusal_kind(evidence), planner_message=planner_message(evidence),
                  baseline_also_failed=bool(baseline is not None and not baseline.get("passed")),
                  not_a_test=False, excluded_from_refusal_rates=False, **ladder_annotation(row))
    if result["baseline_also_failed"] and not row.get("passed"):
        result["report_status"] = "baseline_also_failed"
    else:
        result["report_status"] = row.get("evidence", {}).get("status", "unknown")
    if row.get("cell") == "tiny" and row.get("rule_type") == "one_port_per_hatch":
        raw = json.loads((measure.ROOT / row["map"]).read_text())
        active = set(raw["load_list"])
        pods = {box["pod"] for box in raw["containers"] if box["id"] in active}
        if len(raw["vessel"].get("hatch_covers", [])) <= 1 and len(pods) > 1:
            result.update(not_a_test=True, excluded_from_refusal_rates=True,
                          report_status="not a test: section has one hatch")
    return result
