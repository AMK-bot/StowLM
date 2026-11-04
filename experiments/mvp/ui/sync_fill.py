"""Write the video's value files for the page: stage-fillv6.js from fill-v6.json, stage-fillv5.js from fill-v5.json, stage-fill.js from fill.json.

    uv run python experiments/mvp/ui/sync_fill.py

fill-v6.json (experiments/mvp/video/) is what the film reads (window.STAGE_FILL_V6): one key per {{name}} and [[IF name: …]] of
script-v6.md, filled by hand at task 4.2 from STORY-NEEDS-v6.md. A name is a number unless V6_TEXT_KEYS says it's words; an IF name
is true or absent. Unset: a chip in draft, a dropped segment in final, and the block goes. The hero's own numbers come from the
gate at boot (stage-director.js), so they aren't needed here.
fill-v5.json is what the v5 rehearsal reads (window.STAGE_FILL_V5). Task 4.2 fills it by hand from the
sources in STORY-NEEDS.md, keyed by the placeholder text of script.md: {{NUMBER: reader accuracy %}} is the key
"reader accuracy %". REPO_URL is the one other key. Nothing from STORY-NEEDS's provisional column goes in. An absent value
is a PENDING chip in draft, a dropped segment in final, and "pending: <task>" on a card.
fill.json is the 30 Oct numbers of the retired variants. The page no longer loads stage-fill.js; it stays in sync for the
fill tests and the archive.
Fail-closed: a key the script doesn't define, a nested value, a leftover placeholder, or a non-number where the script needs
a number stops the run and leaves both output files as they were. This script never computes or invents a value.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VIDEO = ROOT / "experiments/mvp/video"
FILL_JSON = VIDEO / "fill.json"
OUTPUT = ROOT / "app/static/stage-fill.js"
FILL_V5_JSON = VIDEO / "fill-v5.json"
OUTPUT_V5 = ROOT / "app/static/stage-fillv5.js"
SCRIPT_V5 = VIDEO / "script.md"
FILL_V6_JSON = VIDEO / "fill-v6.json"
OUTPUT_V6 = ROOT / "app/static/stage-fillv6.js"
SCRIPT_V6 = VIDEO / "script-v6.md"
V6_TEXT_KEYS = frozenset("""hero_sentence hero_backup_sentence hero_plan_date backup_plan_date hero_gauges_line lifts_map_size repo_url
    impossible_sentence meta_model_count""".split())
V5_NUMBER = re.compile(r"^[+−-]?[0-9][0-9,]*(\.[0-9]+)?$")
V5_TEXT_KEYS = frozenset({"REPO_URL"})
SCRIPTS = tuple(VIDEO / f"script.{v}.md" for v in ("worse", "par", "better"))
PLAIN_NUMBER = re.compile(r"^[0-9]+(\.[0-9]+)?$")
# Results-card cells: a count or a mean goes in these columns, so no percent or words (logic-demo P1-3).
NUMERIC = re.compile(r"(_PASS|_MUTUAL|_MOVES|_UNFINISHED|_DEAD|_TIMEOUT)$")
# Worse's placeholders were filled in place on 30 Oct (VARIANTS.md "Chosen"), so its script no longer names them. Frozen
# here: the key table as it stood, the pitch's extra keys from v4r-fill-worse, and the tiny paired Δ.
FILLED_IN_PLACE = frozenset("""ADAPTER TEACHER_LINE SMALL_PASS SMALL_TS_PASS SMALL_MUTUAL SMALL_MOVES SMALL_TS_MOVES SMALL_SPLIT
    SMALL_UNFINISHED SMALL_DEAD SMALL_TIMEOUT SMALL_REJECT MORE_PCT FEWER_PCT VAL_PASS VAL_TS_PASS VAL_MUTUAL VAL_MOVES VAL_TS_MOVES
    TINY_PASS TINY_MUTUAL TINY_MOVES TINY_TS_MOVES TINY_SPLIT TINY_DELTA TINY_BASE_SPLIT OVERRIDE_TINY PLAN_TIME_S TS_TIME_S PEAK_GB
    DEMO_STOWLM_MOVES DEMO_TWOSTEP_MOVES DEMO_LB_MOVES SHOWCASE_LINE HYBRID_MOVES TS_POLISH_MOVES REPLAN_LINE SPARK_EXPLAIN_NOTE
    REPO_URL FALLBACK_SMALL VAL_DELTA VAL_UNFINISHED VAL_REACHED TINY_TS_PASS OVERRIDE_SMALL INSTR_HELDOUT INSTR_TESTSET""".split())


def allowed_keys(scripts=SCRIPTS) -> set[str]:
    keys: set[str] = set(FILLED_IN_PLACE)
    for path in scripts:
        keys |= set(re.findall(r"\{\{([A-Z0-9_]+)\}\}", path.read_text()))
    return keys


def v5_keys(script: Path = SCRIPT_V5) -> set[str]:
    return (set(re.findall(r"\{\{NUMBER: ([^{}]+)\}\}", script.read_text())) - {"…"}) | V5_TEXT_KEYS  # the header names the pattern


def v6_flags(script: Path = SCRIPT_V6) -> set[str]:
    return set(re.findall(r"\[\[IF ([a-z-]+):", script.read_text()))


def v6_keys(script: Path = SCRIPT_V6) -> set[str]:
    return (set(re.findall(r"\{\{([a-z_]+)\}\}", script.read_text())) - {"name"}) | v6_flags(script) | {"hero_gauges_line"}  # the header names the pattern


def validate_v6(values: dict, allowed: set[str], flags: set[str]) -> dict:
    if not isinstance(values, dict):
        raise ValueError("fill-v6.json must be one JSON object")
    out = {}
    for key, value in sorted(values.items()):
        if key not in allowed:
            raise ValueError(f"{key}: not a name in script-v6.md")
        if key in flags:
            if value is not True:
                raise ValueError(f"{key}: an [[IF]] name is true or absent")
        else:
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ValueError(f"{key}: value must be a string or a number")
            text = str(value)
            if "{{" in text or "PENDING" in text or not text.strip():
                raise ValueError(f"{key}: still a placeholder")
            if key not in V6_TEXT_KEYS and not V5_NUMBER.match(text):
                raise ValueError(f"{key}: must be a number, got {text!r}")
        out[key] = value
    return out


def validate_v5(values: dict, allowed: set[str]) -> dict:
    if not isinstance(values, dict):
        raise ValueError("fill-v5.json must be one JSON object")
    out = {}
    for key, value in sorted(values.items()):
        if key not in allowed:
            raise ValueError(f"{key}: not a placeholder in script.md")
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError(f"{key}: value must be a string or a number")
        text = str(value)
        if "{{" in text or "PENDING" in text or not text.strip():
            raise ValueError(f"{key}: still a placeholder")
        if key not in V5_TEXT_KEYS and not V5_NUMBER.match(text):
            raise ValueError(f"{key}: must be a number, got {text!r}")
        out[key] = value
    return out


def validate(values: dict, allowed: set[str]) -> dict:
    if not isinstance(values, dict):
        raise ValueError("fill.json must be one JSON object")
    out = {}
    for key, value in sorted(values.items()):
        if key not in allowed:
            raise ValueError(f"{key}: not a placeholder in any variant script")
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError(f"{key}: value must be a string or a number")
        text = str(value)
        if "{{" in text or "PENDING" in text:
            raise ValueError(f"{key}: still a placeholder")
        if NUMERIC.search(key) and not PLAIN_NUMBER.match(text):
            raise ValueError(f"{key}: must be a plain number, got {text!r}")
        out[key] = value
    return out


def render(values: dict) -> str:
    body = json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True)
    return ("/* GENERATED by experiments/mvp/ui/sync_fill.py from experiments/mvp/video/fill.json. Do not edit. */\n"
            f"window.STAGE_FILL = {body};\n")


def render_v5(values: dict) -> str:
    body = json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True)
    return ("/* GENERATED by experiments/mvp/ui/sync_fill.py from experiments/mvp/video/fill-v5.json. Do not edit. */\n"
            f"window.STAGE_FILL_V5 = {body};\n")


def render_v6(values: dict) -> str:
    body = json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True)
    return ("/* GENERATED by experiments/mvp/ui/sync_fill.py from experiments/mvp/video/fill-v6.json. Do not edit. */\n"
            f"window.STAGE_FILL_V6 = {body};\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fill", type=Path, default=FILL_JSON)
    ap.add_argument("--out", type=Path, default=OUTPUT)
    ap.add_argument("--fill-v5", type=Path, default=FILL_V5_JSON)
    ap.add_argument("--out-v5", type=Path, default=OUTPUT_V5)
    ap.add_argument("--fill-v6", type=Path, default=FILL_V6_JSON)
    ap.add_argument("--out-v6", type=Path, default=OUTPUT_V6)
    args = ap.parse_args(argv)
    try:
        values = json.loads(args.fill.read_text()) if args.fill.is_file() else {}
        clean = validate(values, allowed_keys())
        values_v5 = json.loads(args.fill_v5.read_text()) if args.fill_v5.is_file() else {}
        clean_v5 = validate_v5(values_v5, v5_keys())
        values_v6 = json.loads(args.fill_v6.read_text()) if args.fill_v6.is_file() else {}
        clean_v6 = validate_v6(values_v6, v6_keys(), v6_flags())
    except ValueError as exc:
        print(f"sync_fill: {exc}; {args.out}, {args.out_v5} and {args.out_v6} left unchanged", file=sys.stderr)
        return 1
    args.out.write_text(render(clean))
    args.out_v5.write_text(render_v5(clean_v5))
    args.out_v6.write_text(render_v6(clean_v6))
    print(f"{args.out}: {len(clean)} value(s); {args.out_v5}: {len(clean_v5)} value(s); {args.out_v6}: {len(clean_v6)} value(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
