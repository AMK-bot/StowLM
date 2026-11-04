"""Disk memo of fresh full-checker reports, keyed by exact inputs and the checker's source epoch.

Exact memoisation, not an approximation: a hit returns the byte-identical ``CheckReport`` that
``stowage.checker.full.check`` produced earlier for the same instance content, the same plan content and the same
checker source epoch. Everything downstream of the check (binding, validation, shipping gate) runs as before.

Key = sha256(instance content digest | plan content digest | checker source epoch). The epoch is a sha256 over:

* every ``*.py`` / ``*.toml`` / ``*.json`` file in the ``src/stowage`` package (the checker imports moves, vessel,
  feasibility, rules, model...; the whole package is the simple safe choice; ``__pycache__`` excluded);
* every file under ``params/`` (fleet / ports / cargo TOML the vessel and port models read);
* the pinned hull offset sources the vessel builders read (``data/raw/kcs``, ``data/raw/dtc``);
* the Python version string, the installed numpy / scipy / pydantic versions, and the environment switches the
  checker reads (``STOWAGE_*``, ``OPENBLAS_NUM_THREADS``, ``OMP_NUM_THREADS``, ``VECLIB_MAXIMUM_THREADS``; numpy here
  links Accelerate). ``python -m app`` and ``precompute_timeline.py`` default the three thread limits to 1 before
  numpy loads, so both sides reach the same epoch.

Not hashed: ``experiments/cache/{stability,vessels}`` (3.4 GB). Those are content-addressed / certificate-checked
derivations of the hashed sources (the checker re-validates their source sha256 certificates on load), so the epoch
already covers their inputs; hashing them would cost minutes per start.

The epoch is computed once per process (cached by each file's (path, mtime_ns, size)) and stands for the code this
process loaded. ``epoch_drift()`` re-takes the stat fingerprint and the environment; a different stat is re-hashed
(unchanged bytes are a harmless touch, only changed files are read). If the bytes or the environment differ from the
loaded epoch, this process can certify nothing: a stored PASS may be obsolete, and a fresh ``full.check`` here would
run the old loaded code against the new files. ``cached_check`` therefore raises ``CheckerEpochDrift`` before a hit
or a miss and again after a check, nothing is written, and ``app.checker`` turns that into an unavailable payload
(NOT VALIDATED). In-memory replay and baseline memos apply the same test (``app.flow``, ``app.stage_service``).
Restoring the original bytes restores the epoch; otherwise the app must be restarted. Reports that carry time-budget / timeout / internal-error / part-error diagnostics are never cached
(a clean recompute might decide differently). Files are written atomically (temp + rename), carry their key
components, and are verified on load (components, round-trip, ``report_matches_inputs``); any mismatch or parse
error is a miss and the check is recomputed.

Kill switch: ``STOWLM_NO_CHECK_CACHE=1`` or ``python -m app --no-check-cache``.
Location: ``experiments/mvp/ui/cache/checks/<key>.json`` (app cache only; never hand-edit).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

from stowage.checker import full
from stowage.model import CheckReport, Instance, MovePlan

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "experiments/mvp/ui/cache/checks"
FORMAT = "app-check-cache-v1"
KILL_ENV = "STOWLM_NO_CHECK_CACHE"
_SOURCE_SUFFIXES = {".py", ".toml", ".json"}
_ENV_KEYS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
DRIFT_REASON = "checker sources changed since this process started: restart the app"

_lock = threading.Lock()
_epoch: tuple[str, tuple] | None = None          # (epoch hex, stat fingerprint)
_epoch_environment: list[str] | None = None      # environment hashed into _epoch
_loaded_mismatch = False                         # sources or environment moved between import and the first epoch
_file_digests: dict[tuple, str] = {}             # (path, mtime_ns, size) -> sha256
stats = {"hits": 0, "misses": 0, "writes": 0, "skipped": 0, "epoch_s": None}
_last = threading.local()                        # how this thread's last cached_check got its report


def last_source() -> str | None:
    """"fresh" if this thread's last ``cached_check`` ran ``full.check``, "memo" if it read the disk memo, else None."""
    return getattr(_last, "source", None)


def enabled() -> bool:
    return os.environ.get(KILL_ENV, "") not in {"1", "true", "yes"}


def _walk(directory: Path) -> list[str]:
    return [os.path.join(folder, name) for folder, _, names in os.walk(directory) for name in names
            if "__pycache__" not in folder.split(os.sep)]


def _source_files() -> list[str]:
    """Absolute paths (plain strings: this runs on every hit, see ``epoch_drift``)."""
    files = [p for p in _walk(ROOT / "src/stowage") if os.path.splitext(p)[1] in _SOURCE_SUFFIXES]
    files += _walk(ROOT / "params")
    for sub in ("data/raw/kcs", "data/raw/dtc"):
        files += _walk(ROOT / sub)
    return sorted(files)


def _fingerprint(files: list[str]) -> tuple:
    start = len(str(ROOT)) + 1
    out = []
    for p in files:
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append((p[start:], st.st_mtime_ns, st.st_size))
    return tuple(out)


_versions: list[str] = []


def _environment() -> list[str]:
    """Interpreter and library versions (fixed by what this process loaded), then the live switches."""
    if not _versions:
        from importlib import metadata
        _versions.append("python " + sys.version)
        for dist in ("numpy", "scipy", "pydantic"):
            try:
                _versions.append(f"{dist} {metadata.version(dist)}")
            except metadata.PackageNotFoundError:
                _versions.append(f"{dist} absent")
    return _versions + sorted(f"{k}={v}" for k, v in os.environ.items() if k.startswith("STOWAGE_") or k in _ENV_KEYS)


# What this process imported: taken at import, since the first epoch can be computed a while later.
_loaded = (_fingerprint(_source_files()), _environment())


class CheckerEpochDrift(RuntimeError):
    """The sources or environment on disk are not the ones this process loaded; no verdict can be certified."""


def _compute_epoch() -> tuple[str, tuple]:
    files = _source_files()
    fingerprint = _fingerprint(files)
    h = hashlib.sha256()
    for rel, mtime, size in fingerprint:
        key = (rel, mtime, size)
        digest = _file_digests.get(key)
        if digest is None:
            digest = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
            _file_digests[key] = digest
        h.update(f"{rel}\0{digest}\n".encode())
    for part in _environment():
        h.update(f"env\0{part}\n".encode())
    return h.hexdigest(), fingerprint


def checker_epoch() -> str:
    """The checker source epoch for this process (computed once; see the module docstring)."""
    global _epoch, _epoch_environment, _loaded_mismatch
    with _lock:
        if _epoch is None:
            import time
            started = time.perf_counter()
            _epoch = _compute_epoch()
            _epoch_environment = _environment()
            _loaded_mismatch = (_epoch[1], _epoch_environment) != _loaded
            stats["epoch_s"] = round(time.perf_counter() - started, 3)
        return _epoch[0]


def epoch_drift() -> str | None:
    """``DRIFT_REASON`` if the disk sources or environment differ from the loaded epoch, else None.

    Stat-only on the usual path (measured in RECORDING.md); a changed stat re-hashes changed files only.
    """
    global _epoch
    checker_epoch()
    with _lock:
        if _epoch is None or _loaded_mismatch:
            return DRIFT_REASON
        epoch, fingerprint = _epoch
        if _environment() != _epoch_environment:
            return DRIFT_REASON
        if _fingerprint(_source_files()) == fingerprint:
            return None
        try:
            current, fingerprint = _compute_epoch()
        except OSError:
            return DRIFT_REASON
        if current != epoch:
            return DRIFT_REASON
        _epoch = (epoch, fingerprint)  # touched, same bytes
        return None


def require_current() -> None:
    reason = epoch_drift()
    if reason:
        raise CheckerEpochDrift(reason)


def content_digest(value) -> str:
    """Same canonical JSON as the checker's own input bindings."""
    return hashlib.sha256(json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def cache_key(instance_sha: str, plan_sha: str, epoch: str) -> str:
    return hashlib.sha256(f"{instance_sha}|{plan_sha}|{epoch}".encode()).hexdigest()


_UNCACHEABLE_WORDS = ("budget", "timeout", "timed out", "deadline", "internal_error", "internal error")


def cacheable(report: CheckReport) -> bool:
    """Only deterministic verdicts: no part errors, no time-budget/timeout/internal-error diagnostics."""
    for part in report.parts.values():
        if part.error:
            return False
        for key, value in part.metrics.items():
            k = key.lower()
            if ("budget_exceeded" in k or "timeout" in k or "timed_out" in k) and value:
                return False
    for v in [*report.violations, *(v for p in report.parts.values() for v in p.violations)]:
        text = f"{v.rule_id} {v.message}".lower()
        if any(word in text for word in _UNCACHEABLE_WORDS):
            return False
    return True


def _load(path: Path, *, key: str, instance_sha: str, plan_sha: str, epoch: str,
          instance: Instance, plan: MovePlan) -> CheckReport | None:
    try:
        data = json.loads(path.read_text())
        if (data.get("format") != FORMAT or data.get("key") != key or data.get("instance_sha256") != instance_sha
                or data.get("plan_sha256") != plan_sha or data.get("checker_epoch") != epoch):
            return None
        report = CheckReport.model_validate(data["report"])
        if report.model_dump(mode="json") != data["report"]:
            return None
        if not full.report_matches_inputs(report, instance, plan):
            return None
        return report
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _store(path: Path, payload: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(payload, stream, separators=(",", ":"), allow_nan=False)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return True
    except (OSError, ValueError, TypeError):
        return False


def cached_check(instance: Instance, plan: MovePlan, *, cache_dir: Path | None = None) -> CheckReport:
    """``full.check(instance, plan)``, memoised on disk (exact; see the module docstring).

    Raises ``CheckerEpochDrift`` instead of answering when this process no longer matches the sources on disk, with
    or without the disk memo: neither a stored report nor a run of the stale loaded code can be certified.
    """
    _last.source = None
    require_current()
    if not enabled():
        report = full.check(instance, plan)
        require_current()
        _last.source = "fresh"
        return report
    directory = Path(cache_dir) if cache_dir is not None else CACHE_DIR
    epoch = checker_epoch()
    instance_sha, plan_sha = content_digest(instance), content_digest(plan)
    key = cache_key(instance_sha, plan_sha, epoch)
    path = directory / f"{key}.json"
    if path.is_file():
        report = _load(path, key=key, instance_sha=instance_sha, plan_sha=plan_sha, epoch=epoch,
                       instance=instance, plan=plan)
        if report is not None:
            stats["hits"] += 1
            print(f"check cache: hit {key[:12]} ({plan.id}); full.check skipped (exact memo)", flush=True)
            _last.source = "memo"
            return report
    stats["misses"] += 1
    report = full.check(instance, plan)
    require_current()
    _last.source = "fresh"
    dumped = report.model_dump(mode="json")
    try:
        round_trip = CheckReport.model_validate(json.loads(json.dumps(dumped, allow_nan=False))).model_dump(mode="json")
    except (ValueError, TypeError):
        round_trip = None
    if round_trip == dumped and cacheable(report) and full.report_matches_inputs(report, instance, plan):
        from stowage.versions import CHECKER_VERSION
        if _store(path, {"format": FORMAT, "key": key, "instance_sha256": instance_sha, "plan_sha256": plan_sha,
                         "checker_epoch": epoch, "checker_version": CHECKER_VERSION,
                         "instance_id": instance.id, "plan_id": plan.id, "report": dumped}):
            stats["writes"] += 1
    else:
        stats["skipped"] += 1
        print(f"check cache: not stored ({plan.id}): report not cacheable", flush=True)
    return report
