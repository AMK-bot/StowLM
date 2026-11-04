"""Ship-time accounting helpers; default report uses v5 sourced small-map lifts only.

Run: .venv/bin/python experiments/impact-estimate/ship_time.py [--scenario] [--json PATH]
No measured berth-time or planner-time saving is available for these maps, so the
default report does not estimate hours or dollars. The older synthetic accounting
is available only with an explicit --saved-pct value and prints an archival warning.
Its prices, inventories and critical-path fractions are assumptions, not current
StowLM results. Functions remain for signed-accounting regression tests.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from math import isfinite
from pathlib import Path

_spec = spec_from_file_location("stowlm_impact_layer1", Path(__file__).with_name("impact.py"))
impact = module_from_spec(_spec)
sys.modules[_spec.name] = impact
_spec.loader.exec_module(impact)

# Public anchors (read 29 Oct 2026).
CHARTER_FEEDER_AVG = "https://container-news.com/contships-announces-q3-and-nine-month-results/"
"""Contships (900–2,000 TEU feeder owner), 13 Nov 2025: average net time-charter rate $14,333/day for 9M 2025;
Q4 2025 guidance about $15,700/day gross."""
CHARTER_INDEX_1700 = "https://en.seanews.ir/wp-content/uploads/2025/02/7.pdf"
"""New ConTex commentary issued 6 Feb 2025 (week 06; VHBS e.V. index, via SeaNews PDF): 1,100 TEU $14,795/day and
1,700 TEU $26,150/day, 12-month periods. Index assessments, not fleet averages. Text extracted from the public PDF."""
CPPI = "https://openknowledge.worldbank.org/bitstreams/3f7344ae-49a3-4aed-83a8-c480a798d24c/download"
"""World Bank Container Port Performance Index: port hours and gross crane productivity definitions."""
PLANNING_TIME = "https://ningbo.chinadaily.com.cn/2025-06/20/c_1102058.htm"
"""China Daily (Ningbo), 20 Jun 2025: manual stowage planning 'could take over three hours per vessel'; the port's
AI system plans '3,000 units in just 20 minutes' and cuts reshuffling by 'over 30 moves per plan on average'.
A port's own announcement about a large terminal: an anchor, not a feeder measurement."""

HOURS_PER_DAY = 24


@dataclass(frozen=True)
class ShipTimeScenario:
    name: str
    charter_usd_per_day: float
    yard_to_berth_fraction: float
    manual_planning_hours: float

    def __post_init__(self) -> None:
        for name in ("charter_usd_per_day", "manual_planning_hours"):
            value = getattr(self, name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if not isfinite(self.yard_to_berth_fraction) or not 0 <= self.yard_to_berth_fraction <= 1:
            raise ValueError("yard_to_berth_fraction must be in [0, 1]")


# low / mid / high, paired with impact.SCENARIOS of the same name.
SHIP_SCENARIOS = (
    # SOURCE: Contships 9M 2025 average net TC rate (900–2,000 TEU).
    # ASSUMPTION: no yard work reaches the ship's critical path.
    ShipTimeScenario("low", 14_333, 0.0, 3.0),
    # SOURCE: Contships Q4 2025 gross TC guidance.
    # ASSUMPTION: 10 % of freed yard-equipment time was holding up the cranes (expert Q5: yard digging "impacts cargo
    # operation"; no measured share).
    ShipTimeScenario("mid", 15_700, 0.10, 3.0),
    # SOURCE: New ConTex 1,700 TEU 12-month index, 6 Feb 2025.
    # ASSUMPTION: 25 % of yard time on the critical path.
    ShipTimeScenario("high", 26_150, 0.25, 3.0),
)
# manual_planning_hours = 3.0 for all three cases. SOURCE: Ningbo ">3 hours per vessel" (large terminal).
# APPROXIMATION: transferred to a feeder call as an order of magnitude; reported as hours, never monetised (no public
# planner wage for Thailand was found).

SERVICE_CALLS_PER_YEAR = 104  # impact.py: one weekly service, two Laem Chabang calls per loop (public flyer).
PORT_CALLS_PER_YEAR = 9_325   # impact.py: BSAA 2024 Laem Chabang container-vessel calls (scale illustration).


@dataclass(frozen=True)
class ShipTime:
    handling_usd: float
    berth_hours: float
    ship_time_usd: float
    quay_crane_hours: float
    yard_equipment_hours: float

    @property
    def total_usd(self) -> float:
        return self.handling_usd + self.ship_time_usd


def ship_time(layer1: impact.Impact, scenario: ShipTimeScenario) -> ShipTime:
    """Convert a first-layer Impact (illustrative or measured) into ship berth-hours and their hire value.

    Berth hours are the first layer's critical-path quay hours, plus a labelled share of the freed yard time.
    APPROXIMATION: no crane or yard schedule is simulated; both shares are explicit assumptions.
    Signs follow the input: a candidate that adds work gives negative hours and dollars.
    """
    berth = layer1.berth_hours + layer1.yard_equipment_hours * scenario.yard_to_berth_fraction
    return ShipTime(
        handling_usd=layer1.total_usd,
        berth_hours=berth,
        ship_time_usd=berth * scenario.charter_usd_per_day / HOURS_PER_DAY,
        quay_crane_hours=layer1.quay_crane_hours,
        yard_equipment_hours=layer1.yard_equipment_hours,
    )


def table(saved_fraction: float) -> list[tuple[str, ShipTime]]:
    """Each paired low/mid/high case at the given avoided fraction (illustrative path)."""
    return [(s.name, ship_time(impact.estimate(base, saved_fraction), s))
            for base, s in zip(impact.SCENARIOS, SHIP_SCENARIOS, strict=True)]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--saved-pct", type=float, default=None,
                        help="ARCHIVAL synthetic cost sensitivity only; not current impact results")
    parser.add_argument("--scenario", action="store_true", help="explicit economic/time EXTRAPOLATION from 12–22-box maps")
    parser.add_argument("--json", type=Path, help="write measured layer plus optional scenario layer")
    args = parser.parse_args(argv)
    if args.saved_pct is not None and (args.scenario or args.json):
        parser.error("archival --saved-pct cannot be combined with --scenario or --json")
    if args.scenario:
        forwarded = ["--scenario"] + (["--json", str(args.json)] if args.json else [])
        impact.main(forwarded)
        return
    if args.saved_pct is None:
        impact.main(["--json", str(args.json)] if args.json else [])
        print("SHIP/PLANNER TIME: not estimated; no measured replan-time saving or berth-time change.")
        print(f"Historical context only (full-vessel manual planning, not replanning or local feeder data): {PLANNING_TIME}")
        return
    fraction = args.saved_pct / 100
    print("ARCHIVAL SYNTHETIC COST SENSITIVITY — NOT V5 IMPACT, NOT A MEASURED OR SOURCED LOCAL ESTIMATE")
    print("All move inventories/prices/timing shares below retain their old assumptions; no current-port/later-port split.")
    print(f"avoided non-productive moves: {args.saved_pct:g}%")
    print(f"charter anchors: {CHARTER_FEEDER_AVG} ; {CHARTER_INDEX_1700}")
    print(f"planning-time anchor: {PLANNING_TIME}")
    print("case | handling USD/call | berth h/call | ship-time USD/call | total USD/call | "
          f"total USD/yr ({SERVICE_CALLS_PER_YEAR} calls) | total USD/yr ({PORT_CALLS_PER_YEAR:,} calls, illustration)")
    for name, r in table(fraction):
        print(f"{name} | {r.handling_usd:,.0f} | {r.berth_hours:.3f} | {r.ship_time_usd:,.0f} | {r.total_usd:,.0f} | "
              f"{r.total_usd * SERVICE_CALLS_PER_YEAR:,.0f} | {r.total_usd * PORT_CALLS_PER_YEAR:,.0f}")
    print("Planner time per replan and time saved: no source; not estimated or monetised.")


if __name__ == "__main__":
    main()
