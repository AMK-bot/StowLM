"""Disposable, content-addressed JSON cache for expensive whole-ship assembly.

The key fingerprints parameters, the vessel builder epoch and implementation of
geometry, hydrostatics, tanks, strength limits and model serialization. A slice
has its own key and is derived from the corresponding cached full vessel.
Numerical-library versions also separate results from different installations.
No numerical integration or safety constraints are changed by caching.

Files contain ordinary ``Vessel.model_dump_json()`` output and are validated on
read. Atomic replacement permits concurrent writers; a damaged or unwritable
cache falls back to building. The bounded process memo stores JSON, so every
caller receives an independent model, including nested mutable data. Set
``STOWAGE_VESSEL_CACHE=0`` to bypass both caches, or
``STOWAGE_VESSEL_CACHE_DIR`` to choose an alternate cache directory.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import tempfile

from pydantic import ValidationError

from stowage.model import Vessel
from stowage.toml_cache import load_value as load_toml_value
from stowage.params import repo_root
from stowage import versions

VESSEL_BUILDER_VERSION = "2"
_MEMO: OrderedDict[str, str] = OrderedDict()
_MEMO_LIMIT = 16


def clear_vessel_memo() -> None:
    """Clear this process's vessel cache (disk entries remain reusable)."""
    _MEMO.clear()


def _source_files() -> list[Path]:
    root = Path(__file__).resolve().parents[1]
    # Production assembly uses strength_limits, not the assessment/checker
    # modules. Global version labels and reference-only design documents are
    # not construction inputs (D30). Keep actual geometry/model dependencies.
    modules = (
        "vessel/fleet.py", "vessel/fleet_cache.py", "vessel/fleet_conditions.py",
        "vessel/fleet_balance.py", "vessel/fleet_booking.py", "vessel/fleet_gm.py", "vessel/fleet_slices.py",
        "vessel/fleet_voyage.py", "vessel/fleet_manual.py", "vessel/fleet_lashing_design.py",
        "vessel/fleet_manual_witness.py",
        "vessel/hull.py", "vessel/hydrostatics.py", "vessel/hull_offsets.py",
        "vessel/tanks.py", "vessel/hull_tanks.py", "vessel/hull_tank_sections.py",
        "model/__init__.py", "model/base.py", "model/vessel.py", "model/provenance.py",
        "model/voyage.py", "model/ship.py", "model/port.py", "ports/profiles.py",
        "checker/strength_limits.py", "conventions.py", "params.py", "toml_cache.py", "cache_singleflight.py",
    )
    return sorted(root / module for module in modules)


def _cache_key(name: str, slice_name: str, with_hull: bool, params_bytes: bytes) -> str:
    fingerprint = {
        "format": 2, "builder": VESSEL_BUILDER_VERSION,
        "schema": versions.SCHEMA_VERSION,
        "name": name, "slice": slice_name, "with_hull": with_hull,
        "params": sha256(params_bytes).hexdigest(),
        "sources": {str(path.relative_to(Path(__file__).resolve().parents[1]))
                    if path.is_relative_to(Path(__file__).resolve().parents[1]) else str(path):
                    sha256(path.read_bytes()).hexdigest() for path in _source_files()},
        "dependencies": {package: version(package) for package in ("numpy", "scipy", "pydantic")},
        "condition_inputs": {str(path.relative_to(repo_root())): sha256(path.read_bytes()).hexdigest()
                             for path in (repo_root()/"params/cargo/feeder_export.toml",
                                          repo_root()/"params/ports/laem_chabang_like.toml",
                                          repo_root()/"params/ports/small_feeder.toml")
                             if path.is_file()},
    }
    return sha256(json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _cache_dir() -> Path:
    override = os.environ.get("STOWAGE_VESSEL_CACHE_DIR")
    return Path(override) if override else repo_root() / "experiments/cache/vessels"


def _remember(key: str, payload: str) -> None:
    _MEMO[key] = payload
    _MEMO.move_to_end(key)
    while len(_MEMO) > _MEMO_LIMIT:
        _MEMO.popitem(last=False)


def cached_vessel(name: str, *, slice_name: str, with_hull: bool,
                  params_bytes: bytes, build: Callable[[], Vessel]) -> Vessel:
    """Build or validate a cached vessel; cache use never mutates the result."""
    if os.environ.get("STOWAGE_VESSEL_CACHE", "1").lower() in {"0", "false", "off"}:
        return build()
    if with_hull:
        form = load_toml_value(params_bytes, "hull_form", {})
        if form.get("family") == "stowlm_offsets_v1":
            from stowage.vessel.hull_offsets import verified_offset_bytes

            # Validate before both memo and disk hits. A pinned digest in the
            # parameter file alone does not prove that the source still exists
            # and matches the auditable geometry input (D20/D30).
            verified_offset_bytes(form["offsets_path"], expected_sha256=form["offsets_sha256"])
    key = _cache_key(name, slice_name, with_hull, params_bytes)
    if key in _MEMO:
        payload = _MEMO[key]
        _MEMO.move_to_end(key)
        return Vessel.model_validate_json(payload)
    target = _cache_dir() / f"{name}.{slice_name}.{'hull' if with_hull else 'geometry'}.{key}.json"
    try:
        payload = target.read_text(encoding="utf-8")
        vessel = Vessel.model_validate_json(payload)
    except (OSError, ValueError, ValidationError):
        from stowage.cache_singleflight import cache_singleflight

        # Opt-in only; the raw offset check above remains before every cache
        # hit and lock wait. Waiting peers must validate the published JSON.
        with cache_singleflight(target) as locked:
            payload = None
            if locked:
                try:
                    payload = target.read_text(encoding="utf-8")
                    vessel = Vessel.model_validate_json(payload)
                except (OSError, ValueError, ValidationError):
                    payload = None
            if payload is None:
                vessel = build()
                payload = vessel.model_dump_json()
                temporary: Path | None = None
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                                     prefix=".vessel-", suffix=".tmp", delete=False) as stream:
                        temporary = Path(stream.name)
                        stream.write(payload)
                    os.replace(temporary, target)
                except OSError:
                    pass  # The cache is optional; builder errors above still propagate.
                finally:
                    if temporary is not None:
                        try:
                            temporary.unlink(missing_ok=True)
                        except OSError:
                            pass
    _remember(key, payload)
    # Deserialize cold results too, keeping JSON representation identical on all paths.
    return Vessel.model_validate_json(payload)
