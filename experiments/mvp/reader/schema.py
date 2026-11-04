"""Experiment-only, tighten-only reader contract. Never changes production schemas.

The existing three placement constraints retain their production model classes;
the only addition is a restriction on distinct discharge ports per physical cover.
"""
from __future__ import annotations

import json
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from llm.instructions.schema import CloseBay, DgKeepAway, MaxTiers, Refusal

SCHEMA_VERSION = "stowlm.reader/2"
REFUSAL_REASONS = {
    "ambiguous": "The instruction is uncertain or ambiguous.",
    "unsupported": "The instruction is outside the supported vessel or vocabulary.",
    "loosening": "The instruction would relax a constraint.",
    "not_an_instruction": "The sentence does not give a planning instruction.",
}
IMDG_CLASSES = frozenset(
    [str(i) for i in range(1, 10)]
    + [f"1.{i}" for i in range(1, 7)]
    + [f"2.{i}" for i in range(1, 4)]
    + [f"4.{i}" for i in range(1, 4)]
    + ["5.1", "5.2", "6.1", "6.2"]
)


class OnePortPerHatch(BaseModel):
    """Each physical hatch cover may serve at most one POD across hold AND deck.

    Empty covers are allowed. This does not designate a port or authorize moving
    retained onboard cargo; an incompatible fixed state makes the request infeasible.
    """
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    type: Literal["one_port_per_hatch"]


Constraint = Annotated[Union[CloseBay, MaxTiers, DgKeepAway, OnePortPerHatch], Field(discriminator="type")]
Answer = CloseBay | MaxTiers | DgKeepAway | OnePortPerHatch | Refusal


class _ConstraintEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["stowlm.reader/2"]
    constraint: Constraint


class _RefusalEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["stowlm.reader/2"]
    refusal: Refusal


_ENVELOPE = TypeAdapter(Union[_ConstraintEnvelope, _RefusalEnvelope])


def refusal(code: str) -> Refusal:
    return Refusal(code=code, reason=REFUSAL_REASONS[code])


def _unique_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON key")
        out[key] = value
    return out


def validate_context(answer: Answer, context: dict) -> Answer:
    """Raise on invalid references/ranges; never silently clamp or remap a bay."""
    if isinstance(answer, Refusal):
        return answer
    named = getattr(answer, "bay", None)
    if isinstance(answer, DgKeepAway):
        if answer.imdg_class not in IMDG_CLASSES:
            raise ValueError("unsupported IMDG class")
        if answer.away_from.kind == "bay":
            named = answer.away_from.bay
        elif not context.get("has_accommodation", False):
            raise ValueError("no accommodation in vessel context")
    if named is not None and named not in context["bays"]:
        raise ValueError("bay is not in vessel context")
    if isinstance(answer, MaxTiers):
        cap = context["max_tiers"][answer.zone]
        by_bay = context.get("max_tiers_by_bay", {})
        if named is not None:
            cap = by_bay.get(str(named), {}).get(answer.zone, cap)
        if answer.max_tiers > cap:
            raise ValueError("tier count is outside vessel context")
    if isinstance(answer, OnePortPerHatch) and not context.get("hatch_covers"):
        raise ValueError("no hatch covers in vessel context")
    return answer


def validate_answer(text: str, context: dict | None = None) -> Answer:
    """Parse exactly one JSON envelope, with no fence stripping or repair."""
    value = json.loads(text, object_pairs_hook=_unique_object)
    envelope = _ENVELOPE.validate_python(value)
    answer = envelope.refusal if isinstance(envelope, _RefusalEnvelope) else envelope.constraint
    # Class validity is independent of the vessel context.
    if isinstance(answer, DgKeepAway) and answer.imdg_class not in IMDG_CLASSES:
        raise ValueError("unsupported IMDG class")
    return validate_context(answer, context) if context is not None else answer


def answer_envelope(answer: Answer) -> dict:
    key = "refusal" if isinstance(answer, Refusal) else "constraint"
    return {"schema_version": SCHEMA_VERSION, key: answer.model_dump()}


def canonicalize(answer: str | dict | Answer, context: dict | None = None) -> dict:
    """Exact structured target; ignore only free-form refusal rationale.

    Every field and refusal code still counts. Invalid output raises, so harnesses
    must count it as incorrect rather than replacing it with a helpful refusal.
    """
    if isinstance(answer, dict):
        answer = validate_answer(json.dumps(answer), context)
    elif isinstance(answer, str):
        answer = validate_answer(answer, context)
    elif context is not None:
        validate_context(answer, context)
    out = answer_envelope(answer)
    if "refusal" in out:
        out["refusal"].pop("reason")
    return out


def schema_json() -> dict:
    return _ENVELOPE.json_schema()


def context_from_instance(instance) -> dict:
    """Expose physical ship values only, from a public/synthetic instance."""
    from stowage.conventions import odd_bays_of, parse_slot
    raw = instance if isinstance(instance, dict) else instance.model_dump(mode="json")
    vessel = raw["vessel"]
    cells = {c["id"] for c in vessel["cells"]}
    odd = sorted({parse_slot(c).bay for c in cells})
    # Adjacent odd bays do not imply a physical 40-foot bay across hatch pairs.
    # Prefer declared pair geometry; sliced instances retain cover bay names.
    named_even = {p["even_bay"] for p in vessel.get("bay_pairs", [])}
    named_even.update(b for h in vessel.get("hatch_covers", []) for b in h.get("bays", []) if b % 2 == 0)
    bays = sorted(set(odd) | {b for b in named_even if set(odd_bays_of(b)) <= set(odd)})
    by_bay = {}
    for bay in bays:
        by_bay[str(bay)] = {
            zone: max((len(set(s["cell_ids"]) & cells) for s in vessel["stacks"]
                       if s["bay"] in odd_bays_of(bay)
                       and s["on_deck"] == (zone == "deck")), default=0)
            for zone in ("hold", "deck")
        }
    max_tiers = {zone: max((v[zone] for v in by_bay.values()), default=0) for zone in ("hold", "deck")}
    return {
        "id": raw.get("id", vessel.get("id", "synthetic-vessel")),
        "bays": bays,
        "max_tiers": max_tiers,
        "max_tiers_by_bay": by_bay,
        "has_accommodation": any(r["kind"] == "accommodation" for r in vessel.get("exclusion_regions", [])),
        "hatch_covers": sorted(h["id"] for h in vessel.get("hatch_covers", [])),
        "ports": list(dict.fromkeys(c["locode"] for c in sorted(raw["rotation"]["calls"], key=lambda c: c["index"])
                                   if c["index"] > raw["call_index"])),
    }


def system_prompt() -> str:
    return '''Read one planner sentence. Return JSON only, schema_version="stowlm.reader/2", exactly one constraint or refusal.
Constraints: {"type":"close_bay","bay":N}; {"type":"max_tiers","bay":N or null,"zone":"hold" or "deck","max_tiers":T}; {"type":"dg_keep_away","imdg_class":"X","min_bays":K,"away_from":{"kind":"bay","bay":N} or {"kind":"accommodation"}}; {"type":"one_port_per_hatch"}.
CloseBay bars loading; MaxTiers caps occupied tiers per stack; DG distance counts 20ft bays (1 to 3 is one bay), K=1..20; one_port_per_hatch permits at most one discharge port per physical cover across hold and deck, including retained cargo. IMDG whole classes 1..9 or divisions 1.1..1.6,2.1..2.3,4.1..4.3,5.1,5.2,6.1,6.2 only.
Use only context bays and physical tier ranges, T=0..zone maximum (bay-specific when supplied). Never guess, clamp or change a named bay. Apply one explicit firm supported restriction only. Hedged/unclear or missing tier zone => ambiguous; multiple distinct restrictions, nonexistent bay, out-of-range values, half-cover scope or unsupported requests => unsupported; any relaxation/removal/override/permission to break a rule => loosening (even mixed with tightening). Polite questions requesting a clear restriction are instructions; mere commentary/informational questions => not_an_instruction.
Refusal: {"schema_version":"stowlm.reader/2","refusal":{"refused":true,"code":"ambiguous" or "unsupported" or "loosening" or "not_an_instruction","reason":"brief reason"}}. Constraints go under "constraint" with the same schema_version. No extra fields.'''


def messages(sentence: str, context: dict) -> list[dict]:
    return [{"role": "system", "content": system_prompt()},
            {"role": "user", "content": "Vessel: " + json.dumps(context, sort_keys=True, separators=(",", ":"))
             + "\nSentence: " + sentence}]
