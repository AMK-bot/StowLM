"""Task 10.2: every number in experiments/mvp/video/fill.json recomputes from the measured per-map files.

SUMMARY's paired column reads better/equal/worse, the results card's reads fewer/more/equal and the hybrid table's
fewer/equal/more: the column order is pinned here so a split can't be copied across in the wrong order."""
from __future__ import annotations

import json
import statistics as st
from pathlib import Path

import pytest

from app.tests.report_helpers import historical

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "experiments/mvp/runs"
T91 = RUNS / "test-91"
FILL = ROOT / "experiments/mvp/video/fill.json"

pytestmark = pytest.mark.skipif(not (T91 / "summary.json").is_file() or not FILL.is_file(), reason="9.1 run not on disk")
# The per-map files are the v5 film's audit trail; the v6 demo never reads them, and the curated export ships only test-91's results.
PER_MAP = historical("experiments/mvp/runs/test-91/stowlm/instances/*.json", "experiments/mvp/runs/test-91/stowlm/instances/*/outcome.json",
                     "experiments/mvp/runs/test-91/stowlm/instances/*/report.json", "experiments/mvp/runs/test-91/twostep/*.json")


def fill() -> dict:
    return json.loads(FILL.read_text())


def stowlm_rows(rung: str) -> dict:
    rows = {}
    for path in (T91 / "stowlm/instances").glob("*.json"):
        rec = json.loads(path.read_text())
        if rec["rung"] != rung:
            continue
        folder = path.parent / rec["instance_id"]
        outcome = json.loads((folder / "outcome.json").read_text())["outcome"]
        report = json.loads((folder / "report.json").read_text()) or {}
        rows[rec["instance_id"]] = {"outcome": outcome, "passed": report.get("passed"), "s": rec["pipeline_s"],
                                    "moves": (report.get("count") or {}).get("weighted_nonproductive")}
    return rows


def twostep_rows() -> dict:
    return {r["instance_id"]: r for r in (json.loads(p.read_text()) for p in (T91 / "twostep").glob("*.json"))}


def mutual(rung: str) -> list:
    two = twostep_rows()
    return [(r["moves"], two[i]["weighted_nonproductive"]) for i, r in stowlm_rows(rung).items()
            if r["outcome"] == "own pass" and two[i].get("passed") is True]


def split(pairs) -> str:
    fewer, more = sum(a < b for a, b in pairs), sum(a > b for a, b in pairs)
    return f"{fewer} / {more} / {len(pairs) - fewer - more}"


@PER_MAP
def test_card_splits_are_fewer_more_equal_not_summary_order():
    f, summary = fill(), json.loads((T91 / "summary.json").read_text())
    row = summary["rows"]["StowLM own plan (pass@1, greedy)"]
    for rung, key in (("small", "SMALL_SPLIT"), ("tiny", "TINY_SPLIT")):
        paired = row[rung]["paired_vs_twostep"]
        assert f[key] == f"{paired['better']} / {paired['worse']} / {paired['equal']}" == split(mutual(rung))
    assert f["SMALL_SPLIT"] == "2 / 11 / 1" and f["TINY_SPLIT"] == "2 / 7 / 6"  # 9.1's table prints 2/1/11 and 2/6/7


@PER_MAP
def test_small_and_tiny_values_recompute_per_map():
    f = fill()
    rows, pairs = stowlm_rows("small"), mutual("small")
    own, two = sum(a for a, _ in pairs), sum(b for _, b in pairs)
    assert len(rows) == 15 and f["SMALL_PASS"] == str(sum(r["outcome"] == "own pass" for r in rows.values()))
    assert f["SMALL_TS_PASS"] == str(sum(r.get("passed") is True for i, r in twostep_rows().items() if i in rows))
    assert f["SMALL_MUTUAL"] == str(len(pairs)) == "14"
    assert (f["SMALL_MOVES"], f["SMALL_TS_MOVES"]) == (f"{own / 14:.2f}", f"{two / 14:.2f}")
    assert f["MORE_PCT"] == f"{(own / two - 1) * 100:.0f}" and f["FEWER_PCT"] == f"{(1 - two / own) * 100:.0f}"
    assert f["MORE_PCT"] != f["FEWER_PCT"]
    tiny = mutual("tiny")
    assert f["TINY_MUTUAL"] == str(len(tiny)) == "15" and f["TINY_PASS"] == "15"
    total, ref = sum(a for a, _ in tiny), sum(b for _, b in tiny)
    assert (f["TINY_MOVES"], f["TINY_TS_MOVES"], f["TINY_DELTA"]) == (f"{total / 15:.2f}", f"{ref / 15:.2f}", f"+{(total - ref) / 15:.2f}")


@PER_MAP
def test_unfinished_map_is_the_same_dead_end_on_both_sides_and_nothing_was_rejected():
    f, rows, two = fill(), stowlm_rows("small"), twostep_rows()
    not_passed = [i for i, r in rows.items() if r["outcome"] != "own pass"]
    assert not_passed == ["sim.small.28100011"] and rows[not_passed[0]]["outcome"] == "dead end"
    assert two["sim.small.28100011"]["outcome"] == "dead end"
    reached = [r for r in rows.values() if r["passed"] is not None]
    assert (f["SMALL_UNFINISHED"], f["SMALL_DEAD"], f["SMALL_TIMEOUT"]) == ("1", "1", "0")
    assert f["SMALL_REJECT"].startswith(f"{sum(r['passed'] is False for r in reached)} of the {len(reached)} plans") and len(reached) == 14


@PER_MAP
def test_hybrid_is_equal_polish_and_times_are_medians_on_the_same_maps():
    f, summary = fill(), json.loads((T91 / "summary.json").read_text())
    hybrid = summary["hybrid_paired"]["small"]
    assert (f["HYBRID_MOVES"], f["TS_POLISH_MOVES"]) == (f"{hybrid['stowlm_mean']:.2f}", f"{hybrid['twostep_mean']:.2f}")
    rows, two = stowlm_rows("small"), twostep_rows()
    assert st.median(r["s"] for r in rows.values()) == 26.25 and f["PLAN_TIME_S"] == "26.2"
    assert st.median(two[i]["s"] for i in rows) == 36.88 and f["TS_TIME_S"] == "36.9"


@PER_MAP
@historical("experiments/mvp/runs/test-91/stowlm/instances/*/trace.jsonl", "experiments/mvp/runs/base-mask/tiny-securing-T0/instances/*.json")
def test_override_and_untuned_split_are_measured_and_clear_the_branches():
    f, rows, overridden, positions = fill(), stowlm_rows("tiny"), 0, 0
    for iid in rows:
        for line in (T91 / "stowlm/instances" / iid / "trace.jsonl").read_text().splitlines():
            for d in json.loads(line).get("decisions") or []:
                positions, overridden = positions + 1, overridden + bool(d.get("overridden"))
    assert (overridden, positions) == (7, 224) and f["OVERRIDE_TINY"] == "3 %"
    base = {r["instance_id"]: r["objective"] for r in (json.loads(p.read_text()) for p in (RUNS / "base-mask/tiny-securing-T0/instances").glob("*.json"))}
    pairs = [(r["moves"], base[i]) for i, r in rows.items()]
    assert split(pairs) == "8 / 1 / 6" and f["TINY_BASE_SPLIT"].startswith("StowLM fewer on 8, more on 1, equal on 6")


@historical(*(f"experiments/mvp/runs/{run}/instances/random.small.29000201.0.7/result.json" for run in ("stowlm-showcase", "stowlm-instruction-dg", "stowlm-instruction-binding")))
def test_demo_values_are_the_hero_and_the_superseded_run_stays_unshown():
    f = fill()
    verify = json.loads((RUNS / "stowlm-showcase/verify.json").read_text())["rows"]
    hero = verify["random.small.29000201.0.7"]
    assert hero["fresh_check_passed"] is True and f["DEMO_STOWLM_MOVES"] == f"{hero['weighted_lifts']:.0f}" == "67"
    assert f["DEMO_TWOSTEP_MOVES"] == "37" and f["DEMO_LB_MOVES"] == "15"
    # D166: StowLM's plan under the sentence is the plan without it, and Spark did not parse that sentence.
    plan = lambda run: json.loads((RUNS / run / "instances/random.small.29000201.0.7/result.json").read_text())["plan"]["moves"]  # noqa: E731
    assert plan("stowlm-showcase") == plan("stowlm-instruction-dg")
    # D170: the binding run replaces it, in the allowed words only.
    assert plan("stowlm-showcase") != plan("stowlm-instruction-binding")
    assert f["REPLAN_LINE"] == ("StowLM replanned under a plain-language instruction parsed by Muse Spark, and the checker "
                                "passed it (one pre-registered call)")


def test_page_data_is_in_sync_with_fill_json():
    text = (ROOT / "app/static/stage-fill.js").read_text()
    assert json.loads(text[text.index("{"): text.rindex("}") + 1]) == fill()
    assert "{{" not in json.dumps(fill()) and "REPO_URL" not in fill()
