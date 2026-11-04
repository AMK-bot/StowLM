"""The 25 pinned break-it drags as one static page, for anyone to take the dare with no server (logic review 4, F12).

    uv run python experiments/mvp/ui/replay/build.py      # writes experiments/mvp/ui/replay/index.html

Reads the pinned drag cache (`experiments/mvp/pipeline/sandbox/fixtures/drag-cache.json`) only if its bytes match the pin
in `app/v6_hero.json`, and words each verdict with the app's own `app.v6_sandbox.outcome`, so the page and the live chapter
can't disagree. Every verdict is recorded: the page says "Recorded verdict" and the date of its check, never "checked now".
No script, no network: a <details> per drag holds its verdict until it's opened. Hosting it publicly is the user's call.
"""
from __future__ import annotations

import hashlib
import json
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT)]
from app import v6, v6_build, v6_sandbox  # noqa: E402

CACHE = ROOT / "experiments/mvp/pipeline/sandbox/fixtures/drag-cache.json"
MANIFEST = ROOT / "experiments/mvp/pipeline/freeze/manifest-pipeline.json"  # frozen pipeline v4, the demo and package build (D313)
OUT = Path(__file__).with_name("index.html")
LABEL = "Recorded verdict"


def slot_words(slot: str) -> str:
    s = str(slot)
    return f"bay {int(s[:3])}, row {s[3:5]}, tier {s[5:7]}" if len(s) == 7 and s.isdigit() else s


def drags(cache: dict) -> list[dict]:
    rows = []
    for entry in sorted(cache["entries"].values(), key=lambda e: e["request_index"]):
        fast, full = v6_sandbox.outcome(entry.get("fast") or {}), v6_sandbox.outcome(entry.get("full") or {})
        rows.append({"n": entry["request_index"] + 1, "box": entry["box_id"], "slot": entry["target_slot"], "where": slot_words(entry["target_slot"]),
                     "fast": fast, "full": full, "passes": full["kind"] == "passes", "lifts": v6_sandbox.lifts_line(entry.get("full") or {}),
                     "checked_on": v6_sandbox.checked_on(entry.get("checked_utc")), "provenance": entry.get("provenance")})
    return rows


def load(cache_path: Path = CACHE, pin: str | None = None) -> tuple[dict, str]:
    data = cache_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    pin = pin if pin is not None else v6_sandbox._config().get("drag_cache_sha256")
    if digest != pin:
        raise SystemExit(f"not built: {cache_path.name} is {digest[:12]}, app/v6_hero.json pins {str(pin)[:12]}")
    return json.loads(data), digest


def build_line(digest: str, manifest_path: Path = MANIFEST) -> str:
    """Which build this page is (logic14): a static page from the pinned cache, and whether the frozen v4 manifest pins those same bytes."""
    try:
        raw = manifest_path.read_bytes()
        pinned = json.loads(raw)["files"].get(str(CACHE.relative_to(ROOT)))
    except (OSError, ValueError, KeyError, AttributeError):
        pinned = None
    if pinned != digest:
        return v6_build.DEVELOPMENT
    return (f"Static page, built on the development tree from the pinned drag cache; frozen pipeline v4 "
            f"({hashlib.sha256(raw).hexdigest()[:12]}) pins the same cache byte for byte")


def changes(demo: dict) -> list[str]:
    """Each listed cargo change in the sandbox source's own words: the first sentence of its plain_words."""
    out = []
    for m in demo.get("modifications") or []:
        words = str((m or {}).get("plain_words") or "").strip()
        if words:
            out.append(words.split(". ")[0].rstrip(".") + ".")
    return out


def base_plan(demo: dict) -> dict:
    """The plan the drags edit: the sandbox source's ``derived_from`` lattice result, its date and its instruction rules. Unreadable: {}."""
    try:
        data = json.loads((ROOT / demo["derived_from"]).read_text())
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    day = str(data.get("computed_utc") or "")[:10]
    return {"label": f"Stored plan, computed on {day}" if day else None, "rules": data.get("canonical_rules")}


def closed_bays() -> set[int]:
    """The hero instruction's closed bay, if it closes one: the drag plan predates it, so a drop there is checked without that rule."""
    rule = (v6.hero().get("instruction") or {}).get("rule") or {}
    c = rule.get("constraint") or {}
    return {c["bay"]} if c.get("type") == "close_bay" and isinstance(c.get("bay"), int) else set()


def no_rule_note(r: dict, plan: dict, bays: set[int]) -> str | None:
    """Beside a drop into the closed bay that the full checker didn't refuse for it: the plan these drags use carries no instruction rule."""
    s = str(r["slot"])
    if plan.get("rules") != [] or not (len(s) == 7 and s.isdigit() and int(s[:3]) in bays) or r["full"].get("rule_id", "").startswith("instruction"):
        return None
    bay = int(s[:3])
    return (f"Why bay {bay} isn't refused here: these drags edit the plan from before the instruction to close bay {bay}, and that plan "
            f"carries no close-bay rule, so the checker has no rule against bay {bay}. Its verdict covers the rules that plan carries.")


def page(cache: dict, digest: str, demo: dict | None = None) -> str:
    demo = demo or {}
    rows, e = drags(cache), escape
    tag = v6_sandbox.CHALLENGE_TAG  # D311: the fixture's own label overstated the change; its disclosure stays in the footer
    plan, bays = base_plan(demo), (closed_bays() if demo else set())
    passing = sum(1 for r in rows if r["passes"])
    cards = []
    for r in rows:
        cls = "ok" if r["passes"] else "no"
        rule = f'<code>{e(r["full"]["rule_id"])}</code>' if r["full"].get("rule_id") else ""
        why = no_rule_note(r, plan, bays)
        cards.append(f"""<details class="drag {cls}"><summary><b>{r['n']:02d}</b><span>Drag <code>{e(r['box'])}</code> to <code>{e(r['slot'])}</code></span><em>{e(r['where'])}</em></summary>
<div class="v"><p class="fast"><small>Fast screen</small>{e(r['fast']['title'])}</p>
<p class="full"><small>Full checker</small><strong>{e(r['full']['title'])}</strong> {e(r['full']['plain'])} {rule}</p>
{f'<p class="lifts">{e(r["lifts"])}</p>' if r['lifts'] else ''}{f'<p class="why">{e(why)}</p>' if why else ''}<p class="tag">{LABEL} · {e(r['checked_on'] or '')}</p></div></details>""")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Break It Replay</title>
<style>
:root {{ --bg: #070e1b; --panel: #0d1a2e; --line: #1c304f; --ink: #dfe8f6; --dim: #8ba0c0; --amber: #ffa53a; --green: #47f5a0; --red: #ff5a5a; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 980px; margin: 0 auto; padding: 28px 16px 48px; }}
h1 {{ font-size: 26px; margin: 0 0 6px; }} h1 b {{ color: var(--amber); }}
.lede {{ color: var(--dim); margin: 0 0 14px; }}
.pills {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 20px; }}
.pills span {{ border: 1px dashed var(--amber); color: #ffc670; border-radius: 999px; padding: 3px 10px; font-size: 12.5px; }}
.drag {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; margin: 0 0 8px; }}
summary {{ cursor: pointer; display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: baseline; padding: 11px 14px; list-style: none; }}
summary::-webkit-details-marker {{ display: none; }}
summary b {{ color: var(--dim); font-variant-numeric: tabular-nums; }} summary em {{ color: var(--dim); font-style: normal; font-size: 13px; }}
summary::after {{ content: "Drop it"; margin-left: auto; color: var(--amber); font-size: 13px; }}
details[open] summary::after {{ content: ""; }}
code {{ font: 13px ui-monospace, SFMono-Regular, Menlo, monospace; }}
.v {{ padding: 0 14px 12px; border-top: 1px solid var(--line); }}
.v p {{ margin: 10px 0 0; }} .v small {{ display: block; color: var(--dim); font-size: 11.5px; text-transform: uppercase; letter-spacing: .08em; }}
.ok .full strong {{ color: var(--green); }} .no .full strong {{ color: var(--red); }}
.lifts, .tag {{ color: var(--dim); font-size: 13px; }}
.why {{ color: #ffc670; font-size: 13.5px; border-left: 2px solid var(--amber); padding-left: 8px; }}
footer {{ color: var(--dim); font-size: 12.5px; margin-top: 22px; overflow-wrap: anywhere; }}
</style></head>
<body><main>
<h1>Break it: <b>{len(rows)}</b> pinned drags</h1>
<p class="lede">Pick a box and a slot. Each drop shows what the fast screen said, then what the full checker ruled. Software decides. One box, no replan.{' Every drag edits the stored plan from before any instruction, so no instruction rule applies here.' if plan.get("rules") == [] else ''}</p>
<div class="pills"><span>{LABEL}</span>{f'<span>{e(plan["label"])}</span>' if plan.get("label") else ''}<span>{e(tag)}</span><span>Synthetic ships and cargo, simulated port</span></div>
{''.join(cards)}
<footer><p>{e(demo.get("label_disclosure") or "")}</p>
{"".join(f"<p>{e(c)}</p>" for c in changes(demo))}
<p>{passing} of {len(rows)} pinned drops pass as edited. These are drops we chose to show each kind of answer, not a rate.</p>
<p>Each verdict is a recording from the pinned cache (sha256 {digest[:16]}…), replayed with no checker running. {e(build_line(digest))}.</p></footer>
</main></body></html>
"""


def main() -> None:
    cache, digest = load()
    OUT.write_text(page(cache, digest, _demo_source()))
    print(f"wrote {OUT.relative_to(ROOT)}: {cache['entries_count']} drags")


def _demo_source() -> dict:
    """The disclosure that goes with these drags: the hero sandbox source the cache was recorded on."""
    try:
        return json.loads((ROOT / v6_sandbox._config()["sandbox_source"]).read_text())
    except (OSError, ValueError, KeyError):
        return {}


if __name__ == "__main__":
    main()
