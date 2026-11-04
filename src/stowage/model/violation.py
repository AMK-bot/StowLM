"""Violation: one broken rule, tagged with its rule-catalogue id (plan 1.1; produced by 2.3, 2.4, 3.1–3.3, 4.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from stowage.model.base import CallIndex, ContainerId, RuleId, SlotId, StowModel, YardAddr

Severity = Literal["hard", "soft", "tolerated"]


class Violation(StowModel):
    """A rule broken by a plan or a state.

    Fields:
        rule_id: catalogue id (plan 1.2), e.g. ``"imdg.segregation.7_4_3"``, ``"moves.top_box_only"``. Checker-internal
            problems use the reserved ``checker.*`` ids (``checker.missing_vessel_data``, ``checker.internal_error``),
            which are always hard.
        severity: from the catalogue entry's hardness *as applied*: ``hard`` (plan fails), ``soft`` (a penalty/tie-break,
            D11 level 4), ``tolerated`` (a tolerated-if-inherited rule broken only by boxes already aboard at arrival;
            stage B, D11). ``CheckReport.passed`` is False iff any violation is ``hard``.
        call_index: the call at which the violated condition exists (departure from that call for static rules).
        move_index: index into ``MovePlan.moves`` of the offending move (move legality), else None (departure state).
        containers / slots / yard_addresses: what is involved (sorted).
        measured, limit, unit: the quantity and its limit when numeric (e.g. GM 0.12 vs 0.15 "m"; stack mass in "kg").
        message: one plain-language sentence (shown in the UI and to the explainer, 5.6).
        inherited: True if every container involved was aboard at arrival (stage B).
    """

    rule_id: RuleId
    severity: Severity
    call_index: CallIndex
    move_index: int | None = None
    containers: list[ContainerId] = Field(default_factory=list)
    slots: list[SlotId] = Field(default_factory=list)
    yard_addresses: list[YardAddr] = Field(default_factory=list)
    measured: float | None = None
    limit: float | None = None
    unit: str | None = None
    message: str
    inherited: bool = False

    @field_validator("containers", "slots", "yard_addresses")
    @classmethod
    def canonical_references(cls, value: list[str]) -> list[str]:
        return sorted(set(value))
