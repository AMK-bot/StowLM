"""v5 impact sensitivity: small-map lifts, with current-port credit separated from projections.

Run: .venv/bin/python experiments/impact-estimate/impact.py [--scenario] [--json PATH]
Only the standard library is needed. No planner, GPU or worker pool is run.
The legacy pricing helpers below remain for accounting tests; the default report
uses sourced move counts only and does not estimate dollars or elapsed time.
"""

import argparse
import json
from pathlib import Path

from dataclasses import asdict, dataclass, fields
from math import isfinite


PRODUCTIVITY = "https://www.ppiaf.org/sites/ppiaf.org/files/documents/toolkits/Portoolkit/Toolkit/module2/port_dynamics.html"
RESTOW = "https://www.one-line.com/ecom/CUP_HOM_3108.do?amdtSeqParam=9&ctgId=EEN_100006&f_cmd=&mnuId=EEN_L00043&pagerows=&prntMnuId=EEN_100006&searchWordParam=&sessLocale=en&trfNoParam=202&trfPfxCdParam=ONEY&trfRuleNoParam=201.001"
YARD_BENCHMARK = "https://arxiv.org/html/2206.12303"
YARD_TARIFF = "https://www.esco.co.th/escotariff/consignee.html"
SERVICE = "https://www.cnc-line.com/ebusiness/schedules/line-services/flyer/RBHCNC"
PORT_CALLS = "https://thaibsaa.com/images/documents/Annual24-25_Final.pdf"
HATCH_TARIFF = "https://www.adaniports.com/-/media/project/ports/portsandterminals/mundra-documents/tariff/aictpl_ct3-tariff-booklet-wef-16th-june-2026.pdf"


@dataclass(frozen=True)
class Scenario:
    name: str
    retrieved_boxes: float
    yard_rehandles_per_box: float
    restowed_boxes: float
    yard_usd_per_move: float
    quay_usd_per_move: float
    yard_minutes_per_move: float
    quay_minutes_per_move: float
    berth_critical_path_fraction: float
    # D85: hatch-cover lifts (now + projected later-port) priced like one quay-crane move each.
    hatch_cover_lifts: float = 0
    hatch_usd_per_move: float = 0
    hatch_minutes_per_move: float = 0

    def __post_init__(self) -> None:
        for field in fields(self):
            if field.name == "name":
                continue
            value = getattr(self, field.name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"{field.name} must be finite and nonnegative")
        if self.berth_critical_path_fraction > 1:
            raise ValueError("berth_critical_path_fraction must be in [0, 1]")

    @property
    def yard_rehandles(self) -> float:
        return self.retrieved_boxes * self.yard_rehandles_per_box


# ARCHIVAL HELPERS ONLY: not used in the v5 default report.
# APPROXIMATION: synthetic per-call move inventories until expert question 8;
# literature establishes the relocation problem, not a Laem Chabang base rate.
SCENARIOS = (
    Scenario("low", 500, 0.10, 2, 10, 50, 2, 2, 0, 8, 50, 2),
    Scenario("mid", 500, 0.30, 5, 20, 75, 3, 2.4, 0.5, 16, 75, 2.4),
    Scenario("high", 500, 0.60, 10, 30, 100, 4, 3, 1, 32, 100, 3),
)

# Via-quay restows only; a direct ship-to-ship-slot transfer is one lift and
# must be counted separately if C1 contains it.
# D85 (leader, open for veto): a hatch-cover lift is one quay-crane move (the crane lifts the cover off or back on).
# ASSUMPTION: priced and timed like one quay-crane move. Public anchor: a container terminal tariff charges
# USD 140 per hatch-cover handling with landing on the quay (USD 70 without), i.e. about USD 70 per lift if the
# charge covers off + on, close to the mid USD 75 per quay move. This is a retail charge at an Indian terminal, not a
# Laem Chabang resource cost.
# ASSUMPTION: the baseline non-productive hatch-cover lifts per call (8 / 16 / 32: re-opening cycles plus projected
# later-port cover lifts caused by deck boxes over hold cargo) are synthetic, like the other inventories (expert Q8).
QUAY_MOVES_PER_RESTOW = 2
ILLUSTRATIVE_AVOIDED_FRACTIONS = (0, 0.05, 0.10, 0.20)
WEEKS_PER_YEAR = 52
CALL_STREAMS_PER_LOOP = 2
PORT_CONTAINER_CALLS = 9_325

INPUT_NOTES = {
    "retrieved_boxes": ("boxes/call", "ASSUMPTION: yard boxes retrieved for loading; not TEU or vessel capacity; expert Q8 pending"),
    "yard_rehandles_per_box": ("extra yard moves/retrieved box", "ASSUMPTION: synthetic density sensitivity, not a measured local rate; expert Q8 pending"),
    "restowed_boxes": ("via-quay restow events/call", "ASSUMPTION: events attributable to a loading decision over its downstream rotation; count once, not again at each downstream call; expert Q8 pending"),
    "yard_usd_per_move": ("USD/extra yard move", "ASSUMPTION: incremental resource-cost proxy, not customer charge; expert Q8 pending"),
    "quay_usd_per_move": ("USD/extra quay-crane move", "ASSUMPTION: incremental resource-cost proxy, not customer charge; expert Q8 pending"),
    "yard_minutes_per_move": ("equipment minutes/extra yard move", "ASSUMPTION: yard equipment work, not elapsed vessel delay; expert Q8 pending"),
    "quay_minutes_per_move": ("crane minutes/extra quay move", f"APPROXIMATION: 30/25/20 moves per crane-hour; 25-30 typical gross general anchor in {PRODUCTIVITY}; 20 assumed; not local measurement or measured restow cycle; expert Q8 pending"),
    "berth_critical_path_fraction": ("share of saved quay work becoming berth hours", "ASSUMPTION: 0/0.5/1 timing sensitivity; no crane schedule is simulated; expert Q8 pending"),
    "hatch_cover_lifts": ("non-productive hatch-cover lifts/call, now + projected later-port", "ASSUMPTION: synthetic inventory (D85); expert Q8 pending"),
    "hatch_usd_per_move": ("USD/hatch-cover lift", f"ASSUMPTION (D85): one quay-crane move; anchor USD 140 per cover handling with quay landing (~USD 70/lift), {HATCH_TARIFF} (read 30 Oct 2026; Indian terminal retail tariff)"),
    "hatch_minutes_per_move": ("crane minutes/hatch-cover lift", "ASSUMPTION (D85): same crane cycle as a quay move"),
}


@dataclass(frozen=True)
class Impact:
    yard_moves: float
    quay_moves: float
    yard_usd: float
    quay_usd: float
    yard_equipment_hours: float
    quay_crane_hours: float
    berth_hours: float
    # D85: hatch-cover lifts saved (now + projected later-port) and their value. quay_crane_hours and berth_hours
    # include the hatch lifts' crane time.
    hatch_moves: float = 0
    hatch_usd: float = 0

    @property
    def total_usd(self) -> float:
        return self.yard_usd + self.quay_usd + self.hatch_usd

    @property
    def weighted_lifts(self) -> float:
        """D11 non-productive lifts saved (all weights 1): rehandles + restow lifts + hatch-cover lifts."""
        return self.yard_moves + self.quay_moves + self.hatch_moves


@dataclass(frozen=True)
class MoveCounts:
    """Per-call counts including all preparation, repair and fallback moves.

    Fractional counts may represent averages over matched calls. Yard rehandles, quay-mediated restow events and
    hatch-cover lifts (now plus projected later-port, D85) are priced by this model.
    """

    yard_rehandles: float = 0
    restowed_boxes: float = 0
    hatch_cover_lifts: float = 0

    def __post_init__(self) -> None:
        for field in fields(self):
            if not isfinite(getattr(self, field.name)) or getattr(self, field.name) < 0:
                raise ValueError(f"{field.name} must be finite and nonnegative")


def compare_counts(scenario: Scenario, baseline: MoveCounts, candidate: MoveCounts) -> Impact:
    """Value baseline minus candidate counts; negative values mean added work.

    Both inventories must describe checker-passed plans on matched instances.
    This accounting function does not check feasibility. Baseline and candidate
    counts are explicit, so zero baselines need no division or special handling.
    Scenario inventories are ignored; its prices and timing assumptions apply.
    """
    return _value_moves(
        scenario,
        baseline.yard_rehandles - candidate.yard_rehandles,
        (baseline.restowed_boxes - candidate.restowed_boxes) * QUAY_MOVES_PER_RESTOW,
        baseline.hatch_cover_lifts - candidate.hatch_cover_lifts,
    )


def estimate(
    scenario: Scenario,
    yard_avoided_fraction: float,
    restow_avoided_fraction: float | None = None,
    hatch_avoided_fraction: float | None = None,
) -> Impact:
    """Compute illustrative savings; use compare_counts for measured tradeoffs.

    Fractions describe avoided non-productive moves against the same feasible
    baseline, not an improvement in productive loads/discharges or total moves.
    Omitted fractions default to one illustrative rate: restows to the yard fraction, hatch lifts to the restow fraction.
    """
    if restow_avoided_fraction is None:
        restow_avoided_fraction = yard_avoided_fraction
    if hatch_avoided_fraction is None:
        hatch_avoided_fraction = restow_avoided_fraction  # hatch lifts are quay-crane work, like restows
    for fraction in (yard_avoided_fraction, restow_avoided_fraction, hatch_avoided_fraction):
        if not isfinite(fraction) or not 0 <= fraction <= 1:
            raise ValueError("avoided fractions must be finite and in [0, 1]")
    yard_moves = scenario.yard_rehandles * yard_avoided_fraction
    quay_moves = scenario.restowed_boxes * QUAY_MOVES_PER_RESTOW * restow_avoided_fraction
    hatch_moves = scenario.hatch_cover_lifts * hatch_avoided_fraction
    return _value_moves(scenario, yard_moves, quay_moves, hatch_moves)


def _value_moves(scenario: Scenario, yard_moves: float, quay_moves: float, hatch_moves: float = 0) -> Impact:
    yard_hours = yard_moves * scenario.yard_minutes_per_move / 60
    quay_hours = (quay_moves * scenario.quay_minutes_per_move + hatch_moves * scenario.hatch_minutes_per_move) / 60
    # APPROXIMATION: quay work converts to elapsed berth time only through an
    # explicit critical-path factor. Concurrent crane work cannot be summed as
    # vessel delay. Yard work is not added to berth time without a schedule.
    # Hatch-cover lifts are quay-crane work (D85), so they count in quay crane hours.
    return Impact(
        yard_moves,
        quay_moves,
        yard_moves * scenario.yard_usd_per_move,
        quay_moves * scenario.quay_usd_per_move,
        yard_hours,
        quay_hours,
        quay_hours * scenario.berth_critical_path_fraction,
        hatch_moves,
        hatch_moves * scenario.hatch_usd_per_move,
    )


# Named provisional input: replace the complete input bundle after test v3,
# including its baseline and this-port/later-port decomposition. A percentage
# alone cannot tell us where the avoided work occurs.
BLIND_TWO_STEP_AVOIDED_FRACTION = 0.39
BLIND_TWO_STEP_BASELINE_LIFTS = 23.40
BLIND_MUTUAL_MAPS = 25
MID_PROJECTED_LATER_CREDIT = 0.5  # ASSUMPTION, sensitivity only; not a probability.
REPO_ROOT = Path(__file__).resolve().parents[2]
DECOMPOSITION_AUDIT = "experiments/mvp/frontier/runs/test-0.9/AUDIT.json"
DECOMPOSITION_BASELINE = "experiments/mvp/strategy/move_breakdown.json"


@dataclass(frozen=True)
class SavingsInputs:
    """One replaceable result bundle; split is a cross-cohort assumption until v3."""

    name: str
    baseline_lifts_per_map: float
    avoided_fraction: float
    this_port_share_of_saved_lifts: float
    mutual_maps: int
    provenance: str
    split_status: str

    def __post_init__(self) -> None:
        if not isfinite(self.baseline_lifts_per_map) or self.baseline_lifts_per_map < 0:
            raise ValueError("baseline_lifts_per_map must be finite and nonnegative")
        for key in ("avoided_fraction", "this_port_share_of_saved_lifts"):
            value = getattr(self, key)
            if not isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{key} must be finite and in [0, 1]")
        if not isinstance(self.mutual_maps, int) or self.mutual_maps <= 0:
            raise ValueError("mutual_maps must be a positive integer")


def observed_test_split() -> dict:
    """Reproduce RESULTS.md's 14-map v0.9 kind-of-lift table, with exact counts.

    This is an accounting read of stored checker audits, not a fresh checker run.
    Later restows are already lifts in these artifacts; do not double them again.
    """
    audit = {r["instance_id"]: r for r in json.loads((REPO_ROOT / DECOMPOSITION_AUDIT).read_text())}
    baseline = json.loads((REPO_ROOT / DECOMPOSITION_BASELINE).read_text())
    rows = [(audit[r["instance_id"]], r["twostep"]) for r in baseline
            if r["instance_id"] in audit and audit[r["instance_id"]].get("passed")
            and r["stowlm"]["passed"] and r["twostep"]["passed"]]
    if not rows:
        raise ValueError("no mutually passed maps for the sourced test split")
    now_keys = ("relocations", "restow_now", "hatch_now")
    later_keys = ("restow_later", "hatch_later")
    totals = {}
    for label, index in (("frontier", 0), ("two_step", 1)):
        now = sum(sum(row[index][key] for key in now_keys) for row in rows)
        later = sum(sum(row[index][key] for key in later_keys) for row in rows)
        totals[label] = {"this_port_lifts": now, "projected_later_lifts": later,
                         "total_lifts": now + later}
    now_saved = totals["two_step"]["this_port_lifts"] - totals["frontier"]["this_port_lifts"]
    later_saved = totals["two_step"]["projected_later_lifts"] - totals["frontier"]["projected_later_lifts"]
    if now_saved < 0 or later_saved < 0 or now_saved + later_saved <= 0:
        raise ValueError("this sensitivity requires positive savings with no added-work category")
    return {
        "version": "0.9.0", "maps": len(rows), "sources": [DECOMPOSITION_AUDIT, DECOMPOSITION_BASELINE],
        "instance_ids": sorted(row[0]["instance_id"] for row in rows), "totals": totals,
        "saved_this_port_lifts_per_map": now_saved / len(rows),
        "saved_projected_later_lifts_per_map": later_saved / len(rows),
        "saved_total_lifts_per_map": (now_saved + later_saved) / len(rows),
        "this_port_share_of_saved_lifts": now_saved / (now_saved + later_saved),
    }


def credited_savings(inputs: SavingsInputs, projected_later_credit: float) -> dict:
    """Credit this-port lifts plus an explicit share of projected later lifts.

    The low case credits zero later lifts. Modelled this-port work is still not
    realised savings at a live terminal. No prices or time conversion is applied.
    """
    if not isfinite(projected_later_credit) or not 0 <= projected_later_credit <= 1:
        raise ValueError("projected_later_credit must be finite and in [0, 1]")
    total = inputs.baseline_lifts_per_map * inputs.avoided_fraction
    this_port = total * inputs.this_port_share_of_saved_lifts
    projected = total - this_port
    credited_later = projected * projected_later_credit
    return {
        "projected_later_credit": projected_later_credit,
        "modelled_this_port_saved_lifts_per_map": this_port,
        "projected_later_saved_lifts_per_map": projected,
        "credited_projected_later_lifts_per_map": credited_later,
        "credited_saved_lifts_per_map": this_port + credited_later,
        "credited_reduction_vs_baseline": (inputs.avoided_fraction
                                            * (inputs.this_port_share_of_saved_lifts
                                               + (1 - inputs.this_port_share_of_saved_lifts)
                                               * projected_later_credit)),
    }


def impact_report() -> dict:
    split = observed_test_split()
    inputs = SavingsInputs(
        name="BLIND_TWO_STEP_V0_8_1_PROVISIONAL_REPLACE_WITH_TEST_V3",
        baseline_lifts_per_map=BLIND_TWO_STEP_BASELINE_LIFTS,
        avoided_fraction=BLIND_TWO_STEP_AVOIDED_FRACTION,
        this_port_share_of_saved_lifts=split["this_port_share_of_saved_lifts"],
        mutual_maps=BLIND_MUTUAL_MAPS,
        provenance="docs/headline.md; experiments/mvp/frontier/RESULTS.md blind pooled-30 row",
        split_status="ASSUMPTION: transfer the exact v0.9 14-map test saving split to the blind pooled cohort",
    )
    return {
        "unit": "avoided non-productive lifts per matched small map, not per real vessel call",
        "inputs": asdict(inputs), "observed_test_decomposition_context_only": split,
        "cases": {name: credited_savings(inputs, credit)
                  for name, credit in (("low", 0.0), ("mid", MID_PROJECTED_LATER_CREDIT), ("high", 1.0))},
        "assumptions": [inputs.split_status,
                        "Mid credits 50% of projected later-port savings; sensitivity only, not a probability.",
                        "39% and 23.40 are rounded published inputs; their product is 9.126, not the unrounded paired difference."],
        "limits": ["12–22 boxes, two discharge ports, two hatch covers; no medium/full-ship result.",
                   "This-port savings are modelled, not realised operational savings; later lifts assume no future loads and fixed coordinates.",
                   "Cases are not confidence bounds; blind paired bootstrap reduction interval is 30.3–47.3%.",
                   "No local sourced marginal move costs, replan time saved, or measured berth-time change: dollars and elapsed hours not estimated.",
                   "Replace avoided fraction, baseline, mutual cohort and now/later split together after test v3.",
                   "No port-wide or annual scaling: a small map is not a representative Laem Chabang call."],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", type=Path, help="write inspectable input and result artifact")
    parser.add_argument("--scenario", action="store_true", help="add explicitly assumed economic/time EXTRAPOLATION from 12–22-box maps")
    args = parser.parse_args(argv)
    report = impact_report()
    data = report["inputs"]
    print("V5 SMALL-MAP IMPACT — PROVISIONAL BLIND INPUT; NOT A DEPLOYMENT FORECAST")
    print(f"named input: {data['name']}")
    print(f"baseline {data['baseline_lifts_per_map']:.2f} lifts/map; avoided {data['avoided_fraction']:.0%}; "
          f"{data['mutual_maps']} mutually passed blind maps")
    print(data["split_status"])
    split = report["observed_test_decomposition_context_only"]
    print(f"v0.9 test context ({split['maps']} maps): "
          f"{split['saved_this_port_lifts_per_map']:.6f} saved here + "
          f"{split['saved_projected_later_lifts_per_map']:.6f} projected later = "
          f"{split['saved_total_lifts_per_map']:.6f}; this-port share {split['this_port_share_of_saved_lifts']:.6%}")
    print("case | this-port lifts | credited later lifts | total credited lifts/map | reduction vs blind baseline")
    for name, case in report["cases"].items():
        print(f"{name} | {case['modelled_this_port_saved_lifts_per_map']:.6f} | "
              f"{case['credited_projected_later_lifts_per_map']:.6f} | "
              f"{case['credited_saved_lifts_per_map']:.6f} | "
              f"{case['credited_reduction_vs_baseline']:.6%}")
    for note in report["assumptions"] + report["limits"]:
        print(note)
    if args.scenario:
        from importlib.util import module_from_spec, spec_from_file_location
        spec = spec_from_file_location("stowlm_explicit_scenario", Path(__file__).with_name("scenario.py"))
        scenario = module_from_spec(spec)
        spec.loader.exec_module(scenario)
        report["scenario_layer"] = scenario.scenario_report(report)
        scenario.print_scenario(report["scenario_layer"])
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n")
        print(f"Wrote {args.json}")


if __name__ == "__main__":
    main()
