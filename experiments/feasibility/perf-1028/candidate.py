"""Load the quarantined 1028 candidate without replacing production modules.

The adjacent ``candidate/`` files are immutable byte-for-byte source snapshots.
``get_classes(True)`` returns their isolated engine and global-screen classes;
``get_classes(False)`` returns the original production classes, after verifying
that production has been restored to the recorded baseline. Start a fresh Python
process after restoring source files: hashes validate files, not stale objects
that an existing interpreter may have imported before restoration.

The manifest pins the candidate, baseline snapshots and shared feasibility
helpers. External physics/model inputs still require the experiment harness's
normal source pins and production qualification; this loader bypasses no guard.
Importing this file alone does not import or instantiate either engine.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MANIFEST = HERE / "candidate" / "manifest.json"
_GLOBAL_NAME = "stowage.feasibility._perf_1028_candidate_global_bounds"
_ENGINE_NAME = "stowage.feasibility._perf_1028_candidate_engine"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sources(optimized=True):
    """Fail closed if an expected candidate/baseline/shared source was changed.

    True does not require restored production _engine/global_bounds, allowing
    the snapshot to be inspected before quarantine. False additionally requires
    that those two production files match the original baseline bytes.
    """
    manifest = json.loads(MANIFEST.read_text())
    groups = ["candidate", "baseline_snapshots", "shared"]
    if not optimized:
        groups.append("production_baseline")
    mismatches = []
    for group in groups:
        for relative, expected in manifest[group].items():
            path = ROOT / relative
            actual = _sha(path) if path.is_file() else None
            if actual != expected:
                mismatches.append(f"{relative}: expected {expected}, got {actual}")
    if mismatches:
        raise RuntimeError("1028 candidate source mismatch:\n" + "\n".join(mismatches))
    return manifest


def _load_file(name, path):
    previous = sys.modules.get(name)
    if previous is not None:
        if Path(previous.__file__).resolve() != path.resolve():
            raise RuntimeError(f"Unexpected pre-existing module {name}: {previous.__file__}")
        return previous
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load candidate module {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    # Dataclass decoration resolves classes through their registered module.
    # Register only the new alias; never replace a production module entry.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if sys.modules.get(name) is module:
            del sys.modules[name]
        raise
    return module


def _candidate_modules():
    importlib.import_module("stowage.feasibility")
    global_module = _load_file(_GLOBAL_NAME, HERE / "candidate" / "global_bounds.py")
    engine_module = _load_file(_ENGINE_NAME, HERE / "candidate" / "_engine.py")
    # The byte-identical engine snapshot initially imports .global_bounds from
    # the production package. Bind its own namespace to the isolated screen
    # module, including both singleton/status types used by identity checks.
    for name in ("GLOBAL_RULE_IDS", "GlobalScreen", "UnknownReference", "UNKNOWN_INTERVAL"):
        setattr(engine_module, name, getattr(global_module, name))
    return engine_module, global_module


def get_classes(optimized=True):
    """Return a namespace of matching classes, sentinels and modules.

    Attributes: Engine/IncrementalEngine, GlobalScreen, Reference,
    UnknownReference, UNKNOWN_INTERVAL, LPIndeterminate, PersistentLP,
    engine_module, global_module, lp_module, certificates_module.
    Instrument the returned classes rather than assuming that candidate and
    production GlobalScreen are the same class. The native LP helper is shared
    and hash-pinned because it was not changed by the optimization.
    """
    verify_sources(optimized)
    if optimized:
        engine_module, global_module = _candidate_modules()
    else:
        engine_module = importlib.import_module("stowage.feasibility._engine")
        global_module = importlib.import_module("stowage.feasibility.global_bounds")
    lp_module = importlib.import_module("stowage.feasibility._lp_highs")
    certificates_module = importlib.import_module("stowage.feasibility._certificates")
    return SimpleNamespace(
        Engine=engine_module.IncrementalEngine,
        IncrementalEngine=engine_module.IncrementalEngine,
        GlobalScreen=global_module.GlobalScreen,
        Reference=global_module.Reference,
        UnknownReference=global_module.UnknownReference,
        UNKNOWN_INTERVAL=global_module.UNKNOWN_INTERVAL,
        LPIndeterminate=global_module.LPIndeterminate,
        PersistentLP=lp_module.PersistentLP,
        engine_module=engine_module,
        global_module=global_module,
        lp_module=lp_module,
        certificates_module=certificates_module,
    )


def get_candidate_classes():
    return get_classes(True)


def get_reference_classes():
    return get_classes(False)


def make_engine(instance, config=None, optimized=True, *, space=None):
    """Build a reference or isolated candidate engine without global patches.

    ``config`` is FeasibilityConfig (or None); a dictionary is accepted as its
    ``extra`` settings. A supplied config is copied, never modified in place.
    ``space`` defaults to boxslot, matching the performance/equivalence corpus.
    """
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig

    if config is None:
        config = FeasibilityConfig()
    elif isinstance(config, dict):
        config = FeasibilityConfig(extra=dict(config))
    config = replace(config, extra={**config.extra, "c2_optimized": int(bool(optimized))})
    classes = get_classes(optimized)
    return classes.Engine(instance, space or get_action_space("boxslot"), config)


def __getattr__(name):
    aliases = {
        "CandidateEngine": "Engine",
        "CandidateGlobalScreen": "GlobalScreen",
        "CandidateReference": "Reference",
        "UnknownReference": "UnknownReference",
        "UNKNOWN_INTERVAL": "UNKNOWN_INTERVAL",
        "LPIndeterminate": "LPIndeterminate",
    }
    if name in aliases:
        return getattr(get_classes(True), aliases[name])
    raise AttributeError(name)


__all__ = [
    "CandidateEngine", "CandidateGlobalScreen", "CandidateReference",
    "UnknownReference", "UNKNOWN_INTERVAL", "LPIndeterminate",
    "get_classes", "get_candidate_classes", "get_reference_classes",
    "make_engine", "verify_sources",
]
