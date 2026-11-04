"""Public hazard names, spoken numbers, and explicit context class policy.

These are vocabulary aliases, not chemical classification or a segregation table.
Bare ``toxic`` and ``flammable`` do not name an unambiguous transport class.
In particular, toxic substances are 6.1, toxic gases 2.3, never whole class 6.
Sources were checked for D224; authored spelling variants are identified below.
"""
from __future__ import annotations

import re

SOURCES = (
    {"title": "UK Department for Transport: dangerous goods classes",
     "url": "https://www.gov.uk/moving-dangerous-goods/what-are-dangerous-goods"},
    {"title": "Transport Canada: dangerous goods marks and classes description",
     "url": "https://tc.canada.ca/en/dangerous-goods/safety-awareness-materials-faq/industry/dangerous-goods-marks/dangerous-goods-marks-classes-description"},
)
ALL_IMDG_CLASSES = tuple(str(n) for n in range(1, 10)) + (
    "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "2.1", "2.2", "2.3",
    "4.1", "4.2", "4.3", "5.1", "5.2", "6.1", "6.2")

# Singular/plural and US/UK spellings are authored aliases of the cited names.
ALIAS_CLASSES = {
    "corrosive": "8", "corrosives": "8", "corrosive substance": "8", "corrosive substances": "8",
    "explosive": "1", "explosives": "1", "explosive substances": "1",
    "gas": "2", "gases": "2",
    "flammable liquid": "3", "flammable liquids": "3",
    "flammable gas": "2.1", "flammable gases": "2.1",
    "non flammable non toxic gas": "2.2", "non flammable non toxic gases": "2.2",
    "toxic gas": "2.3", "toxic gases": "2.3",
    "flammable solid": "4.1", "flammable solids": "4.1",
    "oxidiser": "5.1", "oxidisers": "5.1", "oxidizer": "5.1", "oxidizers": "5.1",
    "oxidising substances": "5.1", "oxidizing substances": "5.1",
    "organic peroxide": "5.2", "organic peroxides": "5.2",
    "toxic substance": "6.1", "toxic substances": "6.1",
    "toxic liquid": "6.1", "toxic liquids": "6.1", "toxic solid": "6.1", "toxic solids": "6.1",
    "infectious substance": "6.2", "infectious substances": "6.2",
    "radioactive material": "7", "radioactive materials": "7",
}
AMBIGUOUS_HAZARDS = frozenset({"toxic", "toxics", "flammable", "flammables"})
_SMALL = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
          "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")


def normalize_words(text: str) -> str:
    return " ".join(text.casefold().replace("-", " ").split())


def number_words(value: int) -> str:
    """Spell one nonnegative whole number, 0..99 (sufficient for these bays)."""
    if type(value) is not int or not 0 <= value <= 99:
        raise ValueError("Supported spoken whole-number range is 0..99")
    if value < 20:
        return _SMALL[value]
    tens, units = divmod(value, 10)
    return _TENS[tens] + (" " + _SMALL[units] if units else "")


def parse_number(text: str) -> int | None:
    """Exact number phrase only; never extract a convenient number from prose."""
    text = normalize_words(text)
    if re.fullmatch(r"[0-9]{1,2}", text):
        return int(text)
    radio = {"oh": 0, "tree": 3, "fife": 5, "niner": 9}
    if text in radio:
        return radio[text]
    for value in range(100):
        if text == number_words(value):
            return value
    return None


def _class(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("IMDG classes must be recognised strings or numbers")
    result = str(value).strip()
    if result not in ALL_IMDG_CLASSES:
        raise ValueError(f"Unsupported IMDG class: {result}")
    return result


def _class_list(value) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("A declared IMDG class whitelist must be a list")
    return tuple(dict.fromkeys(_class(item) for item in value))


def allowed_imdg_classes(context: dict | None) -> tuple[str, ...]:
    """Read a whitelist without widening a class to its parent or divisions.

    Priority: top-level allowed_imdg_classes, imdg_classes, declared_imdg_classes;
    then the same declarations within vessel or ship_context. Explicit empty
    declarations mean no classes. Otherwise gather actual non-null imdg_class
    values from cargo/containers (direct or dg.imdg_class), including those two
    nested context objects. Empty/unannotated cargo does not prove class absence:
    legacy contexts without class information retain the generic vocabulary.
    Invalid declarations/cargo class values raise ValueError rather than widen.
    """
    if context is None:
        return ALL_IMDG_CLASSES
    if not isinstance(context, dict):
        raise ValueError("Vessel context must be an object")
    contexts = [context]
    for key in ("vessel", "ship_context"):
        if key in context and context[key] is not None:
            if not isinstance(context[key], dict):
                raise ValueError(f"{key} context must be an object")
            contexts.append(context[key])
    for item in contexts:
        for key in ("allowed_imdg_classes", "imdg_classes", "declared_imdg_classes"):
            if key in item:
                return _class_list(item[key])
    found = []
    for item in contexts:
        for key in ("cargo", "containers"):
            if key not in item:
                continue
            if not isinstance(item[key], list):
                raise ValueError(f"{key} must be a list of cargo objects")
            for cargo in item[key]:
                if not isinstance(cargo, dict):
                    raise ValueError("Cargo entries must be objects")
                if cargo.get("imdg_class") is not None:
                    found.append(_class(cargo["imdg_class"]))
                dg = cargo.get("dg")
                if isinstance(dg, dict) and dg.get("imdg_class") is not None:
                    found.append(_class(dg["imdg_class"]))
    return tuple(dict.fromkeys(found)) if found else ALL_IMDG_CLASSES


def class_is_allowed(imdg_class: str, context: dict | None) -> bool:
    return _class(imdg_class) in allowed_imdg_classes(context)


def resolve_hazard(phrase: str, context: dict | None = None) -> str | None:
    """Resolve one exact hazard name; None means unknown/ambiguous/disallowed."""
    phrase = normalize_words(phrase).removeprefix("the ")
    result = ALIAS_CLASSES.get(phrase)
    return result if result is not None and class_is_allowed(result, context) else None
