"""Result types returned by the frozen interfaces (plan 0.9). JSON-serialisable so eval rows and the UI can store
them. Numeric-heavy internals (linear model matrices) stay in their modules as dataclasses."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, PrivateAttr, model_serializer

from stowage.model.base import CallIndex, ContainerId, StowModel
from stowage.model.plan import MovePlan
from stowage.model.provenance import Provenance
from stowage.model.ship import WorldState
from stowage.model.violation import Violation


class FutureMoves(StowModel):
    """Lifts at one later call forced by the departure stow (``stowage.moves.project_future_moves``)."""

    call_index: CallIndex
    restowed_boxes: int = 0  # boxes that must come off and back on to reach that call's discharges
    restow_lifts: int = 0  # restowed_boxes × ObjectiveWeights.future_restow_lifts_per_box
    hatch_cover_lifts: int = 0  # cover off + on = 2 per cover opened


class MoveCount(StowModel):
    """Lifts by type for one plan (+ projection). ``weighted_nonproductive`` is the D11 level-3 objective value.

    weighted_nonproductive = w.relocate·relocations + w.restow·restow_lifts + w.hatch_cover·hatch_cover_lifts
                           + w.future_restow·Σ future.restow_lifts + w.future_hatch_cover·Σ future.hatch_cover_lifts
    """

    loads: int = 0
    discharges: int = 0
    relocations: int = 0  # yard rehandles
    restow_lifts: int = 0  # restow_shift + restow_off + restow_on at this call
    hatch_cover_lifts: int = 0  # hatch_cover_off + hatch_cover_on at this call
    gate_moves: int = 0  # simulator only
    future: list[FutureMoves] = Field(default_factory=list)
    weighted_nonproductive: float = 0.0

    @property
    def total_lifts_now(self) -> int:
        return self.loads + self.discharges + self.relocations + self.restow_lifts + self.hatch_cover_lifts


class ApplyResult(StowModel):
    """Outcome of replaying a plan through the move engine.

    legal: every move was physically possible (and the end state is a valid departure state: no box left in the quay
        buffer, all covers on). ``violations`` then lists nothing from the ``moves.*`` family.
    executed: number of moves applied before stopping (= len(moves) when legal or ``stop_at_first=False``).
    final_state: state after the executed moves (the departure state when legal; ballast not applied).
    count: lifts by type over the executed moves, plus the future projection from the final ship state.
    """

    legal: bool
    executed: int
    final_state: WorldState
    count: MoveCount
    violations: list[Violation] = Field(default_factory=list)


class BallastProgramme(StowModel):
    """D74's named in-port programme evidence at parts.in_port.ballast_programme."""

    changeover_k: int
    lifts_after_k: int
    pumped_mass_t: float
    abs_change_t: float
    pump_time_required_h: float
    pump_time_available_h: float
    ballast_delay_h: float = 0.0
    pump_window_lifts: int
    ballast_pump_m3_h: float
    t_lift_s: float
    cranes: int
    gross_moves_per_hour: float
    heel_step_limit_t: float
    heel_max_step_t: float | None = None
    heel_max_ideal_step_t: float
    heel_path_feasible: bool | None = None
    heel_path_linearised_miss: bool = False
    rate_check_rule: str = 'port.ballast.transfer_rate'


class PartReport(StowModel):
    """One checker part's result. ``metrics`` keys are documented in each part's module (e.g. stability:
    ``gm_m``, ``trim_m``, ``list_rad``, ``displacement_kg``, ``area_0_30_mrad``...). ``margins``: limit − value
    in the limit's unit (positive = inside), used for D11 tie-breaks. ``ran`` False + ``error`` if it couldn't run
    (that is itself a hard ``checker.*`` violation)."""

    name: str
    violations: list[Violation] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    margins: dict[str, float] = Field(default_factory=dict)
    ran: bool = True
    error: str | None = None
    ballast_programme: BallastProgramme | None = None

    @model_serializer(mode='wrap')
    def _serialize_programme(self, handler):
        data = handler(self)
        # Legacy parts never supplied this field, so their bytes stay exactly
        # the same. An explicit activated null is retained through public JSON
        # parsing, rather than conflated with a legacy absent programme block.
        if self.ballast_programme is None and 'ballast_programme' not in self.model_fields_set:
            data.pop('ballast_programme', None)
        return data


class CheckReport(StowModel):
    """Full checker verdict on (instance, plan). The source of truth (CLAUDE.md).

    passed: no hard violation in any part (moves, static rules, stability, strength, visibility, lashing,
        plan constraints) and every part ran.
    violations: all violations from all parts, sorted by (call_index, move_index, rule_id).
    parts: part name → report. Names: ``moves``, ``rules``, ``stability``, ``strength``, ``visibility``,
        ``lashing``, ``constraints``.
    count: move count from the move engine (None if the plan couldn't be replayed at all).
    shut_out: load-list boxes not loaded (D11 level 2; non-empty → hard violation ``objective.all_loaded``).
    """

    passed: bool
    violations: list[Violation] = Field(default_factory=list)
    parts: dict[str, PartReport] = Field(default_factory=dict)
    count: MoveCount | None = None
    shut_out: list[ContainerId] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)


PlannerStatus = Literal["ok", "dead_end", "infeasible", "timeout", "error"]


class PlannerResult(StowModel):
    """What every planner returns (planner protocol, ``stowage.planner``).

    status: ``ok`` = a complete plan (not yet judged); ``dead_end`` = the planner got stuck (StowLM under the mask,
        a heuristic) and ``plan`` holds the partial plan; ``infeasible`` = the planner *proved* no plan exists
        (exact solver; ``message`` says why), or ``planner.finalize`` proved that *this stow* has no passing
        ballast (``metrics["ballast_status"] == "infeasible_proven"``; other stows are not excluded, so this is a
        planner failure, not an instance-infeasibility claim); ``timeout``/``error``: ``plan`` may hold the best
        partial plan.
    plan: the MovePlan for ``instance.call_index`` (None only if nothing at all was produced).
    later_plans: stage D only — plans for the further calls in ``Instance.plan_calls`` (one MovePlan per call, in
        order; each is replayed from the state the previous one leaves, with forecast scenario boxes). Empty in A–C.
    planner: name, e.g. ``"solver.exact"``, ``"baseline.twostep"``, ``"stowlm"``, ``"spark.zeroshot"``.
    planner_version: e.g. adapter id + step, solver version.
    seed: seed used (determinism: same instance + seed + versions → same plan, except planners documented as
        nondeterministic, e.g. multi-threaded CP-SAT, which must say so in ``metrics["deterministic"] = 0``).
    wall_time_s: planning time, s (excluding ballast finishing and checking).
    metrics: planner-specific numbers, documented by each planner (``lower_bound``, ``gap``, ``tokens_out``,
        ``mask_dead_ends``, ``cost_usd``...).
    repaired: True if 5.7 repair changed the plan; ``repair_note`` says what (fallback rate = share repaired).
    provenance: merged from the instance and every component used.

    shippable: derived from full checker evidence for every carried plan and clean provenance of the inputs,
        planner, adapter, plans and checker. A caller's ``metrics["shippable"]`` is never evidence. The guarded
        pipeline supplies that compatibility metric from this property. Until sequential checking is implemented,
        results with ``later_plans`` or required ``instance.plan_calls`` are ineligible. A missing replay count
        also makes the evidence incomplete. Evidence is private and bound to the exact checked candidate:
        changing the plan invalidates it, and a deserialized result must be checked again before shipping.
    """

    status: PlannerStatus
    plan: MovePlan | None = None
    later_plans: list[MovePlan] = Field(default_factory=list)
    planner: str
    planner_version: str
    seed: int | None = None
    wall_time_s: float = 0.0
    metrics: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None
    repaired: bool = False
    repair_note: str | None = None
    provenance: Provenance = Field(default_factory=Provenance)

    _shipping_evidence: tuple[Any, str, str, CheckReport, str] | None = PrivateAttr(default=None)

    def _record_full_check(self, instance: Any, report: CheckReport) -> None:
        """Record the pipeline's verdict; this is not a caller-supplied shipping flag."""
        if self.plan is not None:
            self._shipping_evidence = (
                instance, instance.model_dump_json(), self.plan.model_dump_json(),
                report, report.model_dump_json(),
            )

    @property
    def shippable(self) -> bool:
        """Whether every carried plan has an unchanged full, passing, clean checker verdict."""
        from stowage.frozen import StubTaintError, active_stubs, require_clean

        if self.status != "ok" or self.plan is None or self.later_plans or self._shipping_evidence is None:
            return False
        instance, instance_json, plan_json, report, report_json = self._shipping_evidence
        if (instance.plan_calls or active_stubs() or instance.model_dump_json() != instance_json
                or self.plan.model_dump_json() != plan_json or report.model_dump_json() != report_json):
            return False
        required = {"moves", "rules", "stability", "strength", "visibility", "lashing", "constraints"}
        if (not report.passed or report.count is None or not required.issubset(report.parts)
                or report.shut_out or any(v.severity == "hard" for v in report.violations)
                or any(not p.ran or p.error or any(v.severity == "hard" for v in p.violations)
                       for p in report.parts.values())):
            return False
        try:
            require_clean(self, instance, report, what="planner shipping")
        except StubTaintError:
            return False
        return True


class BallastOutcome(StowModel):
    """Result of the ballast finisher (4.2).

    status: ``kept`` = the plan already had ballast (planner set it), returned unchanged; ``ok`` = ballast found and
        the exact stability/strength/visibility/lashing parts pass at every D57 voyage state; ``infeasible_proven`` =
        no ballast in the tanks can make *this fixed stow* pass (``proof`` holds the argument, e.g. an exact bound
        like max achievable GM, or a static stack-weight/height violation that tanks cannot change). It says
        nothing about other cargo placements, and an LP that fails only under margins tightened by the 3.7 error
        bound is not a proof (D49: conservative margins are not necessary conditions);
        ``not_found`` = no ballast found within the time limit and no proof (counts as a failure; never shipped).
    plan: the plan with ``ballast`` set (status ``kept``/``ok``), else None.
    parts: the exact physics part reports on the final condition (stability, strength, visibility, lashing).
    """

    status: Literal["kept", "ok", "infeasible_proven", "not_found"]
    plan: MovePlan | None = None
    proof: str | None = None
    parts: dict[str, PartReport] = Field(default_factory=dict)
    iterations: int = 0
    wall_time_s: float = 0.0
    provenance: Provenance = Field(default_factory=Provenance)
