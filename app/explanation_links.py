"""Which checker lines each shown Spark statement came from, for the video's Spark panel. Render-only.

explanation_facts turns the bound checker report into the statement menu; Spark only picks and orders statement ids, and the
shown text is those statements verbatim. So the statements of a bound explanation are found by exact match in its text, in
Spark's order, and each statement id names the report lines it is rendered from. Nothing here writes a word or a number:
statements come from the explainer, line values from the same facts. No model call. A template or an unbound explanation
gets no links, so the panel can't narrate over it.
"""
from __future__ import annotations

from typing import Any

_STATEMENT_LINES = {
    "total": ("moves.total", "moves.weighted"),
    "loads": ("moves.load",),
    "discharges": ("moves.discharge",),
    "yard_rehandles": ("moves.relocate",),
    "restows_now": ("moves.restow",),
    "no_restows_now": ("moves.restow",),
    "hatch_covers_now": ("moves.hatch",),
    "later_ports": ("moves.later",),
    "tightest_limit": ("limit.tightest",),
    "no_violations": ("violations",),
    "omitted": ("omitted",),
}


def report_lines(facts: dict[str, Any], part_names: dict[str, str]) -> list[dict[str, str]]:
    """The report as lines on screen: every value is a field of the facts built from the bound checker report."""
    planned = facts.get("planned_moves_by_type") or {}
    restows = sum(planned.get(k, 0) for k in ("restow_off", "restow_on", "restow_shift"))
    covers = sum(planned.get(k, 0) for k in ("hatch_cover_off", "hatch_cover_on"))
    lines = [("moves.total", "Crane lifts at this call", facts.get("lifts_now")),
             ("moves.weighted", "Weighted move cost (D11)", facts.get("weighted")),
             ("moves.load", "Boxes loaded", planned.get("load", 0)),
             ("moves.relocate", "Yard rehandles", planned.get("relocate", 0)),
             ("moves.restow", "Restow lifts now", restows),
             ("moves.hatch", "Hatch-cover lifts now", covers),
             ("moves.later", "Projected later-port lifts",
              f"{facts.get('later_restow_lifts')} restow · {facts.get('later_hatch_cover_lifts')} hatch")]
    if planned.get("discharge"):
        lines.insert(3, ("moves.discharge", "Boxes discharged", planned["discharge"]))
    ratios = [m for m in facts.get("checker_margins") or [] if m["metric"].endswith("_ratio") and m["margin"] >= 0]
    if ratios:
        tight = min(ratios, key=lambda m: (m["margin"], m["part"], m["metric"]))
        lines.append(("limit.tightest", "Tightest limit", tight["meaning"]))
    flagged = set(facts.get("violations_by_check") or {})
    for part in facts.get("checks_ran") or []:
        if part in part_names:
            lines.append((f"check.{part}", part_names[part], "violations" if part in flagged else "no violations"))
    lines.append(("violations", "Violations", sum((facts.get("violation_counts") or {}).values())))
    if facts.get("margins_omitted") or facts.get("violations_omitted"):
        lines.append(("omitted", "Further limit margins in the report", facts.get("margins_omitted")))
    return [{"key": key, "label": label, "value": str(value)} for key, label, value in lines]


def statement_lines(statement_id: str, facts: dict[str, Any], part_names: dict[str, str]) -> list[str]:
    if statement_id == "clean_checks":
        flagged = set(facts.get("violations_by_check") or {})
        return [f"check.{p}" for p in facts.get("checks_ran") or [] if p in part_names and p not in flagged]
    return list(_STATEMENT_LINES.get(statement_id, ()))


def link_statements(text: str, statements: list[dict[str, str]], facts: dict[str, Any], part_names: dict[str, str]) -> dict | None:
    """The chosen statements in shown order, each with its lines, or None if none is found verbatim in the text."""
    shown = text.split("Other checked statements:")[0]
    found = sorted(((shown.index(s["text"]), s) for s in statements if s["text"] in shown), key=lambda item: item[0])
    if not found:
        return None
    lines = report_lines(facts, part_names)
    return {"statements": [{"id": s["id"], "kind": s["kind"], "text": s["text"],
                            "lines": statement_lines(s["id"], facts, part_names)} for _, s in found], "lines": lines}


def explanation_links(replay: dict, explanation: dict) -> dict | None:
    """Links for a bound Spark explanation of this replay; None for anything else."""
    if (explanation or {}).get("source") != "spark-cached" or not explanation.get("text"):
        return None
    from llm.spark.explain import _PART_SHORT, explanation_facts
    from stowage.model import CheckReport, Instance, MovePlan
    try:
        facts = explanation_facts(Instance.model_validate(replay["instance"]), MovePlan.model_validate(replay["plan"]),
                                  CheckReport.model_validate(replay["checker"]["report"]))
    except (KeyError, TypeError, ValueError):
        return None
    return link_statements(explanation["text"], facts["statements"], facts, _PART_SHORT)
