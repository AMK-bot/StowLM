"""Offline rule-based fallback parser: the three supported kinds in their canonical wording only.

For use when Muse Spark is unavailable and no recorded response exists (``demo --offline-rules``). It accepts a
sentence only if the *whole* sentence matches one canonical form below (case, spacing, a trailing full stop and
leading zeros on bay numbers are normalised); anything else, including every paraphrase, Thai-English mix,
permission or second clause, is refused as ``ambiguous``. It never guesses: it is deliberately much narrower than
the Spark parser, and its output goes through the same strict schema.

    close bay N | no loading in(to) bay N | nothing goes into bay N
    max T tiers on deck [in bay N] | max T tiers in the hold [of bay N]
    keep class X at least K bays from the accommodation | keep class X at least K bays from bay N
"""

from __future__ import annotations

import re

from llm.instructions.schema import CloseBay, DgKeepAway, MaxTiers, Refusal

_N = r"0*(\d{1,3})"
_FORMS = [
    (re.compile(rf"(?:close|no loading in|no loading into|nothing goes into) bay {_N}"),
     lambda m: CloseBay(type="close_bay", bay=int(m[1]))),
    (re.compile(rf"max (\d{{1,2}}) tiers? on deck(?: in bay {_N})?"),
     lambda m: MaxTiers(type="max_tiers", bay=int(m[2]) if m[2] else None, zone="deck", max_tiers=int(m[1]))),
    (re.compile(rf"max (\d{{1,2}}) tiers? in the hold(?: of bay {_N})?"),
     lambda m: MaxTiers(type="max_tiers", bay=int(m[2]) if m[2] else None, zone="hold", max_tiers=int(m[1]))),
    (re.compile(rf"keep class ([1-9](?:\.[1-6])?) at least (\d{{1,2}}) bays? from the accommodation"),
     lambda m: DgKeepAway(type="dg_keep_away", imdg_class=m[1], min_bays=int(m[2]),
                          away_from={"kind": "accommodation"})),
    (re.compile(rf"keep class ([1-9](?:\.[1-6])?) at least (\d{{1,2}}) bays? from bay {_N}"),
     lambda m: DgKeepAway(type="dg_keep_away", imdg_class=m[1], min_bays=int(m[2]),
                          away_from={"kind": "bay", "bay": int(m[3])})),
]
_REFUSAL = ("The offline rule parser understands only its canonical forms (e.g. 'close bay 3', 'max 2 tiers on deck "
            "in bay 1', 'keep class 3 at least 2 bays from the accommodation'); nothing was applied.")


def parse_rules(sentence: str) -> CloseBay | MaxTiers | DgKeepAway | Refusal:
    """Exact-form parse; refuse anything else (never raises on input)."""
    if not isinstance(sentence, str) or not sentence.isascii():
        return Refusal(code="ambiguous", reason=_REFUSAL)
    text = " ".join(sentence.lower().split()).rstrip(".").strip()
    for pattern, build in _FORMS:
        match = pattern.fullmatch(text)
        if match:
            try:
                return build(match)
            except ValueError:  # out-of-range numbers fail the strict schema: refuse
                break
    return Refusal(code="ambiguous", reason=_REFUSAL)
