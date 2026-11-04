"""Prepare playback using the authoritative crane-move engine.

One lift is one move; a buried target requires moving the boxes above it first
(the block relocation problem; Tanaka & Voss 2019,
doi:10.1016/j.ejor.2019.06.014). The placeholder below is a deterministic
demonstration, never an optimising planner or a checker-approved departure plan.
The browser receives complete states and does not reproduce physical rules.
"""
from __future__ import annotations

from pathlib import Path
import tomllib
from typing import Any

from stowage.conventions import cells_of_slot, format_slot, parse_slot
from stowage.io import load_instance
from stowage.checker import report_matches_inputs
from stowage.model import CheckReport, Instance, MovePlan, PlannerResult
from stowage.model.moves import HatchCoverOff, HatchCoverOn, Load
from stowage.moves import apply_move, apply_plan, initial_state, IllegalMoveError
from stowage.yard.dig_out import NearestStackDigOut, YardCapacityError
from stowage.conventions import canonical_vessel_id

from app import check_cache
from app.checker import build_checker_payload
from app.validation import report_validation

ROOT = Path(__file__).resolve().parents[1]


SMOKE_TINY = ROOT / "experiments/mvp/smoke/run-20261029/tiny-feeder_500-0"
DEV_RUNS = ROOT / "experiments/mvp/ui/dev-runs"


def _instance_files(pattern: str) -> list[Path]:
    return [p for p in sorted(ROOT.glob(pattern))
            if p.name.endswith(".json") and not p.name.endswith((".audit.json", ".selection.json", ".plan.json"))
            and "manifest" not in p.name]


def _plan_for(instance_path: Path) -> Path | None:
    """A saved plan for this exact instance id: a sibling ``.plan.json`` or a dev-run result (planner output).

    Only the id is matched here; ``app.inputs.load_inputs`` then enforces full content binding and the
    fresh checker run owns the verdict.
    """
    import json
    sibling = instance_path.with_name(instance_path.name.removesuffix(".json") + ".plan.json")
    if sibling.is_file():
        return sibling
    if not DEV_RUNS.is_dir():
        return None
    try:
        instance_id = json.loads(instance_path.read_text()).get("id")
    except (OSError, ValueError):
        return None
    for path in sorted(DEV_RUNS.rglob("*.json")):
        try:
            if path.stat().st_size > 32 * 1024 * 1024:
                continue
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        value = data.get("result") or data.get("planner_result") or data
        if not isinstance(value, dict):
            continue
        plan = value.get("plan") if "planner" in value else value
        # Planner results must report status ok (a timeout's partial plan is not a demo default).
        if (isinstance(plan, dict) and plan.get("instance_id") == instance_id and plan.get("moves")
                and value.get("planner") != "baseline.twostep" and value.get("status", "ok") == "ok"):
            return path
    return None


def default_inputs() -> tuple[Path | None, Path | None]:
    """Choose the no-argument demo world, preferring current-parameter data that the checker can assess.

    Old corpora (instances-v2/v3/sim) are stale against current fleet parameters, so the physics parts fail.
    Order (29 Oct, 6.5 resume): the passing smoke tiny run (instance + plan; the only current-parameter plan known
    to pass the full checker), a showcase instance with a saved plan, a current test/small instance with a saved
    plan, then a showcase / test-small instance with the placeholder plan, then the
    historical v2 default. A placeholder on a showcase instance fails the full checker (measured 29 Oct: 36 s,
    rejected), so instances with a real plan come first.
    """
    if (SMOKE_TINY / "instance.json").is_file() and (SMOKE_TINY / "plan.json").is_file():
        return SMOKE_TINY / "instance.json", SMOKE_TINY / "plan.json"
    groups = [_instance_files("experiments/mvp/corpus/showcase/*.json"),
              _instance_files("experiments/mvp/corpus/test/small/**/*.json")]
    for group in groups:
        for path in group:
            plan = _plan_for(path)
            if plan is not None:
                return path, plan
    for group in groups:
        if group:
            return group[0], None
    return None, None


def demo_instance() -> Instance:
    """The default world (see ``default_inputs``), with the historical v2 fallback and generation."""
    preferred, _ = default_inputs()
    if preferred is not None:
        return load_instance(preferred)
    path = ROOT / "experiments/instances-v2/random_yard/small/seed-00100001.json"
    if not path.exists():
        candidates = sorted((ROOT / "experiments/instances-v2").glob("**/small/*.json"))
        path = next((p for p in candidates if not p.name.endswith(".audit.json")), path)
    if path.exists():
        return load_instance(path)
    from stowage.generate.random_yard import generate_random_instance
    return generate_random_instance(size="small", seed=1)


def make_demo_plan(instance: Instance) -> MovePlan:
    """Fixed-ID first-fit loading, with engine-gated deterministic yard dig-out.

    APPROXIMATION: greedy first-fit is a playback placeholder, with no lookahead,
    ballast finishing, cargo-rule certification or optimality claim. Failed
    retrieval/placement attempts are rolled back; shut-outs remain explicit.
    """
    if instance.discharge_list:
        raise ValueError("Placeholder supports stage-A loads only; supply a MovePlan for discharge calls")
    state = initial_state(instance)
    moves = []
    cells = {cell.id: cell for cell in instance.vessel.cells}
    slots = set(cells)
    for cell in cells.values():
        addr = parse_slot(cell.id)
        partner = format_slot(addr.bay + 2, addr.row, addr.tier)
        if partner in cells:
            slots.add(format_slot(addr.bay + 1, addr.row, addr.tier))
    stacks = {stack.id: stack for stack in instance.vessel.stacks}
    boxes = {box.id: box for box in instance.containers}
    # Short boxes first leaves long boxes able to span supported cell pairs;
    # the engine rejects the converse (a short box atop a long box).
    targets = sorted(instance.load_list, key=lambda cid: (boxes[cid].length_ft, cid))
    for cid in targets:
        try:
            retrieval = NearestStackDigOut().retrieve(instance, state, cid)
        except YardCapacityError:
            continue
        for slot in sorted(slots, key=lambda s: (parse_slot(s).tier >= 80, parse_slot(s).tier, s)):
            if (boxes[cid].length_ft == 20) != (parse_slot(slot).bay % 2 == 1):
                continue
            trial = retrieval.state
            extra = []
            cover_ids = sorted({stacks[cells[c].stack_id].hatch_cover_id
                                for c in cells_of_slot(slot)
                                if stacks[cells[c].stack_id].hatch_cover_id is not None})
            try:
                for cover_id in cover_ids:
                    if parse_slot(slot).tier < 80 and cover_id not in trial.ship.covers_off:
                        cover = HatchCoverOff(call_index=instance.call_index, cover_id=cover_id)
                    elif parse_slot(slot).tier >= 80 and cover_id in trial.ship.covers_off:
                        cover = HatchCoverOn(call_index=instance.call_index, cover_id=cover_id)
                    else:
                        continue
                    trial = apply_move(instance, trial, cover)
                    extra.append(cover)
                move = Load(call_index=instance.call_index, container_id=cid, src=retrieval.source, dst=slot)
                trial = apply_move(instance, trial, move)
            except IllegalMoveError:
                continue
            state = trial
            moves.extend([*retrieval.moves, *extra, move])
            break
    for cover_id in sorted(state.ship.covers_off):
        move = HatchCoverOn(call_index=instance.call_index, cover_id=cover_id)
        state = apply_move(instance, state, move)
        moves.append(move)
    return MovePlan(id=f"{instance.id}.ui-placeholder", instance_id=instance.id,
                    call_index=instance.call_index, moves=moves, provenance=instance.provenance)


def vessel_display_label(instance: Instance) -> str:
    """Display name of the instance's vessel (header label); shared by the full and the lite replay."""
    vessel_label = instance.vessel.name or instance.vessel.id
    root_id = canonical_vessel_id(instance.vessel.slice_of or instance.vessel.id).split('.', 1)[0]
    # Known fleet display names come from their current parameter record; this
    # changes no imported vessel geometry, identity or checker input.
    if root_id in {'feeder_500', 'feeder_800', 'feeder_1700'}:
        parameter_path = ROOT / 'params' / 'fleet' / f'{root_id}.toml'
        if parameter_path.is_file():
            vessel_label = tomllib.loads(parameter_path.read_text())['name']
            if instance.vessel.slice_of:
                vessel_label += ' (slice)'
    if not vessel_label.lower().startswith('synthetic '):
        vessel_label = 'Synthetic ' + vessel_label
    return vessel_label


def require_plan_binding(instance: Instance, plan: MovePlan, planner_result: PlannerResult | None) -> None:
    """The input binding every replay path (regular and lite) enforces before replaying."""
    if plan.instance_id != instance.id or plan.call_index != instance.call_index:
        raise ValueError("plan instance_id and call_index must match the instance")
    if planner_result is not None and planner_result.plan != plan:
        raise ValueError("PlannerResult plan must match the replayed plan")


def certify_replay(instance: Instance, plan: MovePlan, planner_result: PlannerResult | None, checker: dict,
                   executed: int) -> tuple[dict, PlannerResult, dict[str, Any]]:
    """The single approval decision for a replay, shared by ``build_replay`` and the lite replay.

    Takes the checker payload and returns (payload after binding/validation, the candidate carrying fresh evidence,
    the approval metadata fields). Fresh authoritative evidence only: imported metrics/reports never certify a
    departure. Reuses the canonical shipping contract, including all seven parts, replay counts, required calls,
    status and recursive provenance (``PlannerResult.shippable``).
    """
    placeholder = plan.id.endswith(".ui-placeholder")
    try:
        report = CheckReport.model_validate(checker.get("report"))
    except (ValueError, TypeError):
        report = None
    bound = report is not None and report_matches_inputs(report, instance, plan)
    # A reason set by the payload (stale loaded code, lite checker not run) is more exact than "unbound".
    unavailable = checker.get("unavailable_reason")
    if not bound:
        from app.checker import unbound_payload
        checker = unbound_payload(executed, unavailable)
    candidate = planner_result or PlannerResult(status="ok", plan=plan,
        planner="ui-placeholder" if placeholder else "supplied-plan", planner_version="ui-v1")
    from stowage.frozen import current_provenance
    provenance = candidate.provenance.merge_nested(instance, plan, candidate, report,
        current_provenance(created_by="app.build_replay"), parent_ids=[instance.id, plan.id])
    candidate = candidate.model_copy(update={"provenance": provenance})
    if bound:
        candidate._record_full_check(instance, report)
    validation = report_validation(report, instance=instance, plan=plan, planner_result=candidate)
    if not bound:
        validation = {'validated': False, 'status': 'NOT VALIDATED', 'reasons': [unavailable or 'Unbound report']}
    checker['validation_status'] = validation['status']
    checker['validation_reasons'] = validation['reasons']
    checker['passed'] = bool(bound and report.passed and validation['validated'])
    shippable = bound and candidate.shippable and validation['validated']
    blockers = []
    if not validation['validated']:
        blockers.extend('NOT VALIDATED: ' + reason for reason in validation['reasons'])
    if not bound:
        blockers.append(unavailable or "Unbound report: checker evidence does not match the displayed instance and plan")
    elif not report.passed:
        blockers.append("Full checker rejected this plan")
    if provenance.stubs:
        blockers.append("Stub taint: " + ", ".join(sorted(provenance.stubs)))
    if candidate.status != "ok":
        blockers.append(f"Planner status is {candidate.status}")
    if candidate.later_plans or instance.plan_calls:
        blockers.append("Later calls lack sequential full-checker coverage")
    if not shippable and not blockers:
        blockers.append("Full clean shipping evidence is incomplete")
    notice = ("Full checker passed; clean shipping evidence approves this departure." if shippable else
              "Departure not approved. " + "; ".join(blockers) + ".")
    repaired = candidate.repaired
    optimality = candidate.metrics.get('optimality_status')
    planner_label = ('Exact (not proven optimal)' if candidate.planner == 'solver.exact'
                     and optimality != 'OPTIMAL' else 'Exact (proven optimal)'
                     if candidate.planner == 'solver.exact' else None)
    return checker, candidate, {
        "repaired": repaired, "repairing_solver": 'solver.scalable' if repaired else None,
        "repair_note": candidate.repair_note,
        "repair_status": candidate.metrics.get('repair_status'),
        "fallback_needed": candidate.metrics.get('fallback_needed', repaired),
        "optimality_status": optimality, "planner_label": planner_label,
        "validation_status": validation["status"], "validation_reasons": validation["reasons"],
        "checker_status": ((unavailable or "unbound report") if not bound else
                           "NOT VALIDATED" if not validation["validated"] else
                           "passed" if checker["passed"] else "failed"),
        "departure_approved": shippable, "shippable": shippable,
        "provenance": provenance.model_dump(mode="json"),
        "stub_tainted": bool(provenance.stubs), "shipping_blockers": blockers,
        "notice": notice}


def withdraw_approval(replay: dict, reason: str) -> dict:
    """Strip checker evidence and approval from a memoised replay in place: its epoch is no longer current.

    Frames and counts stay for inspection; the replay then reads exactly like one with no checker evidence.
    """
    from app.checker import unbound_payload
    replay["checker"] = unbound_payload((replay.get("checker") or {}).get("evaluated_frame"), reason)
    replay.setdefault("metadata", {}).update(
        shippable=False, departure_approved=False, checker_status=reason, validation_status="NOT VALIDATED",
        validation_reasons=[reason], shipping_blockers=[reason], notice=f"Departure not approved. {reason}.")
    return replay


def build_replay(instance: Instance, plan: MovePlan | None, *,
                 planner_result: PlannerResult | None = None) -> dict[str, Any]:
    """Materialise reached states, full checker diagnostics and canonical counts.

    Frame zero is the input snapshot; frame n is after n lifts. ``move`` is the
    lift just completed. Highlight targets the next lift and remaining blockers
    above its next load target. Projection is recalculated by the move engine.
    ``metadata`` leaves room for checker panels and interchangeable planners.
    """
    if plan is None:
        if planner_result is None or planner_result.plan is not None or planner_result.status == 'ok':
            raise ValueError('No plan available: a failed model PlannerResult is required')
        from app.checker import unbound_payload
        provenance = planner_result.provenance.merge_nested(instance, planner_result)
        reason = f'No plan produced (planner status: {planner_result.status})'
        return {'instance': instance.model_dump(mode='json'), 'plan': None, 'frames': [],
            'planner_result': planner_result.model_dump(mode='json'), 'checker': unbound_payload(None),
            'metadata': {'label': 'Imported failed planner result', 'planner': planner_result.planner,
                'vessel_label': vessel_display_label(instance),
                'repaired': planner_result.repaired,
                'repairing_solver': 'solver.scalable' if planner_result.repaired else None,
                'repair_note': planner_result.repair_note, 'repair_status': planner_result.metrics.get('repair_status'),
                'fallback_needed': planner_result.metrics.get('fallback_needed', planner_result.repaired),
                'shippable': False, 'departure_approved': False, 'physical_replay_legal': False,
                'checker_status': 'unavailable: no plan', 'validation_status': 'NOT VALIDATED',
                'validation_reasons': [reason], 'shipping_blockers': [reason],
                'provenance': provenance.model_dump(mode='json'), 'stub_tainted': bool(provenance.stubs),
                'planned_moves': None, 'executed_moves': 0, 'notice': reason + '. ' + (planner_result.message or '')}}
    require_plan_binding(instance, plan, planner_result)
    result = apply_plan(instance, plan)
    state = initial_state(instance)
    frames = []
    for index in range(result.executed + 1):
        move = plan.moves[index - 1] if index else None
        if move is not None:
            state = apply_move(instance, state, move, move_index=index - 1)
        prefix = plan.model_copy(update={"moves": plan.moves[:index]})
        count = apply_plan(instance, prefix).count
        counts = count.model_dump(mode="json")
        counts["total_lifts_now"] = count.total_lifts_now
        upcoming = plan.moves[index] if index < len(plan.moves) else None
        target = next((m.container_id for m in plan.moves[index:] if m.kind == "load"), None)
        blockers = []
        for stack in state.yard.stacks:
            if target in stack.boxes:
                blockers = stack.boxes[stack.boxes.index(target) + 1:]
        frames.append({"state": state.model_dump(mode="json"),
                       "move": move.model_dump(mode="json") if move else None,
                       "counts": counts,
                       "highlight": {"container_id": getattr(upcoming, "container_id", None),
                                     "blockers": blockers, "target_id": target}})
    loaded = {p.container_id for p in state.ship.placements}
    shut_out = sorted(set(instance.load_list) - loaded)
    placeholder = plan.id.endswith(".ui-placeholder")
    checker = build_checker_payload(instance, plan, evaluated_frame=result.executed)
    checker, candidate, shipping = certify_replay(instance, plan, planner_result, checker, result.executed)
    checker['check_source'] = check_cache.last_source()  # "fresh" or "memo": what a "rechecked" label may say about this run
    return {"checker": checker, "instance": instance.model_dump(mode="json"), "plan": plan.model_dump(mode="json"),
            "planner_result": candidate.model_dump(mode="json"),
            "frames": frames,
            "metadata": {"vessel_label": vessel_display_label(instance), **shipping,
                         "planner": "ui-placeholder" if placeholder else "supplied-plan",
                         "label": "Placeholder · deterministic first-fit" if placeholder else "Supplied MovePlan",
                         "physical_replay_legal": result.legal,
                         "shut_out": shut_out,
                         "planned_moves": len(plan.moves), "executed_moves": result.executed}}
