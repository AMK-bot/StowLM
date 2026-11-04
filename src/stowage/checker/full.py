"""Full checker: the single verdict on a plan (plan 4.1).

A loading condition means cargo and liquids together. Replay establishes what a
crane can physically accomplish; the independent parts then judge that reached
condition, even after an illegal lift. Hard failures cannot be exchanged for fewer
moves. D11 counts forced later-port restows as well as today's nonproductive lifts
(see ``stowage.moves.engine`` for its fixed-stow projection and Q1 assumption).

Loading-condition coverage (D57): stability, strength, visibility, lashing and
the instruction hook judge departure, mid-voyage and next-call arrival. Every
consumable scales proportionally at r=1, (1+r_arrival)/2 and r_arrival (default
0.10, or the common fraction implied by declared consumption). Cargo and ballast
stay unchanged. Replay and static cargo rules run once. Missing declarations and
voyage ballasting fail closed. Intermediate in-port stability after successful
load moves is checked
only with ``intermediate_stability=True`` in the legacy regime. With the
rule-freeze-v1 switch on, D64 checks in-port arrival and every lift, using the
HEEL transfer and a single ballast changeover k; harbour strength/static stack
criteria use D64's sampled states. Visibility and lashing remain seagoing.

APPROXIMATION: D57 samples three states; the actual continuous-voyage worst
consumable/free-surface state is not searched. This coverage is narrower than
a whole-voyage compliance assessment. IACS UR
S11A distinguishes harbour and seagoing permissible still-water strength limits;
the legacy entry point uses the supplied departure/seagoing envelope; the gated
D64 replay supplies a separate harbour envelope. SOLAS V/22 requires visibility in all conditions of draught, trim and
deck cargo (MSC.99(73), V/22.1). Those obligations also concern in-port and later
leg conditions that stage A does not plan: later-port loads and their ballast
are unknown. Projected future restow lifts are an objective calculation, not a
physics certificate for those future loading conditions. See the rule sources
in ``docs/data-sources.md`` and the limitations in the individual part modules.

Part contract: ``check_<part>(instance, ship, *, call_index) -> PartReport``.
Missing vessel data is a hard ``checker.missing_vessel_data`` failure; unexpected
exceptions become ``ran=False`` and hard ``checker.internal_error``. Regulatory
limits come from the parts and instance, never extra checker tolerances. IACS UR
L5 numerical tolerances are validation tolerances, not regulatory allowances.

The parts own their cached hull geometry. Immutable ballast capacity metadata is
memoised by vessel id, component versions and actual tank data, so edited vessels
with the same id cannot reuse stale capacities. No verdict cache is used here:
different cargo, liquid masses, port water density, rule overrides
and active frozen-interface overrides must always be judged afresh. Instructions
(task 19.2) plug into ``check_plan_constraints``; until a kind has an implementation
it fails closed rather than being silently ignored.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math

from stowage.checker.lashing import check_lashing
from stowage.checker.rules import check_static_rules
from stowage.checker.stability import check_stability
from stowage.checker.strength import check_strength
from stowage.checker.visibility import check_visibility
from stowage.frozen import current_provenance, frozen
from stowage.model import CheckReport, Instance, MovePlan, PartReport, ShipState, Violation
from stowage.model import (VoyageConditionIdentity, VoyageSpec,
                           arrival_remaining_fraction, derive_voyage_liquid_state)
from stowage.moves import IllegalMoveError, apply_move, apply_plan, initial_state
from stowage.versions import component_versions


_BINDING_PREFIXES = ("checker.instance.sha256:", "checker.plan.sha256:")


def _input_bindings(instance: Instance, plan: MovePlan) -> tuple[str, str]:
    """Canonical, complete input identities; artifact IDs alone are mutable."""
    return tuple(prefix + hashlib.sha256(json.dumps(
        value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode("utf-8")).hexdigest()
        for prefix, value in zip(_BINDING_PREFIXES, (instance, plan)))


def report_matches_inputs(report: CheckReport, instance: Instance, plan: MovePlan) -> bool:
    """Whether a serialized checker report is bound to these exact inputs.

    This verifies content linkage, not authenticity or a passing/clean verdict.
    Consumers must still enforce those gates. Legacy reports without hashes
    need a fresh check; same-ID edits to moves, ballast or evidence do not match.
    """
    if plan.instance_id != instance.id or plan.call_index != instance.call_index:
        return False
    try:
        expected = _input_bindings(instance, plan)
        return all({parent for parent in report.provenance.parents if parent.startswith(prefix)} == {binding}
                   for prefix, binding in zip(_BINDING_PREFIXES, expected))
    except (TypeError, ValueError, AttributeError):
        return False


def _problem(instance: Instance, rule: str, message: str, **kwargs) -> Violation:
    return Violation(rule_id=rule, severity="hard", call_index=instance.call_index,
                     message=message, **kwargs)


def _failed(instance: Instance, name: str, error: Exception) -> PartReport:
    message = f"{name}: {type(error).__name__}: {error}"
    return PartReport(name=name, ran=False, error=message,
                      violations=[_problem(instance, "checker.internal_error", message)])


def _run_part(instance: Instance, ship: ShipState, name: str,
              function: Callable[..., PartReport]) -> PartReport:
    """Isolate failures so one broken calculation cannot suppress the other parts."""
    try:
        report = function(instance, ship, call_index=instance.call_index)
        if not isinstance(report, PartReport):
            raise TypeError("checker part did not return PartReport")
        report = report.model_copy(deep=True, update={"name": name})
        # CheckReport serializes parts as PartReport, so subclass-only notes
        # (including D56 lashing evidence) must also use the base JSON contract.
        for note in getattr(report, "notes", ()):
            if isinstance(note, str) and note.strip():
                report.metrics[f"note: {note}"] = 1.
        if not report.ran and not any(v.severity == "hard" for v in report.violations):
            report.violations.append(_problem(instance, "checker.internal_error",
                f"{name} did not run: {report.error or 'no diagnostic supplied'}"))
        return report
    except Exception as error:  # A bad plan must still yield a complete diagnostic report.
        return _failed(instance, name, error)


@lru_cache(maxsize=32)
def _ballast_metadata(vessel_id: str, versions: tuple, payload: tuple) -> tuple:
    """Cache only immutable derived capacities; the full payload guards same-id edits."""
    return tuple((tid, adjustable, volume * density) for tid, adjustable, volume, density in payload)


def _ballast(instance: Instance, plan: MovePlan, ship: ShipState) -> tuple[ShipState, list[Violation]]:
    """Write adjustable tanks only; omitted adjustable tanks in a condition are empty.

    Invalid declarations are diagnosed and left at their reached-state values, so
    the remaining parts can still show useful physics for a realizable condition.
    Fixed tanks always retain their arrival contents (BallastCondition contract).
    """
    result = ship.model_copy(deep=True)
    problems = []
    if plan.ballast is None:
        return result, problems
    declared = plan.ballast.tank_masses_kg
    payload = tuple(sorted((t.id, t.adjustable, t.capacity_m3, t.content_density_kg_m3)
                           for t in instance.vessel.tanks))
    metadata = _ballast_metadata(instance.vessel.id, tuple(sorted(component_versions().items())), payload)
    tanks = {tid for tid, _, _ in metadata}
    for tid in sorted(set(declared) - set(tanks)):
        problems.append(_problem(instance, "checker.invalid_ballast", f"Unknown ballast tank {tid}."))
    for tid, adjustable, capacity in metadata:
        if not adjustable:
            if tid in declared:
                problems.append(_problem(instance, "checker.invalid_ballast",
                    f"Ballast may not set fixed tank {tid}."))
            continue
        mass = declared.get(tid, 0.0)
        if not isinstance(mass, (int, float)) or not math.isfinite(mass) or not 0 <= mass <= capacity:
            problems.append(_problem(instance, "checker.invalid_ballast",
                f"Ballast mass for {tid} must be finite and within its capacity ({capacity} kg)."))
            continue
        result.tank_masses_kg[tid] = float(mass)
    return result, problems


def check_plan_constraints(instance: Instance, ship: ShipState, *, call_index: int) -> PartReport:
    """Task 19.2 integration hook; unknown instruction semantics cannot pass.

    This function's signature is intentionally not a new frozen interface. The
    instructions owner can supply the evaluator once its kind/params schema is
    defined; callers still use the full ``check`` verdict.
    """
    violations = []
    for constraint in instance.plan_constraints:
        kind = constraint.kind
        # Unsupported kinds are data in the diagnostic, never new catalogue IDs.
        violations.append(Violation(rule_id="checker.missing_vessel_data", severity=constraint.hardness,
            call_index=call_index, message=f"No checker implementation for instruction kind {kind!r}."))
    if violations:
        violations.append(_problem(instance, "checker.missing_vessel_data",
            "Plan constraints cannot be evaluated until their instruction kinds are implemented."))
    return PartReport(name="constraints", violations=violations, ran=not violations,
                      error="Unsupported instruction kinds." if violations else None)


def _in_port(instance: Instance, plan: MovePlan, ship: ShipState | None = None, *,
             workers: int | None = None, warm_start: bool = False,
             telemetry: dict | None = None) -> PartReport:
    """D64 production replay; the activation decision belongs to _check."""
    from stowage.checker.in_port import replay
    return replay(instance, plan, ship, workers=workers, warm_start=warm_start,
                  telemetry=telemetry)


def _intermediate(instance: Instance, plan: MovePlan) -> list[tuple[int, PartReport]]:
    """Check stability after each successful load, before departure ballast transfer."""
    state = initial_state(instance)
    reports = []
    for index, move in enumerate(plan.moves):
        try:
            state = apply_move(instance, state, move, move_index=index)
        except IllegalMoveError:
            # Full replay owns the legality diagnostic and stops at this lift too.
            break
        if move.kind == "load":
            report = _run_part(instance, state.ship, "stability", check_stability)
            report.violations = [v.model_copy(update={"move_index": index}) for v in report.violations]
            reports.append((index, report))
    return reports


_D57_NOTE = ("note: APPROXIMATION: D57 samples departure, mid-voyage and arrival; "
             "the true worst continuous-voyage consumable/free-surface state is not searched; "
             "voyage ballast operations are forbidden")


@dataclass(frozen=True)
class _ReportCondition:
    """Derived report identity; no new input phase or free-form liquid state."""

    id: str
    phase: str
    call_index: int
    locode: str
    environment_id: str
    density_id: str
    water_density_kg_m3: float
    remaining_fraction: float | None


def _report_identity(identity: VoyageConditionIdentity, fraction: float | None,
                     *, midpoint: bool = False) -> _ReportCondition:
    return _ReportCondition(
        id=f"{identity.id}:mid_voyage" if midpoint else identity.id,
        phase="mid_voyage" if midpoint else identity.phase,
        call_index=identity.call_index, locode=identity.locode,
        environment_id=identity.environment_id, density_id=identity.density_id,
        water_density_kg_m3=identity.water_density_kg_m3,
        remaining_fraction=fraction)


def _condition_functions():
    # Resolve globals on each check, preserving frozen overrides and future
    # condition-dependent instructions. Cargo/DG rules and move replay are static.
    return (("stability", check_stability), ("strength", check_strength),
            ("visibility", check_visibility), ("lashing", check_lashing),
            ("constraints", check_plan_constraints))


def _condition_report(report: PartReport, identity: _ReportCondition) -> PartReport:
    """Retain identity as JSON-safe metric labels without changing PartReport."""
    result = report.model_copy(deep=True)
    label = f"[{identity.id} {identity.phase}]"
    result.name = f"{report.name} {label}"
    result.violations = [v.model_copy(update={"message": f"{label} {v.message}",
                                           "call_index": identity.call_index})
                         for v in result.violations]
    if result.error:
        result.error = f"{label} {result.error}"
    result.metrics.update({f"condition.id: {identity.id}": 1.,
        f"condition.phase: {identity.phase}": 1.,
        f"condition.environment_id: {identity.environment_id}": 1.,
        f"condition.density_id: {identity.density_id}": 1.,
        f"condition.locode: {identity.locode}": 1.,
        "condition.call_index": float(identity.call_index),
        "condition.water_density_kg_m3": identity.water_density_kg_m3,
        _D57_NOTE: 1.})
    if identity.remaining_fraction is not None:
        result.metrics["condition.remaining_fraction"] = identity.remaining_fraction
    if identity.phase == "mid_voyage":
        result.metrics["note: APPROXIMATION: mid-voyage uses constant declared departure environment and density context; current-port berth limits do not apply"] = 1.
    return result


def _merge_condition(parts: dict[str, PartReport], name: str,
                     report: PartReport, identity: _ReportCondition) -> None:
    """Keep individual evidence and the legacy aggregate shipping/UI part."""
    key = f"condition:{identity.id}:{name}"
    condition = _condition_report(report, identity)
    parts[key] = condition
    if identity.phase == "departure":
        parts[name] = condition.model_copy(deep=True, update={"name": name})
    else:
        aggregate = parts[name]
        aggregate.violations.extend(condition.violations)
        aggregate.ran = aggregate.ran and condition.ran
        if condition.error:
            aggregate.error = "; ".join(filter(None, (aggregate.error, condition.error)))
        # Legacy scalar metrics describe departure. Legacy constraint margins
        # describe the worst sample, and qualified keys preserve every value.
        for metric, value in condition.margins.items():
            aggregate.margins[metric] = min(aggregate.margins.get(metric, value), value)
    parts[name].metrics.update({f"condition:{identity.id}.{k}": v for k, v in condition.metrics.items()})
    parts[name].margins.update({f"condition:{identity.id}.{k}": v for k, v in condition.margins.items()})


def _voyage(instance: Instance, ship: ShipState, parts: dict[str, PartReport]):
    """Evaluate D57's three proportional samples of the reached departure."""
    parts["voyage"] = PartReport(name="voyage", metrics={_D57_NOTE: 1.})
    leg = None
    samples = []

    def failed(error, identity=None):
        rule = "checker.missing_vessel_data" if isinstance(error, (ValueError, TypeError, AttributeError, KeyError)) else "checker.internal_error"
        label = f"[{identity.id} {identity.phase}] " if identity else ""
        message = f"{label}D57 voyage: {type(error).__name__}: {error}"
        parts["voyage"].ran = False
        parts["voyage"].error = "; ".join(filter(None, (parts["voyage"].error, message)))
        parts["voyage"].violations.append(_problem(instance, rule, message))
        if identity is not None:
            for name, _ in _condition_functions():
                failure = PartReport(name=name, ran=False, error=str(error),
                    violations=[_problem(instance, rule, f"Cannot evaluate {name}: {error}")])
                _merge_condition(parts, name, failure, identity)

    try:
        if instance.voyage is None:
            raise ValueError("D57 voyage declaration is missing; departure/arrival identities are required.")
        raw = instance.voyage
        leg = VoyageSpec.model_validate(raw.model_dump() if isinstance(raw, VoyageSpec) else raw)
        departure, arrival = leg.departure, leg.arrival
        calls = instance.rotation.calls
        if (departure.call_index != instance.call_index or departure.locode != instance.port.locode
                or not 0 <= departure.call_index < len(calls)
                or calls[departure.call_index].locode != departure.locode):
            raise ValueError("voyage.departure call/LOCODE does not match the checked instance")
        if arrival.call_index >= len(calls) or calls[arrival.call_index].locode != arrival.locode:
            raise ValueError("voyage.arrival must identify the immediate next call in rotation")
        if not math.isclose(departure.water_density_kg_m3, instance.port.water_density_kg_m3,
                            rel_tol=0., abs_tol=1e-9):
            raise ValueError("voyage.departure density does not match the checked port density")
        if arrival.id == f"{departure.id}:mid_voyage":
            raise ValueError("voyage midpoint condition ID collides with arrival.id")
        r_arrival = arrival_remaining_fraction(ship, leg, vessel=instance.vessel)
        identities = (_report_identity(departure, 1.),
                      _report_identity(departure, (1.+r_arrival)/2, midpoint=True),
                      _report_identity(arrival, r_arrival))
        # IACS UR S11A 2.2.2, data/raw/iacs/ur-s11a.txt, PDF p.8:
        # "intermediate stage of the voyage ... more severe". IS Code
        # B3.4.1.2-.3, PDF p.41: "with 10 % stores and fuel remaining".
        # APPROXIMATION: D57 samples the proportional path, not its true extrema.
        parts["voyage"].metrics["note: next-port berth/channel limits are unavailable; voyage samples use vessel seagoing limits"] = 1.
    except Exception as error:
        # Retain reached-departure diagnostics even when voyage evidence fails.
        for name, function in _condition_functions():
            parts[name] = _run_part(instance, ship, name, function)
        failed(error)
        if leg is not None:
            failed(error, _report_identity(leg.departure, None, midpoint=True))
            failed(error, _report_identity(leg.arrival, None))
        return samples
    for identity in identities:
        try:
            sample = derive_voyage_liquid_state(ship, leg, identity.remaining_fraction,
                                                 vessel=instance.vessel)
            state = ship.model_copy(deep=True, update={"tank_masses_kg": dict(sample.tank_masses_kg)})
            port = instance.port if identity.phase == "departure" else instance.port.model_copy(update={
                "locode": identity.locode, "water_density_kg_m3": identity.water_density_kg_m3,
                "max_draught_m": None, "port_rules": []})
            # APPROXIMATION: no intermediate environmental declaration exists;
            # midpoint holds the declared departure environment/density fixed.
            context = instance.model_copy(update={"call_index": identity.call_index,
                                                   "port": port, "onboard": state})
            for name, function in _condition_functions():
                _merge_condition(parts, name, _run_part(context, state, name, function), identity)
            samples.append(sample)
        except Exception as error:
            failed(error, identity)
    return samples


@frozen("checker.check", owner="4.1", implemented=True)
def check(instance: Instance, plan: MovePlan, *, intermediate_stability: bool = False) -> CheckReport:
    """Judge every condition with a private numerical reuse workspace."""
    from stowage.checker.perf import evaluation_cache

    with evaluation_cache():
        return _check(instance, plan, intermediate_stability=intermediate_stability)


def check_reference(instance: Instance, plan: MovePlan, *, intermediate_stability: bool = False) -> CheckReport:
    """Run live checker code with six optional numerical caches bypassed.

    The bypass covers interpolation stencils, base hull submerged integration,
    strength companion roots, visibility cone intersections, strength assessment
    cuts and slot syntax. It retains public frozen overrides and shares the
    canonical projection/baseline cache and parsed-parameter LRU with ``check``.
    It is not a preserved earlier checker or an independent implementation of
    canonical policy resolution. The projection's independent evidence is the
    public-builder comparison in ``test_perf_canonical_policy_1028.py``.
    """
    from stowage.checker.perf import without_reuse

    with without_reuse():
        return check(instance, plan, intermediate_stability=intermediate_stability)


def _check(instance: Instance, plan: MovePlan, *, intermediate_stability: bool = False) -> CheckReport:
    """Judge a plan: replay → shut-out → ballast → all parts → instruction hook.

    ``passed`` means no hard violations and every part ran. An illegal lift stops
    replay; physics still judges the state reached. With ``ballast=None`` judge
    arrival tank contents, as specified by the original frozen contract. Optional
    intermediate stability checks each successfully loaded condition using the
    tanks at arrival, since the move schema has no ballast-transfer operations.
    D57 requires every condition-dependent part at departure, midpoint and
    arrival. Proportional consumable samples preserve cargo and ballast; static
    cargo rules run once. Numerical parts dominate the cost.
    """
    parts: dict[str, PartReport] = {}
    ship = instance.onboard.model_copy(deep=True)
    count = None
    try:
        replay = apply_plan(instance, plan)
        ship = replay.final_state.ship
        count = replay.count
        violations = [v.model_copy(update={"severity": "hard"}) for v in replay.violations]
        if not replay.legal and not violations:
            violations.append(_problem(instance, "checker.internal_error", "Move replay rejected the plan without a diagnostic."))
        parts["moves"] = PartReport(name="moves", violations=violations)
    except ValueError as error:
        # The move engine reserves ValueError for instance/call mismatch.
        parts["moves"] = PartReport(name="moves", ran=False, error=str(error),
            violations=[_problem(instance, "expert.moves.top_access", str(error))])
    except Exception as error:
        parts["moves"] = _failed(instance, "moves", error)
    shut_out = sorted(set(instance.load_list) - {p.container_id for p in ship.placements})
    if shut_out:
        parts["moves"].violations.append(_problem(instance, "objective.all_loaded",
            "Every load-list box must be aboard at departure.", containers=shut_out))
    try:
        ship, problems = _ballast(instance, plan, ship)
        parts["moves"].violations.extend(problems)
    except Exception as error:
        failure = _failed(instance, "ballast", error)
        parts["moves"].violations.extend(failure.violations)
        parts["moves"].ran = False
        parts["moves"].error = failure.error
    parts["rules"] = _run_part(instance, ship, "rules", check_static_rules)
    voyage_samples = _voyage(instance, ship, parts)
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    if rule_freeze_v1_enabled():
        try:
            parts["in_port"] = _in_port(instance, plan, ship)
        except Exception as error:
            parts["in_port"] = _failed(instance, "in_port", error)
    elif intermediate_stability:
        try:
            for index, report in _intermediate(instance, plan):
                departure = parts["stability"]
                departure.violations.extend(report.violations)
                departure.metrics.update({f"intermediate_{index}.{key}": value for key, value in report.metrics.items()})
                departure.margins.update({f"intermediate_{index}.{key}": value for key, value in report.margins.items()})
                if not report.ran:
                    departure.ran = False
                    departure.error = report.error
        except Exception as error:
            failure = _failed(instance, "stability", error)
            parts["stability"].ran = False
            parts["stability"].error = failure.error
            parts["stability"].violations.extend(failure.violations)
    provenance = current_provenance(created_by="checker.check", seed=instance.seed).merge_nested(
        instance, plan, voyage_samples, parent_ids=[instance.id, plan.id])
    # These namespaces identify this check's direct inputs. An input artifact
    # may inherit older report hashes; they must not masquerade as this binding.
    provenance.parents = [parent for parent in provenance.parents
                          if not parent.startswith(_BINDING_PREFIXES)]
    try:
        provenance.parents = sorted(set(provenance.parents) | set(_input_bindings(instance, plan)))
    except Exception as error:
        # Mutable inputs may bypass the model's JSON contract. An unbound
        # verdict must fail closed while retaining the physics diagnostics.
        failure = _failed(instance, "input binding", error)
        parts["constraints"].ran = False
        parts["constraints"].error = failure.error
        parts["constraints"].violations.extend(failure.violations)
    # Individual condition reports are also represented in the legacy aggregate
    # parts; avoid emitting every condition violation twice at the top level.
    violations = sorted((v for key, part in parts.items() if not key.startswith("condition:")
                         for v in part.violations),
                        key=lambda v: (v.call_index, -1 if v.move_index is None else v.move_index, v.rule_id))
    report_class = CheckReport
    if hasattr(parts.get("in_port"), "ballast_programme"):
        from stowage.checker.in_port_report import InPortCheckReport
        report_class = InPortCheckReport
    return report_class(passed=all(p.ran for p in parts.values()) and not any(v.severity == "hard" for v in violations),
        violations=violations, parts=parts, count=count, shut_out=shut_out,
        provenance=provenance)


@frozen("checker.departure_condition", owner="4.1", implemented=True)
def departure_condition(instance: Instance, plan: MovePlan) -> ShipState:
    """Return replayed ship with declared adjustable ballast; illegal plans raise ValueError.

    This constructs a loading condition, not a safety verdict: callers must still
    use ``check`` before shipping any plan.
    """
    replay = apply_plan(instance, plan)
    if not replay.legal:
        raise ValueError("Illegal plan: " + "; ".join(f"{v.rule_id}: {v.message}" for v in replay.violations))
    ship, problems = _ballast(instance, plan, replay.final_state.ship)
    if problems:
        raise ValueError("Invalid ballast: " + "; ".join(v.message for v in problems))
    return ship
