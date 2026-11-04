"""Planner-instruction schema (plan v4 task 5.10): what Muse Spark may return for one sentence.

Every constraint type is *tighten-only by construction*: it can only remove ship slots from the set a box may be
placed in. No type can add a slot, relax a rule or change a limit, so a loosening request has no representation and
must come back as a :class:`Refusal`. Strict validation (``extra="forbid"``, exact literals, bounded integers) means
anything else Spark writes is rejected rather than interpreted.

Bays follow the ISO bay-plan convention used across the repo (``stowage.conventions``): odd bays are 20-ft bays,
an even bay is the 40-ft bay over its two odd neighbours. Tiers are counted per stack from the bottom of the zone
(hold or deck), so ``max_tiers=2`` in the hold keeps tiers 02 and 04 and closes 06 and above.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

SCHEMA_VERSION = "stowlm.instruction/1"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CloseBay(_Strict):
    """(a) Bay or crane out: no box may be loaded into ``bay`` (an even bay also closes its two odd bays)."""

    type: Literal["close_bay"]
    bay: int = Field(ge=1, le=999)


class MaxTiers(_Strict):
    """(b) At most ``max_tiers`` tiers in ``zone`` per stack, in ``bay`` (``None`` = every bay)."""

    type: Literal["max_tiers"]
    bay: int | None = Field(default=None, ge=1, le=999)
    zone: Literal["hold", "deck"]
    max_tiers: int = Field(ge=0, le=12)


class AccommodationRef(_Strict):
    kind: Literal["accommodation"]


class BayRef(_Strict):
    kind: Literal["bay"]
    bay: int = Field(ge=1, le=999)


class DgKeepAway(_Strict):
    """(c) DG keep-away: boxes of IMDG class ``imdg_class`` (a division such as ``"2.1"`` or a whole class such
    as ``"2"``) at least ``min_bays`` 20-ft bays from the accommodation or from a named bay."""

    type: Literal["dg_keep_away"]
    imdg_class: str = Field(pattern=r"^[1-9](\.[1-6])?$")
    min_bays: int = Field(ge=1, le=20)
    away_from: Annotated[Union[AccommodationRef, BayRef], Field(discriminator="kind")]


Constraint = Annotated[Union[CloseBay, MaxTiers, DgKeepAway], Field(discriminator="type")]
REFUSAL_CODES = ("ambiguous", "unsupported", "loosening", "not_an_instruction")


class Refusal(_Strict):
    """The parser's answer when it must not guess: ambiguous, unsupported, loosening or not an instruction."""

    refused: Literal[True] = True
    code: Literal["ambiguous", "unsupported", "loosening", "not_an_instruction"]
    reason: str = Field(min_length=1, max_length=300)


class _ConstraintEnvelope(_Strict):
    schema_version: Literal["stowlm.instruction/1"]
    constraint: Constraint


class _RefusalEnvelope(_Strict):
    schema_version: Literal["stowlm.instruction/1"]
    refusal: Refusal


_ENVELOPE = TypeAdapter(Union[_ConstraintEnvelope, _RefusalEnvelope])


def validate_answer(text: str) -> CloseBay | MaxTiers | DgKeepAway | Refusal:
    """Strictly validate Spark's raw answer; raise ``ValueError`` on anything that isn't exactly one envelope.

    JSON is parsed by ``json.loads`` (no fence stripping, no repair): a near-miss is an invalid answer, not a guess.
    """
    try:
        value = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"not JSON: {type(exc).__name__}") from None
    if not isinstance(value, dict):
        raise ValueError("answer must be a JSON object")
    envelope = _ENVELOPE.validate_python(value)
    return envelope.refusal if isinstance(envelope, _RefusalEnvelope) else envelope.constraint


def schema_json() -> dict:
    """JSON Schema of the answer envelope (versioned), for docs and the prompt."""
    return _ENVELOPE.json_schema()
