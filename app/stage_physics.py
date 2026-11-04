"""Physics timeline for the stage page: real checker stability + strength at keyframes of a crane plan.

Plain-language background
-------------------------
* **GM** (metacentric height) is the ship's initial "stiffness" against heeling: positive and above a
  minimum means it springs back upright. **GZ** is the righting lever at each heel angle; the GZ curve and
  the area under it are what the IMO Intact Stability Code (IS Code) criteria test.
* **Shear force (SF) and bending moment (BM)** are the hull-girder loads along the ship's length: weight and
  buoyancy are not distributed equally, so the hull is bent like a beam. IACS sets limits per station.

For every keyframe *k* (the state after *k* crane lifts) this module replays the plan prefix with the move
engine and then calls the checker's own part functions (``check_stability``, ``check_strength``) and the
app's existing payload builders (``stability_payload`` for the GZ curve, ``_strength_payload`` for stations).
Nothing is re-derived in the browser or here: we only extract the checker's metrics.

Tank state per frame
--------------------
APPROXIMATION: the move schema has no ballast-transfer operations, so intermediate frames (k < n) use the tank
contents the ship arrived with (exactly what ``stowage.checker.full._intermediate`` does), while the final frame
applies the plan's declared departure ballast (``full._ballast``), which is the condition the full checker
approves. Intermediate frames therefore show the un-ballasted ship during cargo work, not a ballast programme.

Criteria per frame
------------------
Frames are first assessed with the sea-going IS Code part ("as if it sailed now"; gives the GZ curve). When that
cannot run because a hatch cover is off (the watertight envelope is open), the frame falls back to the checker's
in-port equilibrium criteria (``check_stability(..., in_port=True)``, D64), which yield GM/heel/trim/draughts but
no GZ curve. Intermediate verdicts are informational: the plan is judged only at departure.
"""
from __future__ import annotations

import hashlib
import json
import math
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Callable

from app import check_cache

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "experiments/mvp/ui/cache"
PHYSICS_VERSION = "stage-physics-v3"  # v2: GZ withheld on in-port (hatch-off) frames; v3: epoch-bound frames
_FRAME_SOURCES = ("app/stage_physics.py", "app/stability.py", "app/checker.py")  # these shape the frames
DRIFT_REASON = "physics sources changed since this process started: restart the app"
ALL_FRAMES_MAX_LIFTS = 80
EVEN_KEYFRAMES = 40
MAX_KEYFRAMES = 80
HEAVY_T = 20.0  # boxes at or above this gross mass count as "load-heavy changes" for keyframe selection

NOTE = ("APPROXIMATION: intermediate frames use arrival tank contents (no ballast transfers are modelled during "
        "cargo work, as in full._intermediate); the final frame applies the plan's declared departure ballast. "
        "Intermediate frames are informational: sea-going IS Code criteria 'as if departing now', or in-port "
        "equilibrium criteria (no GZ curve) while a hatch cover is off. The plan is judged only at departure.")


# ---------------------------------------------------------------- identity
def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def input_digests(instance, plan) -> dict:
    """sha256 of the canonical instance JSON and plan JSON; ``key`` = sha256(instance json + plan json)[:16]."""
    inst = _canonical(instance.model_dump(mode="json"))
    pl = _canonical(plan.model_dump(mode="json"))
    return {"instance_sha256": hashlib.sha256(inst).hexdigest(), "plan_sha256": hashlib.sha256(pl).hexdigest(),
            "key": hashlib.sha256(inst + pl).hexdigest()[:16]}


def cache_path(digests: dict, cache_dir: Path = CACHE_DIR) -> Path:
    return Path(cache_dir) / f"{digests['key']}.physics.json"


def _source_digest() -> str:
    h = hashlib.sha256()
    for name in _FRAME_SOURCES:
        h.update(name.encode() + b"\0" + (ROOT / name).read_bytes() + b"\0")
    return h.hexdigest()


_LOADED_SOURCES = _source_digest()  # the code this process imported


def physics_epoch() -> str:
    """Identity of frame code and numbers: format version + checker epoch (src, params, hulls, numerical environment)
    + the app modules that shape frames, as loaded by this process."""
    return hashlib.sha256(f"{PHYSICS_VERSION}|{check_cache.checker_epoch()}|{_LOADED_SOURCES}".encode()).hexdigest()


def physics_drift() -> str | None:
    """A reason when disk no longer matches the loaded physics epoch (frames are then neither served nor written)."""
    reason = check_cache.epoch_drift()
    if reason:
        return reason
    try:
        return None if _source_digest() == _LOADED_SOURCES else DRIFT_REASON
    except OSError:
        return DRIFT_REASON


class PhysicsEpochDrift(RuntimeError):
    """Frames from this process can no longer be stored as current."""


_FRAME_KEYS = ("step", "error", "criteria", "gz", "stability_passed", "strength_passed")
_FRAME_NUMBERS = ("gm_m", "gm_min_m", "heel_deg", "trim_m", "draught_aft_m", "draught_fwd_m", "draught_mean_m",
                  "max_draught_m", "displacement_t", "max_sf_ratio", "max_bm_ratio")
_FRAME_LISTS = ("sf_n", "bm_nm")


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _frame_ok(frame, n_lifts: int) -> bool:
    """Structure of one stored frame: every required key, step in range, finite-or-null numbers.

    A frame without an ``error`` must carry every measured field; an error frame carries only the required keys.
    """
    if not isinstance(frame, dict) or any(k not in frame for k in _FRAME_KEYS):
        return False
    step = frame["step"]
    if not isinstance(step, int) or isinstance(step, bool) or not 0 <= step <= n_lifts:
        return False
    if not all(frame[k] is None or isinstance(frame[k], bool) for k in ("stability_passed", "strength_passed")):
        return False
    if not all(frame[k] is None or isinstance(frame[k], str) for k in ("error", "criteria")):
        return False
    if frame["error"] is None and any(k not in frame for k in (*_FRAME_NUMBERS, *_FRAME_LISTS)):
        return False
    if not all(frame.get(k) is None or _number(frame[k]) for k in _FRAME_NUMBERS):
        return False
    for key in _FRAME_LISTS:
        value = frame.get(key)
        if value is not None and not (isinstance(value, list) and all(x is None or _number(x) for x in value)):
            return False
    gz = frame["gz"]
    return isinstance(gz, list) and all(isinstance(p, dict) and _number(p.get("deg")) and _number(p.get("m"))
                                        for p in gz)


def _frames_ok(frames, n_lifts: int) -> bool:
    if not isinstance(frames, list) or not all(_frame_ok(f, n_lifts) for f in frames):
        return False
    return len({f["step"] for f in frames}) == len(frames)


def _read_bound(instance, plan, cache_dir: Path) -> dict | None:
    """The cache file for these inputs, only if it binds to them and to the current physics epoch.

    The single identity gate of ``load_cached`` and ``load_partial``. Applies to every directory, including read-only
    run-local ``timeline-cache`` folders: a file without the epoch binding is a miss.
    """
    digests = input_digests(instance, plan)
    try:
        data = json.loads(cache_path(digests, cache_dir).read_text())
    except (OSError, ValueError):
        return None
    if (not isinstance(data, dict) or data.get("version") != PHYSICS_VERSION
            or data.get("physics_epoch") != physics_epoch() or data.get("instance_sha256") != digests["instance_sha256"]
            or data.get("plan_sha256") != digests["plan_sha256"] or physics_drift()):
        return None
    return data


def load_cached(instance, plan, cache_dir: Path = CACHE_DIR) -> dict | None:
    """The complete physics block, only when identity, every frame and the keyframe set all check out; else None."""
    data = _read_bound(instance, plan, cache_dir)
    physics = data.get("physics") if data is not None and data.get("complete") is True else None
    if not isinstance(physics, dict):
        return None
    frames, policy = select_keyframes(instance, plan)
    stored = physics.get("frames")
    if (not _frames_ok(stored, len(plan.moves)) or [f["step"] for f in stored] != frames
            or physics.get("keyframes") != policy or physics.get("n_lifts") != len(plan.moves)):
        return None
    return physics


# ---------------------------------------------------------------- keyframes
def select_keyframes(instance, plan) -> tuple[list[int], str]:
    """All frames if <= 80 lifts; else ~40 evenly spaced plus frames right after heavy-box loads (cap 80)."""
    n = len(plan.moves)
    if n <= ALL_FRAMES_MAX_LIFTS:
        return list(range(n + 1)), "all"
    frames = {round(i * n / EVEN_KEYFRAMES) for i in range(EVEN_KEYFRAMES + 1)}
    mass = {c.id: c.gross_mass_kg / 1000 for c in instance.containers}
    heavy = [i + 1 for i, m in enumerate(plan.moves)
             if m.kind in {"load", "discharge", "restow_on", "restow_off"} and mass.get(m.container_id, 0) >= HEAVY_T]
    budget = MAX_KEYFRAMES - len(frames)
    if heavy and budget > 0:
        step = max(1, math.ceil(len(heavy) / budget))
        frames.update(heavy[::step])
    return sorted(f for f in frames if 0 <= f <= n), "every-k"


# ---------------------------------------------------------------- one frame
def _clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _frame_from_parts(instance, ship, stab, strength, criteria: str) -> dict:
    """Extract contract fields from checker PartReports (and the app's existing payload builders)."""
    from app.checker import _part_passed, _strength_payload
    from app.stability import stability_payload
    m = stab.metrics if stab else {}
    sp = stability_payload(instance, ship, stab) if stab and stab.ran and not stab.error else None
    gm_limits = [c["limit"] for c in (sp or {}).get("criteria", []) if c.get("key") in {"gm_m", "initial_gm"}]
    hull = instance.vessel.hull
    limits = [x for x in (getattr(hull, "max_draught_m", None), instance.port.max_draught_m) if x is not None]
    st = _strength_payload(instance, strength) if strength else None
    by_x = {s["x_m"]: s for s in (st or {}).get("stations", [])}
    xs = [s.x_m for s in hull.stations] if hull else []
    list_rad = m.get("list_rad")
    return {
        "criteria": criteria,
        "gm_m": _clean(m.get("gm_m")), "gm_min_m": max(gm_limits) if gm_limits else None,
        "heel_deg": _clean(math.degrees(list_rad)) if list_rad is not None else None,
        "trim_m": _clean(m.get("trim_m")),
        "draught_aft_m": _clean(m.get("draught_aft_m")), "draught_fwd_m": _clean(m.get("draught_fwd_m")),
        "draught_mean_m": _clean(m.get("draught_mean_m")), "max_draught_m": min(limits) if limits else None,
        "displacement_t": round(m["displacement_kg"] / 1000, 3) if m.get("displacement_kg") is not None else None,
        "sf_n": [round(by_x[x]["sf_n"]) if x in by_x else None for x in xs] if st else None,
        "bm_nm": [round(by_x[x]["bm_nm"]) if x in by_x else None for x in xs] if st else None,
        "max_sf_ratio": _clean((st or {}).get("metrics", {}).get("max_sf_ratio")),
        "max_bm_ratio": _clean((st or {}).get("metrics", {}).get("max_bm_ratio")),
        "gz": [{"deg": round(math.degrees(p["heel_rad"]), 3), "m": round(p["gz_m"], 5)}
               for p in (sp or {}).get("gz_curve", [])],
        "gz_error": (sp or {}).get("curve_error"),
        "stability_passed": _part_passed(stab) if stab else None,
        "strength_passed": _part_passed(strength) if strength else None,
        "stability_violations": sorted({v.rule_id for v in stab.violations}) if stab else [],
        "strength_violations": sorted({v.rule_id for v in strength.violations}) if strength else [],
        "error": "; ".join(e for e in ((stab.error if stab else "stability not run"),
                                       (strength.error if strength else "strength not run")) if e) or None,
    }


def compute_frame(instance, plan, k: int) -> dict:
    """Replay ``plan.moves[:k]`` and run the checker's stability + strength parts on the reached ship."""
    from stowage.checker.full import _ballast, _run_part
    from stowage.checker.stability import check_stability
    from stowage.checker.strength import check_strength
    from stowage.moves import apply_plan
    # One engine pass over the prefix (apply_plan: ~0.07 s for 1 486 lifts, vs ~3 ms per apply_move call).
    # End-of-plan conditions (covers back on, quay buffer empty) are not lift legality, so only the executed
    # count matters here: stop-at-first means executed < k exactly when a lift in the prefix is illegal.
    result = apply_plan(instance, plan.model_copy(update={"moves": list(plan.moves[:k])}))
    if result.executed < k:
        bad = next((v for v in result.violations if v.move_index is not None), None)
        return {"step": k, "error": f"illegal lift before frame {k}: {bad.message if bad else 'engine rejected'}",
                "criteria": None, "stability_passed": None, "strength_passed": None, "gz": [], "tank_state": None}
    state = result.final_state
    ship, tank_state = state.ship, "arrival tanks (no ballast transfer)"
    final = k == len(plan.moves)
    if final:
        ship, problems = _ballast(instance, plan, ship)
        tank_state = "declared departure ballast" + (" (invalid declaration diagnosed)" if problems else "")
    stab = _run_part(instance, ship, "stability", check_stability)
    criteria = "departure: sea-going IS Code" if final else "sea-going IS Code (as if departing now)"
    if not stab.ran and stab.error and "hatch" in stab.error.lower():
        # A hatch cover is off: the sea-going envelope is open, so use the checker's in-port criteria.
        port_part = _run_part(instance, ship, "stability",
                              lambda i, s, call_index: check_stability(i, s, call_index=call_index, in_port=True))
        port_part.metrics.setdefault("note: sea-going check skipped: " + stab.error, 1.0)
        stab, criteria = port_part, "in-port equilibrium (hatch cover off; no GZ curve)"
    strength = _run_part(instance, ship, "strength", check_strength)
    frame = _frame_from_parts(instance, ship, stab, strength, criteria)
    if criteria.startswith("in-port"):
        # The panel builder would still draw a closed-hull GZ curve; with a hatch cover off the weathertight
        # envelope that curve assumes is open, so it is withheld rather than shown as if valid.
        frame.update(gz=[], gz_error="withheld: hatch cover off (open envelope); in-port criteria only")
    frame.update(step=k, tank_state=tank_state, covers_off=sorted(state.ship.covers_off))
    return frame


def _worker(payload: tuple) -> dict:
    instance_json, plan_json, k = payload
    from stowage.model import Instance, MovePlan
    instance, plan = Instance.model_validate(instance_json), MovePlan.model_validate(plan_json)
    try:
        return compute_frame(instance, plan, k)
    except Exception as error:  # one failing frame must not lose the others
        return {"step": k, "error": f"{type(error).__name__}: {error}", "criteria": None, "gz": [],
                "stability_passed": None, "strength_passed": None}


# ---------------------------------------------------------------- whole timeline
def physics_header(instance, keyframes_policy: str) -> dict:
    hull = instance.vessel.hull
    stations = list(hull.stations) if hull else []
    return {"stations_x_m": [s.x_m for s in stations],
            "sf_min_n": [s.min_shear_n for s in stations], "sf_max_n": [s.max_shear_n for s in stations],
            "bm_min_nm": [s.min_bending_nm for s in stations], "bm_max_nm": [s.max_bending_nm for s in stations],
            "keyframes": keyframes_policy, "note": NOTE}


def compute_physics(instance, plan, *, frames: list[int] | None = None, workers: int = 1,
                    done: dict[int, dict] | None = None,
                    on_frame: Callable[[int, dict], None] | None = None) -> dict:
    """Compute (or resume) the physics block. ``done`` maps already computed frame index -> frame."""
    policy = "custom"
    if frames is None:
        frames, policy = select_keyframes(instance, plan)
    results = dict(done or {})
    todo = [k for k in frames if k not in results]
    if workers <= 1:
        for k in todo:
            results[k] = _worker((instance.model_dump(mode="json"), plan.model_dump(mode="json"), k))
            if on_frame:
                on_frame(k, results[k])
    else:
        inst_json, plan_json = instance.model_dump(mode="json"), plan.model_dump(mode="json")
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for frame in pool.map(_worker, [(inst_json, plan_json, k) for k in todo]):
                results[frame["step"]] = frame
                if on_frame:
                    on_frame(frame["step"], frame)
    block = physics_header(instance, policy)
    block["frames"] = [results[k] for k in sorted(results) if k in set(frames)]
    block["n_lifts"] = len(plan.moves)
    return block


def write_cache(instance, plan, physics: dict | None, *, partial: dict | None = None,
                cache_dir: Path = CACHE_DIR) -> Path:
    """Write the cache file (``complete`` only when ``physics`` is given); atomic replace."""
    reason = physics_drift()
    if reason:
        raise PhysicsEpochDrift(reason)
    digests = input_digests(instance, plan)
    path = cache_path(digests, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"version": PHYSICS_VERSION, "physics_epoch": physics_epoch(),
                            "instance_id": instance.id, "plan_id": plan.id,
                            "instance_sha256": digests["instance_sha256"], "plan_sha256": digests["plan_sha256"],
                            "key": digests["key"], "complete": physics is not None, "physics": physics,
                            "partial_frames": None if physics is not None else
                            [partial[k] for k in sorted(partial or {})]}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, allow_nan=False, default=str))
    tmp.replace(path)
    return path


def load_partial(instance, plan, cache_dir: Path = CACHE_DIR) -> dict[int, dict]:
    """Frames checkpointed for exactly these inputs at the current physics epoch (resume support).

    Same identity gate as ``load_cached``: obsolete or malformed frames are refused whole, never resumed.
    """
    data = _read_bound(instance, plan, cache_dir)
    if data is None:
        return {}
    frames = (data.get("physics") or {}).get("frames") if data.get("complete") else data.get("partial_frames")
    return {f["step"]: f for f in frames} if _frames_ok(frames, len(plan.moves)) else {}
