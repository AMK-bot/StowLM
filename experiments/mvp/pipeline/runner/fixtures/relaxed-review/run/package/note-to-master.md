RELAXED PLAN — level 3; broken rules: ["expert.e8.away_from_heat"]; boxes ashore: 0. Synthetic ships and cargo, simulated port. Not a full checker pass. Pipeline: not frozen yet. Checker coverage: 54 active, 8 inactive.

RELAXED PLAN — broken rules: ["expert.e8.away_from_heat"]

# Note to the master

Terminology: 'crane lifts' in the inherited checker statements below counts this-call moves by all equipment, including yard relocations and hatch-cover moves.

[Deterministic template fallback]
Checker: FAIL; this plan must not be shipped.
Expert.e8.away_from_heat: 2 hard violations. Boxes: QZXU0000035. Cargo is too close to heat source heat-FO-F-P.
4 boxes are loaded.
1 yard rehandle (a box moved within the yard to reach another, not a move on board): QZWU0000005.
No restow lifts at this call.
2 hatch-cover lifts at this call (HC-002-P).
Projected at later ports: 0 restow lifts and 4 hatch-cover lifts.
7 crane lifts at this call in the checker's replay; weighted move cost 7 (D11 lift weights, including projected later-port lifts; not money).
Tightest limit reported as a share of its limit: lashing, lashing rod load, 39% of the limit spare.
No violations reported in: plan constraints, lashing, move replay, stability, hull strength, bridge visibility, voyage conditions.
Not listed here but in the checker report: 17 further limit margins and 0 further violation types.
Status: a proposal for the carrier's planners to review and approve (expert Q14), not a final stow plan.
