"""D58 acceptance of fixed slice cargo as an independent D57 ship condition.

The exact checker remains authoritative. The IS Code MSC.267(85), Part B 2.3
ratio is the smallest measured/required GZ-area or lever ratio; 1.05 at arrival
reserves five percent above the statutory minimum for the decision cargo.
The empty-plan witness may carry different adjustable ballast from the booking;
its tanks are acceptance evidence only and never become the plan's initial tanks.
Caching changes no physics: keys include the exact witness inputs, implementation
bytes, numerical dependencies and physics environment, never vessel name alone.
With rule-freeze-v1 active, the actual prepared berth arrival also passes D64
P1--P6; a full empty ship receives that berth certificate without a seagoing
empty-ship claim. Departure witness tanks never replace its emitted arrival.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
import inspect
import math
import os
from pathlib import Path
from time import perf_counter, time_ns
from typing import Any

from stowage.model import BallastCondition, Instance, MovePlan
from stowage.model.yard import YardState

_B23 = dict(zip(("area_0_30_mrad", "area_0_40_mrad", "area_30_40_mrad",
                  "gz_at_or_above_30_m", "gz_max_m", "area_0_flood_mrad"),
                 (.009, .016, .006, .033, .042, .029)))
_PHASES = {"departure", "mid_voyage", "arrival"}
_LOADED_REFERENCE_SIZES = frozenset({"medium", "full"})
_MEMO: OrderedDict[str, BackgroundAcceptance] = OrderedDict()
_TABLE_HASHES: OrderedDict[tuple, str] = OrderedDict()
# The shared directory includes more than 1,200 legacy tables. Retain their
# small identity/digest records so sequential manifests do not evict every hash
# and read gigabytes again on each background key. Table bytes are never held.
_TABLE_HASH_LIMIT = 4096


@dataclass(frozen=True)
class BackgroundAcceptance:
    accepted: bool
    reasons: tuple[str, ...]
    finisher_status: str
    witness_tank_masses_kg: dict[str, float]
    states: dict[str, dict[str, float]]
    elapsed_s: float
    cache_hit: bool = False
    physics_sha256: str = ""
    in_port: dict[str, Any] = field(default_factory=dict)
    unknown: bool = False

    @property
    def status(self) -> str:
        return "unknown" if self.unknown else ("accepted" if self.accepted else "rejected")

    @property
    def tank_masses_kg(self) -> dict[str, float]:
        """Compatibility alias: these are empty-plan witness tanks, not booking tanks."""
        return self.witness_tank_masses_kg


class BackgroundAcceptanceUnknown(RuntimeError):
    """No physical verdict: producers must stop, never redraw this candidate."""

    def __init__(self, result: BackgroundAcceptance):
        self.result = result
        super().__init__("Background acceptance unknown: " + "; ".join(result.reasons))


def _timed_out(message: object) -> bool:
    text = str(message).lower()
    return any(token in text for token in ("timeout", "timed out", "time budget", "deadline",
                                           "budget exceeded", "budget exhausted"))


def _unknown_part(part) -> bool:
    """Numerical deadlines/internal errors cannot establish infeasibility."""
    return (_timed_out(part.error) or any(_timed_out(v.message)
            or v.rule_id == "checker.internal_error" for v in part.violations))


def _arrival_pass_one(instance: Instance):
    """Use the exact public lift evaluator, including worked FSM and revised P5.

    Equal arrival/departure inputs select candidate-independent pass 1: HEEL
    and carried consumables use maximum FSM; other ballast uses actual fill.
    The separate seagoing witness is deliberately absent from this context.
    """
    from stowage.checker.in_port_evaluator import check_state
    from stowage.checker.in_port_liquids import prepare_context
    context = prepare_context(instance, instance.onboard)
    return check_state(instance, instance.onboard, context, warm_start=False)


def _seagoing_context_keywords(checker, finisher) -> dict[str, str] | None:
    """Require the owners' explicit context API; generic **kwargs is no proof.

    Ordinary HEEL sea ballast must not enter the actual-port replay, and all
    other activated rules must remain on. Until both owners expose this public
    contract, the independent sea certificate is undecided, never rejected.
    """
    if all("operating_context" in inspect.signature(function).parameters
           for function in (checker, finisher)):
        return {"operating_context": "seagoing_witness"}
    return None


@dataclass(frozen=True)
class _WitnessReport:
    """A full CheckReport restricted to the parts that judge a sea witness."""
    parts: dict
    passed: bool
    excluded: tuple[str, ...]


def _witness_report(report, excluded: frozenset[str]):
    """Drop only ``excluded`` parts; ``passed`` keeps CheckReport semantics.

    The remaining parts pass only if each ran without error and carries no
    hard violation, exactly as the full report's own verdict would require.
    """
    if not excluded:
        return report
    parts = {name: part for name, part in report.parts.items() if name not in excluded}
    passed = all(part.ran and not part.error and not any(
        getattr(v, "severity", "hard") == "hard" for v in part.violations) for part in parts.values())
    return _WitnessReport(parts, passed, tuple(sorted(excluded & set(report.parts))))


def clear_background_cache() -> None:
    _MEMO.clear()


def background_instance(instance: Instance) -> Instance:
    """Remove decision cargo and its instructions, retaining the actual ship."""
    aboard = {p.container_id for p in instance.onboard.placements}
    return instance.model_copy(update={
        "load_list": [], "discharge_list": [], "plan_calls": [], "forecasts": [],
        "plan_constraints": [], "yard": YardState(yard_id=instance.yard.yard_id),
        "containers": [c for c in instance.containers if c.id in aboard],
    }, deep=True)


def _stability_table_directory() -> Path:
    from stowage.checker.stability_tables import cached_geometry
    # Dispatch uses this bound default, which may differ from a later mutation
    # of the module constant. Relative paths follow the checker's current cwd.
    return Path(cached_geometry.__kwdefaults__["cache_dir"]).resolve()


def _table_file_identity(path: Path) -> dict:
    """Hash stable bytes, reusing hashes only while OS file identity is unchanged.

    ctime_ns guards writes that preserve mtime/size; opening and checking the
    descriptor and path before/after hashing guards replacement during the read.
    These are trusted local cache files, not attacker-controlled timestamps.
    """
    def identity(stat):
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    for _ in range(3):
        try:
            before = path.stat()
            signature = identity(before)
            key = (str(path.resolve()), *signature)
            digest = _TABLE_HASHES.get(key)
            if digest is None:
                checksum = sha256()
                with path.open("rb") as stream:
                    if identity(os.fstat(stream.fileno())) != signature:
                        continue
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        checksum.update(block)
                    if identity(os.fstat(stream.fileno())) != signature:
                        continue
                digest = checksum.hexdigest()
            if identity(path.stat()) != signature:
                continue
            _TABLE_HASHES[key] = digest
            _TABLE_HASHES.move_to_end(key)
            while len(_TABLE_HASHES) > _TABLE_HASH_LIMIT:
                _TABLE_HASHES.popitem(last=False)
            return {"name": path.name, "sha256": digest, "identity": signature,
                    "selection_mtime": before.st_mtime}
        except OSError:
            continue
    # An unstable/missing entry must never hit an old acceptance key.
    return {"name": path.name, "unstable_read_nonce": time_ns()}


def _stability_table_manifest() -> dict:
    directory = _stability_table_directory()
    # Enumerate on every call: additions/deletions and the checker's newest-mtime
    # choice matter even when every surviving file's bytes are unchanged.
    tables = [_table_file_identity(path) for path in directory.glob("*.npz")]
    certificates = [_table_file_identity(path)
                    for path in sorted(directory.glob("*.certificate.json"))]
    return {"directory": str(directory), "tables_in_directory_order": tables,
            "newest_mtime_order": [entry["name"] for entry in sorted(tables,
                key=lambda entry: entry.get("selection_mtime", float("-inf")), reverse=True)],
            "certificates": certificates}


def _implementation_key() -> dict:
    from stowage.generate.physics_identity import current_physics_identity
    return current_physics_identity()


def _content_key(instance: Instance, time_limit_s: float, *, implementation: dict | None = None,
                 operational_instances: tuple[Instance, Instance] | None = None) -> str:
    # IDs, seeds and provenance label evidence; all checker inputs stay present.
    payload = instance.model_dump(mode="json", exclude={"id", "seed", "family", "provenance"})
    content = {"format": 4, "background": payload,
               "implementation": _implementation_key() if implementation is None else implementation,
               "finisher_budget_s": time_limit_s,
               "stability_tables": _stability_table_manifest()}
    if operational_instances is not None:
        # S0 ballast, H, pending demand and its required paths are not part of
        # the stripped empty-region witness. Bind them before removing cargo.
        content["activated_operations"] = [item.model_dump(mode="json",
            exclude={"id", "seed", "family", "provenance"}) for item in operational_instances]
    return sha256(json.dumps(content, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode()).hexdigest()


def _prepare_arrival_instance(instance: Instance) -> Instance:
    from stowage.vessel.fleet_berth import prepare_arrival
    return prepare_arrival(instance)


def _d69_failures(instance: Instance) -> tuple[str, ...]:
    """Reject immovable high columns; use shared geometry for needed lifts.

    Load domains deliberately overapproximate eventually available targets.
    A blocked optional row is never called mandatory: every syntactic target
    alternative must cross fixed high cargo before that diagnosis is emitted.
    """
    from stowage.conventions import cells_of_slot, format_slot, parse_slot
    from stowage.model.moves import Discharge, HatchCoverOff, Load
    from stowage.moves import crossing_paths

    height_limit = instance.port.max_crane_height_tiers
    if height_limit is None:
        return ()  # Same explicit unknown-H semantics as public crossing_paths.
    occupied = {cid for load in instance.vessel.fixed_loads
                for box in load.cargo_boxes or [] for cid in box.cell_ids}
    stacks = {stack.id: stack for stack in
              instance.vessel.stacks + instance.vessel.fixed_load_stacks}
    high = {stack.id for stack in stacks.values() if stack.on_deck and
            max((index+1 for index, cid in enumerate(stack.cell_ids) if cid in occupied),
                default=0) >= height_limit}
    if not high:
        return ()
    reasons = {"background.d69.fixed_deck_at_crane_height"}
    cells = {cell.id: cell for cell in instance.vessel.cells}
    boxes = {box.id: box for box in instance.containers}

    def blocked(move):
        return bool(high.intersection(crossing_paths(instance, move).ship_stack_ids))

    try:
        aboard = {placement.container_id: placement.slot for placement in instance.onboard.placements}
        for cid in instance.discharge_list:
            if cid in aboard and blocked(Discharge(call_index=instance.call_index,
                    container_id=cid, src=aboard[cid], dst="truck")):
                reasons.add("background.d69.fixed_blocks_needed_row")
        for cover_id in instance.onboard.covers_off:
            from stowage.model.moves import HatchCoverOn
            if blocked(HatchCoverOn(call_index=instance.call_index, cover_id=cover_id)):
                reasons.add("background.d69.fixed_blocks_needed_row")
        for length in {boxes[cid].length_ft for cid in instance.load_list if cid in boxes and cid not in aboard}:
            cid = next(cid for cid in instance.load_list if cid in boxes and boxes[cid].length_ft == length)
            targets = set()
            for cell in cells.values():
                address = parse_slot(cell.id)
                target = cell.id if length == 20 else format_slot(address.bay+1, address.row, address.tier)
                if set(cells_of_slot(target)) <= cells.keys():
                    targets.add(target)
            def target_blocked(target):
                move = Load(call_index=instance.call_index, container_id=cid, src="truck", dst=target)
                if blocked(move):
                    return True
                covers = {stacks[cells[support].stack_id].hatch_cover_id
                          for support in cells_of_slot(target) if not parse_slot(support).on_deck}
                return any(blocked(HatchCoverOff(call_index=instance.call_index, cover_id=cover))
                           for cover in covers if cover is not None)
            if targets and all(target_blocked(target) for target in targets):
                reasons.add("background.d69.fixed_blocks_needed_row")
    except (KeyError, ValueError):
        # Missing geometry is never interpreted as an unobstructed path.
        reasons.add("background.d69.crossing_geometry_unavailable")
    return tuple(sorted(reasons))


def evaluate_background(instance: Instance, *, time_limit_s: float = 10.,
                        use_cache: bool = True,
                        arrival_instance: Instance | None = None) -> BackgroundAcceptance:
    """Return a physical verdict or explicit UNKNOWN without changing inputs."""
    started = perf_counter()
    try:
        return _evaluate_background(instance, time_limit_s=time_limit_s,
                                    use_cache=use_cache, arrival_instance=arrival_instance)
    except Exception as error:
        # The public checkers normally return budget diagnostics in PartReport,
        # but a propagated deadline is equally inconclusive. Keep it out of the
        # redraw path and preserve the emission wrapper's attempt accounting.
        if not _timed_out(error):
            raise
        return BackgroundAcceptance(False, ("background.compute.deadline",), "unknown",
            {}, {}, perf_counter()-started, in_port={"error": str(error)}, unknown=True)


def _evaluate_background(instance: Instance, *, time_limit_s: float = 10.,
                        use_cache: bool = True,
                        arrival_instance: Instance | None = None) -> BackgroundAcceptance:
    """Certify actual berth arrival and the independent D57 empty-plan witness.

    A timeout returns ``status='unknown'``, never an infeasibility claim, and
    is not cached. The emission wrapper raises on that status to prevent redraw.
    Returned witness tanks certify only the empty decision
    region; they must never initialize the booked plan. Actual fixed consumables
    are retained while the vessel's separate adjustable-ballast witness is used.
    Inputs and cached nested outputs are isolated.
    With activation on, ``instance`` retains the booked departure context;
    ``arrival_instance`` is the separately prepared actual berth state, when
    the wrapper already prepared it. Otherwise preparation runs here.
    """
    from stowage.checker import check, departure_condition
    from stowage.generate.physics_identity import PhysicsInputError
    from stowage.solver.ballast import finish_ballast
    from stowage.vessel.fleet_conditions import empty_background_witness_tank_masses
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    if not math.isfinite(time_limit_s) or time_limit_s <= 0:
        raise ValueError("background finisher budget must be positive and finite")
    started = perf_counter()
    # D30: even an exact cached witness is valid only while its pinned public
    # physical inputs remain available and checksum-correct. Validate before
    # every lookup, including explicit uncached runs; cached geometry is not
    # substitute evidence that the source still exists.
    try:
        implementation = _implementation_key()
    except PhysicsInputError:
        return BackgroundAcceptance(False, ("background.physics_source.invalid",),
                                    "unavailable", {}, {}, perf_counter()-started)
    activated = rule_freeze_v1_enabled()
    actual_arrival = None
    if activated:
        try:
            actual_arrival = arrival_instance if arrival_instance is not None else _prepare_arrival_instance(instance)
        except (ValueError, TimeoutError) as error:
            return BackgroundAcceptance(False, ("background.arrival_preparation.unavailable",),
                "not_checked", {}, {}, perf_counter()-started,
                physics_sha256=implementation.get("sha256", ""), unknown=_timed_out(error))
    background = background_instance(instance)
    seagoing = _requires_seagoing_background(instance.vessel, size=instance.size_class)
    recipe = empty_background_witness_tank_masses(background.vessel) if seagoing or not activated else {}
    witness_kind = "empty_plan"
    if activated and seagoing and instance.size_class in _LOADED_REFERENCE_SIZES:
        # D64 rev3.1 Correction: medium/full slices sail with their booking
        # loaded; an empty decision region is a light ship no feeder sails as.
        # The witness carries the D48 booking reference at the actual booked
        # mass, with the booking's own departure ballast. Never emitted.
        from stowage.vessel.fleet_conditions import loaded_reference_witness_vessel, slice_condition
        booked = {box.id: box.gross_mass_kg for box in instance.containers}
        loaded = loaded_reference_witness_vessel(background.vessel, booking_mass_kg=sum(
            booked[cid] for cid in instance.load_list if cid in booked) or None)
        if loaded is not None:
            background = background.model_copy(update={"vessel": loaded})
            # The builder's D48 reference sizing balanced mass and both
            # horizontal moments for this reference; else the booked tanks.
            sizing = (slice_condition(instance.vessel) or {}).get("booking_reference_ballast_sizing") or {}
            recipe = dict(instance.onboard.tank_masses_kg)
            recipe.update(sizing.get("tank_masses_kg") or {})
            witness_kind = "loaded_booking_reference"
    witness_tanks = dict(background.onboard.tank_masses_kg)
    # Only ballast changes between the booking and its empty-plan witness.
    # Preserve actual fixed consumables and unknown IDs so an invalid declaration
    # cannot be repaired or hidden by this acceptance-only substitution.
    for tank in background.vessel.tanks:
        if tank.adjustable and (seagoing or not activated):
            witness_tanks[tank.id] = recipe.get(tank.id, 0.)
    background.onboard.tank_masses_kg = witness_tanks
    operations = (instance, actual_arrival) if activated else None
    key = _content_key(background, time_limit_s, implementation=implementation,
                       operational_instances=operations) if use_cache else None
    if key in _MEMO:
        _MEMO.move_to_end(key)
        return replace(deepcopy(_MEMO[key]), elapsed_s=perf_counter()-started, cache_hit=True)

    def finalize(result, *, timed_not_found=False):
        if result.accepted:
            try:
                current_implementation = _implementation_key()
                current_key = (_content_key(background, time_limit_s, implementation=current_implementation,
                    operational_instances=operations) if use_cache else None)
            except PhysicsInputError:
                return replace(result, accepted=False, reasons=("background.physics_source.invalid",),
                               elapsed_s=perf_counter()-started)
            if current_implementation != implementation or (use_cache and current_key != key):
                return replace(result, accepted=False, reasons=("background.physics_input.changed",),
                               elapsed_s=perf_counter()-started)
            result = replace(result, elapsed_s=perf_counter()-started)
            if use_cache and not timed_not_found:
                _MEMO[key] = deepcopy(result)
                while len(_MEMO) > 128:
                    _MEMO.popitem(last=False)
        return result

    in_port = {}
    seagoing_keywords = {}
    excluded_parts: frozenset[str] = frozenset()
    if activated:
        d69 = _d69_failures(actual_arrival)
        if d69:
            return BackgroundAcceptance(False, d69, "not_checked", {}, {}, perf_counter()-started,
                                        physics_sha256=implementation.get("sha256", ""))
        try:
            part = _arrival_pass_one(actual_arrival)
        except (ValueError, ArithmeticError, KeyError, TypeError, RuntimeError, TimeoutError) as error:
            return BackgroundAcceptance(False, ("arrival_in_port/checker.unavailable",),
                "not_checked", {}, {}, perf_counter()-started,
                physics_sha256=implementation.get("sha256", ""),
                in_port={"phase": "arrival_s0", "ran": False, "error": str(error)},
                unknown=_timed_out(error) or not isinstance(error, (ValueError, KeyError)))
        in_port = {"phase": "arrival_s0", "metrics": deepcopy(part.metrics),
                   "tank_masses_kg": dict(actual_arrival.onboard.tank_masses_kg),
                   "ran": part.ran, "error": part.error}
        arrival_reasons = {f"arrival_in_port/{violation.rule_id}" for violation in part.violations}
        if not part.ran or part.error:
            arrival_reasons.add("arrival_in_port/checker.unavailable")
        if arrival_reasons:
            return BackgroundAcceptance(False, tuple(sorted(arrival_reasons)), "not_checked",
                {}, {}, perf_counter()-started, physics_sha256=implementation.get("sha256", ""),
                in_port=in_port, unknown=_unknown_part(part))
        if not seagoing:
            # A light full ship is certified for berth S0 only. It is not a
            # seagoing empty ship or a D57 ballast-only loading condition.
            return finalize(BackgroundAcceptance(True, (), "not_required", {}, {},
                perf_counter()-started, physics_sha256=implementation.get("sha256", ""),
                in_port=in_port))
        seagoing_keywords = _seagoing_context_keywords(check, finish_ballast)
        if seagoing_keywords is None:
            # D64 rev3.1 Correction: the witness is a SEAGOING condition and
            # HEEL is ordinary ballast in it. Without the owners' explicit
            # context API, run the unchanged full check and exclude only its
            # D64 in-port replay part (a fictitious berth state of the sea
            # witness, which also carries the port HEEL-total rule). Every
            # other activated part and all three D57 states still decide; the
            # actual berth arrival s0 was certified above by pass 1.
            seagoing_keywords, excluded_parts = {}, frozenset({"in_port"})
            in_port = {**in_port, "witness_excluded_parts": ["in_port"],
                       "witness_context": "seagoing_witness; HEEL ordinary ballast (D64 rev3.1 Correction)"}
    if activated and seagoing:
        in_port = {**in_port, "witness_kind": witness_kind}
    plan = MovePlan(id="background-empty", instance_id=background.id,
                    call_index=background.call_index, moves=[])
    initial = plan.model_copy(update={"ballast": BallastCondition(source="planner",
        tank_masses_kg={tank.id: background.onboard.tank_masses_kg.get(tank.id, 0.)
                        for tank in background.vessel.tanks if tank.adjustable})})
    # The builder supplies an independent empty-plan witness recipe. ``kept``
    # means only that the finisher
    # preserved that declaration, NEVER that it certified physics. The following
    # independent full check is its acceptance evidence and includes all D57
    # states. Search is needed only when the declared recipe actually fails.
    outcome = finish_ballast(background, initial, time_limit_s=time_limit_s, seed=0,
                             **seagoing_keywords)
    timed_not_found = outcome.status == "not_found"
    checked_plan = outcome.plan or initial
    report = _witness_report(check(background, checked_plan, **seagoing_keywords), excluded_parts)
    if any(_unknown_part(part) for part in report.parts.values()):
        return BackgroundAcceptance(False, ("background.checker.unknown",), outcome.status,
            {}, {}, perf_counter()-started, physics_sha256=implementation.get("sha256", ""),
            in_port=in_port, unknown=True)
    arrival = next((part.metrics for name, part in report.parts.items()
        if name.startswith("condition:") and name.endswith(":stability")
        and "condition.phase: arrival" in part.metrics), {})
    C = arrival.get("containership_form_factor", float("nan"))
    initial_ratios = [arrival.get(k, float("nan"))*C/v for k, v in _B23.items()]
    if (not report.passed or not all(math.isfinite(x) for x in initial_ratios)
            or min(initial_ratios) < 1.05):
        outcome = finish_ballast(background, plan, time_limit_s=time_limit_s, seed=0,
                                 **seagoing_keywords)
        timed_not_found |= outcome.status == "not_found"
        checked_plan = outcome.plan or plan
        report = _witness_report(check(background, checked_plan, **seagoing_keywords), excluded_parts)
    reasons = set()
    states = {}
    tanks = dict(background.onboard.tank_masses_kg)
    if outcome.plan is None or outcome.status not in {"ok", "kept"}:
        reasons.add(f"background.finisher.{outcome.status}")
        if outcome.status == "not_found":
            reasons.add("background.finisher." + (getattr(outcome, "not_found_cause", None)
                                                  or "unknown_witness"))
        # Preserve exact failing-rule diagnostics without treating timeout as proof.
    else:
        tanks = dict(departure_condition(background, checked_plan).tank_masses_kg)
    for name, part in report.parts.items():
        reasons.update(f"{name}/{v.rule_id}" for v in part.violations)
        if not part.ran or part.error:
            reasons.add(f"{name}/checker.unavailable")
        if not name.startswith("condition:") or not name.endswith(":stability"):
            continue
        phase = next((p for p in _PHASES if f"condition.phase: {p}" in part.metrics), None)
        if phase is None:
            reasons.add("background.d57.unidentified_state")
            continue
        metrics = dict(part.metrics)
        C = metrics.get("containership_form_factor")
        if C is not None and math.isfinite(C) and C > 0:
            ratios = [metrics.get(k, float("nan"))*C/v for k, v in _B23.items()]
            metrics["b23_ratio"] = min(ratios) if all(math.isfinite(x) for x in ratios) else float("nan")
        states[phase] = metrics
    if set(states) != _PHASES:
        reasons.add("background.d57.missing_states")
    ratio = states.get("arrival", {}).get("b23_ratio", float("nan"))
    if not math.isfinite(ratio) or ratio < 1.05:
        reasons.add("background.arrival_b23_margin")
    if not report.passed and not reasons:
        reasons.add("background.checker.failed")
    result = BackgroundAcceptance(not reasons and report.passed, tuple(sorted(reasons)),
                                  outcome.status, tanks, states, perf_counter()-started,
                                  physics_sha256=implementation.get("sha256", ""), in_port=in_port,
                                  unknown=(outcome.status == "not_found" or any(
                                      _unknown_part(part) for part in report.parts.values())))
    # Search failures and rejections can depend on wall clock/warm state. Cache
    # proven accepts only, and never an evaluation that encountered not_found.
    # Re-key before writing so a table/source changed during checking cannot
    # store the result under its pre-evaluation implementation identity.
    return finalize(result, timed_not_found=timed_not_found)


def _requires_seagoing_background(vessel, *, size: str | None = None) -> bool:
    """Recognize custom selectors even when they retain their parent's ID."""
    from stowage.vessel.fleet_conditions import slice_condition
    # Reconditioning a whole-ship booking also adds a slice audit/parent ID.
    # With no fixed cargo it has no outside-ship background to certify; its
    # export ballast belongs to the actual loaded plan, not an empty ship.
    if size == "full" and not any(load.kind == "cargo" and load.mass_kg > 0
                                   for load in vessel.fixed_loads):
        return False
    return bool(vessel.slice_of or slice_condition(vessel) is not None
                or any(load.kind == "cargo" for load in vessel.fixed_loads))


def requires_background_gate(vessel, *, size: str | None = None) -> bool:
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    return rule_freeze_v1_enabled() or _requires_seagoing_background(vessel, size=size)


def accept_background_candidate(instance: Instance, audit) -> Instance | None:
    """Return the certified arrival copy, retaining booked tanks in the audit.

    Legacy calls return their original instance. Activated producers must emit
    this return value: the public initializer prepares a separate arrival copy
    while the original remains the booked departure context. UNKNOWN raises
    after recording the attempt; it must never trigger candidate redraw.
    """
    if not requires_background_gate(instance.vessel, size=instance.size_class):
        return instance
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    activated = rule_freeze_v1_enabled()
    started = perf_counter()
    original = instance
    preparation_error = None
    if activated:
        try:
            instance = _prepare_arrival_instance(original)
        except (ValueError, TimeoutError) as error:
            preparation_error = str(error)
    if preparation_error is not None:
        result = BackgroundAcceptance(False, ("background.arrival_preparation.unavailable",),
                                      "not_checked", {}, {}, perf_counter()-started,
                                      unknown=_timed_out(preparation_error))
    else:
        result = (evaluate_background(original, arrival_instance=instance) if activated
                  else evaluate_background(instance))
    if activated:
        result = replace(result, elapsed_s=perf_counter()-started)
    audit.background_checks += 1
    audit.background_cache_hits += int(result.cache_hit)
    audit.background_seconds += result.elapsed_s
    adjustable = {tank.id for tank in instance.vessel.tanks if tank.adjustable}
    booking_tanks = dict(original.onboard.tank_masses_kg)
    witness_tanks = dict(result.witness_tank_masses_kg)
    audit.background_last = {"accepted": result.accepted, "finisher_status": result.finisher_status,
        "physics_sha256": result.physics_sha256,
        "states": deepcopy(result.states), "ballast_context": "empty_background_witness",
        "witness_tank_masses_kg": witness_tanks, "booking_tank_masses_kg": booking_tanks,
        "witness_ballast_mass_kg": sum(mass for tid, mass in witness_tanks.items() if tid in adjustable),
        "booking_ballast_mass_kg": sum(mass for tid, mass in booking_tanks.items() if tid in adjustable)}
    if activated:
        audit.background_last.update(status=result.status, in_port=deepcopy(result.in_port),
            arrival_tank_masses_kg=dict(instance.onboard.tank_masses_kg),
            ballast_context=("arrival_and_empty_background_witness" if result.states else "arrival_in_port_only"))
        if preparation_error is not None:
            audit.background_last["arrival_preparation_error"] = preparation_error
    if result.unknown:
        audit.background_last.update(status="unknown", reasons=list(result.reasons))
        if not hasattr(audit, "background_unknowns"):
            audit.background_unknowns = {}
        for reason in result.reasons:
            audit.background_unknowns[reason] = audit.background_unknowns.get(reason, 0)+1
        raise BackgroundAcceptanceUnknown(result)
    if not result.accepted:
        for reason in result.reasons:
            audit.background_rejections[reason] = audit.background_rejections.get(reason, 0)+1
        audit.rejections["background.acceptance"] = audit.rejections.get("background.acceptance", 0)+1
        return None
    # The departure witness is evidence only. Actual emitted arrival fills
    # belong to the prepared copy; the booked context stays independently bound.
    return instance
