"""Offline v6 forward-pilot sensitivity. No measured savings are inputs.

The workflow buckets are disjoint: initial document-to-plan work, subsequent
instruction changes, and exceptional rework excluded from both other buckets.
Crane moves are physical lifts, not boxes, TEU, or elapsed berth time.
"""

import argparse
from copy import deepcopy
import json
from math import isfinite
from pathlib import Path
import re


THROUGHPUT_SOURCE = "https://unctadstat.unctad.org/insights/theme/245"
MOVE_SOURCE = (
    "https://www.dpworld.com/india/-/media/project/dpwg/dpwg-tenant/apac/india/"
    "media-files/ports-and-terminals/chennai/tariff/cctl-sor---wef-1st-may-2025.pdf"
    "?rev=ceba2186d9df4e2e928898993b4792ec"
)


def _input(value, unit, note, status="assumption", source=None):
    return {"value": value, "unit": unit, "status": status, "source": source, "note": note}


def _scenario(index, scale=False):
    scenario = {
        "primary_moves_per_call": _input(
            ((500, 1500, 3000) if scale else (100, 150, 195))[index], "physical lifts/call",
            ("Hypothetical total productive load AND discharge lifts/call, one container per lift; "
             "workflow assumptions cover the complete call, not one labor allocation per direction."
             if scale else "Hypothetical primary productive load lifts, one container per lift; "
             "no transfer of old section-level results.")),
        "extra_rehandles_per_primary_move": _input(
            (0.02, 0.05, 0.10)[index], "extra crane lifts/primary lift",
            "Assumed attributable extra lifts, not a measured local rate or share of all moves."),
        "rehandle_reduction": _input(
            (0.10, 0.20, 0.30)[index], "fraction of attributable extra lifts",
            "Hypothetical effect, independent of legacy selected-lift results; may fail to transfer."),
        "move_charge_usd": _input(
            20.86, "USD/extra crane lift",
            "41.72 USD for a 20-foot via-quay restow / two lifts; historical 2025 Indian retail proxy, not marginal resource cost or a local quote.",
            "computed", MOVE_SOURCE),
        "planner_usd_per_hour": _input(
            (20, 30, 40)[index], "USD/planner hour",
            "Assumed carrier-side loaded hourly labor opportunity value, outside terminal tariff labor; not a sourced local wage."),
        "initial_baseline_hours": _input(
            (2, 2.5, 3)[index], "planner hours/call",
            "Assumed initial document-to-checked-plan work, excluding later changes and exceptional rework."),
        "initial_assisted_hours": _input(
            (1, 0.75, 0.5)[index], "planner hours/call",
            "Assumed assisted initial work including reading, checking, confirmation and review."),
        "instruction_events_per_call": _input(
            (1, 2, 3)[index], "subsequent instruction changes/call",
            "Assumed changes after initial plan; excluded from initial and rework time."),
        "instruction_baseline_minutes": _input(
            (30, 45, 60)[index], "planner minutes/change",
            "Assumed baseline attention for a subsequent instruction change."),
        "instruction_assisted_minutes": _input(
            (10, 5, 3)[index], "planner minutes/change",
            "Assumed assisted attention including confirmation and review; not measured machine latency."),
        "rework_events_avoided_per_call": _input(
            (0.10, 0.25, 0.50)[index], "exceptional rework events/call",
            "Assumed net events avoided, excluded from both initial planning and instruction changes."),
        "rework_minutes_per_event": _input(
            (30, 60, 90)[index], "planner minutes/rework event",
            "Assumed additional correction work; no safety-loss or accident value."),
    }
    if scale:
        scenario.update({
            "adopted_throughput_share": _input(
                (0.01, 0.05, 0.20)[index], "fraction of world container throughput",
                "Assumed eligible AND adopting throughput including loading and discharge; alternative populations, never summed."),
            "ship_capacity_teu": _input(
                (1700, 8000, 20000)[index], "TEU vessel capacity",
                "Hypothetical future target: more compute for the search, a larger model for reading; capability still needs validation."),
        })
    else:
        scenario["annual_calls"] = _input(
            (52, 260, 1040)[index], "eligible adopting calls/year",
            "Assumed deployment portfolio; not an estimate of feeder calls or Laem Chabang adoption.")
    return scenario


def _prize_scenario(index):
    yard = "https://informs-sim.org/wsc13papers/includes/files/305.pdf"
    tariffs = "anchor-sources-v6.md#broader-extra-lift-prices-revised-source-survey"
    cppi = "https://unctad.org/system/files/official-document/rmt2025ch4_en.pdf"
    return {
        "ship_extra_restow_fraction_of_total_moves": _input(
            (0.05, 0.10, 0.15)[index], "extra ship lifts/all ship lifts",
            "Global restow-incidence ASSUMPTION, without an observed world rate or calibration dataset. Some restows are required; this share does not establish avoidable planning waste."),
        "yard_extra_rehandles_per_relevant_primary_move": _input(
            (23.67 / 633, 33.24 / 360, 18 / 84)[index], "extra yard lifts/relevant primary retrieval",
            "Worldwide transfer ASSUMPTION: Legato/Mazza WSC2013 Table 2 simulated reshuffles for real loading plans; ratios 23.67/633, 33.24/360, 18/84. Selected case sensitivity, not a global observed distribution.", source=yard),
        "yard_eligible_throughput_fraction": _input(
            0.5, "fraction of primary throughput",
            "Assumed loaded-export/retrieval-relevant throughput; avoids applying the yard ratio to all port throughput."),
        "ship_extra_lift_charge_usd": _input(
            (30, 75, 150)[index], "USD/extra ship lift",
            "Assumed price sensitivity discounted from regional list-tariff context: SAGT USD46.50-80/lift; Yusen LA USD205.93 cell shift; Liverpool GBP192.92/lift. Not a weighted world price or marginal operating cost.", source=tariffs),
        "yard_extra_lift_charge_usd": _input(
            (10, 25, 50)[index], "USD/extra yard lift",
            "Assumed sensitivity using Ries USD10/rehandle cost context and regional charge context: SAGT sorting USD25-47/container, Yusen mounting/grounding USD99.41, Liverpool GBP57.03/additional yard lift. Different service bundles; not a measured global marginal cost.", source=tariffs),
        "global_container_calls_per_year": _input(
            520_000, "container-vessel calls/year",
            "Rounded computed 2 x approximately260000 calls/half-year. UNCTAD RMT2025 ch4 says 2024 halves stayed near H2 2023; AIS/IMO >=1000GT coverage, not exact all-vessel census.", "computed", cppi),
        "berth_hours_per_call": _input(
            (20 * 0.75, 35 * 0.75, 60 * 0.75)[index], "berth hours/call",
            "Assumed global port hours20/35/60 x CPPI75% berth share. CPPI Table5.1 call-size means are context, not a fleet mean; excludes anchorage in the resulting berth hours.", source="https://ppp.worldbank.org/sites/default/files/2026-01/The%20Container%20Port%20Performance%20Index%202020%20to%202024%20-%20Trends%20and%20Lessons%20Learned.pdf"),
        "charter_usd_per_day": _input(
            (17_855, 39_725, 72_722)[index], "USD/vessel day",
            "Assumed world fleet proxies selected from VHBS29Oct2026 twelve-month assessments for1100/2500/6500TEU. Sourced assessments, unmeasured world mix. Charter opportunity value excludes separate fuel dollars.", source="https://www.vhbs.de/index.php?id=28&L=1"),
        "reducible_berth_share": _input(
            (0.02, 0.05, 0.10)[index], "fraction of berth hours",
            "Assume removing modeled extra work shortens berth stay2/5/10%; critical-path and parallel-crane effect, not a measured elasticity. Ship-time opportunity only, with no separate fuel charge."),
        "carrier_planner_hours_per_call": _input(
            (2, 2.5, 3)[index], "carrier planner hours/call",
            "Assumed global transfer of published2-3h whole-port allocation effort; midpoint2.5h. Full workflow-effort pool before capture, not all waste or time already saved.", source="https://www.jstage.jst.go.jp/article/pjsai/JSAI2019/0/JSAI2019_2C5E502/_pdf/-char/ja"),
        "carrier_planner_usd_per_hour": _input(
            (20, 39.58, 60)[index], "USD/carrier planner hour",
            "Assumed global opportunity-cost spread; BLS May2025 US logisticians medianUSD82320/year /2080h =39.58 rounded is context, not a global port-planner wage. Carrier planning excludes terminal tariff labor.", source="https://www.bls.gov/ooh/business-and-financial/logisticians.htm"),
        "capture_fraction": _input(
            (0.01, 0.05, 0.20)[index], "fraction of total gross prize",
            "Assumed combined adoption, eligibility and realized improvement applied once to the gross opportunity; not the scale scenarios' throughput adoption."),
        "marginal_electric_quay_kwh_per_extra_lift": _input(
            4, "kWh/extra electric quay lift",
            "Assumed worldwide transfer of dated PEMA total-minus-auxiliary example; not applicable to every crane or every restow."),
        "marginal_yard_kwh_per_extra_lift": _input(
            0, "kWh/extra yard lift credited",
            "Unquantified: no comparable global marginal yard-energy evidence selected; zero emissions credit, not a claim yard lifts use zero energy."),
        "electricity_kgco2_per_kwh": _input(
            0.445, "kgCO2/kWh",
            "IEA Electricity2025 reports2024 world generation intensity445gCO2/kWh. Transfer to port electricity and marginal avoided generation is an assumption; operational CO2, not lifecycle CO2e.", source="https://www.iea.org/reports/electricity-2025/emissions"),
        "container_ship_auxiliary_kw_at_berth": _input(
            (340, 700, 1320)[index], "kW/container ship at berth",
            "Assumed fleet mix using EPA2022 hotelling auxiliary-load defaults across container size categories; operating load already included. Excludes boiler, main engines and shore power.", source="https://nepis.epa.gov/Exe/ZyPURL.cgi?Dockey=P1014J1S.TXT"),
        "berth_auxiliary_kgco2_per_kwh": _input(
            217 * 3.206 / 1000, "kgCO2/kWh of berth auxiliary energy",
            "EPA2022 MGO auxiliary217g fuel/kWh x3.206gCO2/gfuel. Assumed all relevant hotelling load burns this fuel; operational CO2 excludes lifecycle/nonCO2 effects.", "computed", "https://nepis.epa.gov/Exe/ZyPURL.cgi?Dockey=P1014J1S.TXT"),
    }


DEFAULT_INPUTS = {
    "context": {
        "world_container_throughput_teu": _input(
            920_300_805, "TEU/year",
            "World port container throughput in 2024; UNCTAD page updated 2026-04-15. Port throughput counts handling, not unique cargo voyages.",
            "sourced", THROUGHPUT_SOURCE),
        "teu_per_primary_move": _input(
            1.5, "TEU/primary physical lift",
            "Assumed mix of container lengths; throughput conversion, not a physical constant."),
        "electric_quay_total_kwh_per_move": _input(
            6, "kWh/electric quay-crane move",
            "Dated example at 30 moves/hour, including auxiliary load; not a universal cycle measurement.",
            "sourced", "https://pema.org/wp-content/uploads/2022/09/PEMA-IP2-Energy-and-Environmental-Efficiency-in-Ports-and-Terminals.pdf"),
        "electric_quay_auxiliary_kwh_per_move": _input(
            2, "kWh/electric quay-crane move",
            "Auxiliary energy allocated per move in the same dated example; not all of it disappears with one avoided lift.",
            "sourced", "https://pema.org/wp-content/uploads/2022/09/PEMA-IP2-Energy-and-Environmental-Efficiency-in-Ports-and-Terminals.pdf"),
        "thai_grid_kgco2e_per_kwh": _input(
            0.5562, "kgCO2e/kWh",
            "TGO April 2026 grid factor; apply only to a comparable Thai electric-quay cycle, not mixed/global rehandles.",
            "sourced", "https://thaicarbonlabel.tgo.or.th/index.php?lang=EN&mod=Y0hKdlpIVmpkSE5mWlcxcGMzTnBiMjQ9"),
        "pilot_min_capacity_teu": _input(500, "TEU", "v6 design envelope, not a measured result.", "design", "docs/headline.md"),
        "pilot_max_capacity_teu": _input(800, "TEU", "v6 design envelope, not a measured result.", "design", "docs/headline.md"),
        "pilot_max_boxes": _input(195, "physical boxes", "v6 design envelope, not TEU capacity.", "design", "docs/headline.md"),
        "pilot_max_fill": _input(0.70, "fraction", "v6 design envelope; empty arrival, one loading call.", "design", "docs/headline.md"),
        "laem_chabang_container_calls_per_year": _input(
            9_325, "container-vessel calls/year",
            "BSAA 2024 activity: all container-vessel sizes, not eligible feeder or pilot calls.",
            "sourced", "https://thaibsaa.com/images/documents/Annual24-25_Final.pdf"),
        "terminal_revenue_low_usd": _input(
            62_670_000_000, "USD/year",
            "360iResearch 2024 container-terminal-operator market estimate, verified on GII's full reseller listing dated28Aug2025; not stated on the current publisher page. Commercial estimate, not audited global receipts.",
            "commercial_estimate", "https://www.giiresearch.com/report/ires1803580-container-terminal-operator-market-by-container.html"),
        "terminal_revenue_mid_usd": _input(
            75_500_000_000, "USD/year",
            "TechSci 2024 container-terminal operations estimate, directly verified on its June2025 release. Scope includes storage/intermodal services and cargo categories broader than containers; not audited global receipts or an interpolated midpoint.",
            "commercial_estimate", "https://www.techsciresearch.com/news/24170-container-terminal-operations-market.html"),
        "terminal_revenue_high_usd": _input(
            75_500_000_000, "USD/year",
            "Upper verified benchmark repeats TechSci2024 estimate. Unverified IBISWorld figure excluded, not carried as an assumed upper bound. This is a commercial-estimate comparison range, not three independent market measurements.",
            "commercial_estimate", "https://www.techsciresearch.com/news/24170-container-terminal-operations-market.html"),
    },
    "pilot": {name: _scenario(index) for index, name in enumerate(("low", "mid", "high"))},
    "scale": {name: _scenario(index, scale=True) for index, name in enumerate(("low", "mid", "high"))},
    "prize": {name: _prize_scenario(index) for index, name in enumerate(("low", "mid", "high"))},
}


def default_inputs():
    """Return an independent, JSON-able input bundle with provenance on every input."""
    return deepcopy(DEFAULT_INPUTS)


def _validate(inputs):
    if not isinstance(inputs, dict) or inputs.keys() != DEFAULT_INPUTS.keys():
        raise ValueError("inputs must have context, pilot, scale and prize sections")
    for section, rows in DEFAULT_INPUTS.items():
        if not isinstance(inputs[section], dict) or inputs[section].keys() != rows.keys():
            raise ValueError(f"{section} input keys differ from the schema")
        groups = [(inputs[section], rows)] if section == "context" else [
            (inputs[section][name], rows[name]) for name in rows]
        for group, template in groups:
            if not isinstance(group, dict) or group.keys() != template.keys():
                raise ValueError(f"{section} scenario input keys differ from the schema")
            for name, field in group.items():
                if not isinstance(field, dict) or field.keys() != template[name].keys():
                    raise ValueError(f"{name} needs value, unit, status, source and note")
                value = field["value"]
                if isinstance(value, bool) or not isinstance(value, (float, int)) or not isfinite(value) or value < 0:
                    raise ValueError(f"{name} must be a finite, nonnegative number")
                if field["status"] not in {"assumption", "sourced", "computed", "design", "commercial_estimate"}:
                    raise ValueError(f"{name} has an unknown provenance status")
                if not all(isinstance(field[key], str) and field[key].strip() for key in ("unit", "note")):
                    raise ValueError(f"{name} needs nonempty unit and note")
                if field["status"] != "assumption" and not field["source"]:
                    raise ValueError(f"{name} needs a source for its provenance status")
                if field["source"] is not None and not isinstance(field["source"], str):
                    raise ValueError(f"{name} source must be a string or null")
                if name in {"rehandle_reduction", "adopted_throughput_share", "pilot_max_fill", "yard_eligible_throughput_fraction", "reducible_berth_share", "capture_fraction"} and value > 1:
                    raise ValueError(f"{name} must be at most one")
                if name == "ship_extra_restow_fraction_of_total_moves" and value >= 1:
                    raise ValueError(f"{name} must be less than one")
                if name in {"primary_moves_per_call", "teu_per_primary_move"} and value == 0:
                    raise ValueError(f"{name} must be positive")
                if name.startswith("terminal_revenue_") and value == 0:
                    raise ValueError(f"{name} must be positive")
    context = inputs["context"]
    if context["pilot_min_capacity_teu"]["value"] > context["pilot_max_capacity_teu"]["value"]:
        raise ValueError("pilot capacity bounds are reversed")
    if not (context["terminal_revenue_low_usd"]["value"] <= context["terminal_revenue_mid_usd"]["value"] <= context["terminal_revenue_high_usd"]["value"]):
        raise ValueError("terminal revenue benchmark bounds are reversed")
    if context["electric_quay_auxiliary_kwh_per_move"]["value"] > context["electric_quay_total_kwh_per_move"]["value"]:
        raise ValueError("auxiliary energy cannot exceed total energy")
    for row in inputs["pilot"].values():
        if row["primary_moves_per_call"]["value"] > context["pilot_max_boxes"]["value"]:
            raise ValueError("pilot load lifts exceed the one-load-per-box design bound")


def _per_call(row):
    v = {key: field["value"] for key, field in row.items()}
    rehandles = v["primary_moves_per_call"] * v["extra_rehandles_per_primary_move"]
    avoided = rehandles * v["rehandle_reduction"]
    initial = v["initial_baseline_hours"] - v["initial_assisted_hours"]
    changes = v["instruction_events_per_call"] * (v["instruction_baseline_minutes"] - v["instruction_assisted_minutes"]) / 60
    rework = v["rework_events_avoided_per_call"] * v["rework_minutes_per_event"] / 60
    planner_hours = initial + changes + rework
    handling = avoided * v["move_charge_usd"]
    labor = planner_hours * v["planner_usd_per_hour"]
    return {
        "baseline_extra_rehandles": rehandles,
        "avoided_extra_rehandles": avoided,
        "initial_planner_hours_saved": initial,
        "instruction_planner_hours_saved": changes,
        "rework_planner_hours_saved": rework,
        "total_planner_hours_saved": planner_hours,
        "handling_charge_equivalent_usd": handling,
        "planner_labor_equivalent_usd": labor,
        "total_equivalent_usd": handling + labor,
    }


def _flatten_numbers(value, prefix=""):
    out = {}
    if isinstance(value, dict):
        if "value" in value and "status" in value:
            return {prefix: value["value"]}
        for key, item in value.items():
            out.update(_flatten_numbers(item, f"{prefix}.{key}" if prefix else key))
    elif isinstance(value, (float, int)) and not isinstance(value, bool):
        out[prefix] = value
    return out


def _prize(row, context):
    v = {key: field["value"] for key, field in row.items()}
    primary = context["world_container_throughput_teu"] / context["teu_per_primary_move"]
    ship_share = v["ship_extra_restow_fraction_of_total_moves"]
    ship_extra = primary * ship_share / (1 - ship_share)
    yard_extra = primary * v["yard_eligible_throughput_fraction"] * v["yard_extra_rehandles_per_relevant_primary_move"]
    ship_handling = ship_extra * v["ship_extra_lift_charge_usd"]
    yard_handling = yard_extra * v["yard_extra_lift_charge_usd"]
    berth_hours = v["global_container_calls_per_year"] * v["berth_hours_per_call"]
    avoided_berth_hours = berth_hours * v["reducible_berth_share"]
    ship_time = avoided_berth_hours * v["charter_usd_per_day"] / 24
    planner_hours = v["global_container_calls_per_year"] * v["carrier_planner_hours_per_call"]
    planner_labor = planner_hours * v["carrier_planner_usd_per_hour"]
    handling = ship_handling + yard_handling
    gross = handling + ship_time + planner_labor
    capture = v["capture_fraction"]
    ship_kwh = ship_extra * v["marginal_electric_quay_kwh_per_extra_lift"]
    yard_kwh = yard_extra * v["marginal_yard_kwh_per_extra_lift"]
    ship_kg = ship_kwh * v["electricity_kgco2_per_kwh"]
    yard_kg = yard_kwh * v["electricity_kgco2_per_kwh"]
    berth_kwh = avoided_berth_hours * v["container_ship_auxiliary_kw_at_berth"]
    berth_kg = berth_kwh * v["berth_auxiliary_kgco2_per_kwh"]
    total_kg = ship_kg + yard_kg + berth_kg
    result = {
        "label": "projection",
        "interpretation": "Global annual gross opportunity pool before capture, not a result, revenue forecast or all avoidable cost. Alternative cases; do not add to pilot or scale outputs. Handling charges, ship-time and carrier labor are distinct opportunity-equivalent categories, not one party's profit. No fuel dollars are added.",
        "annual_primary_ship_moves": primary,
        "annual_extra_ship_lifts": ship_extra,
        "annual_extra_yard_lifts": yard_extra,
        "annual_baseline_berth_hours": berth_hours,
        "avoided_berth_hours": avoided_berth_hours,
        "ship_handling_equivalent_usd": ship_handling,
        "yard_handling_equivalent_usd": yard_handling,
        "handling_equivalent_usd": handling,
        "ship_time_equivalent_usd": ship_time,
        "carrier_planner_hours": planner_hours,
        "planner_labor_equivalent_usd": planner_labor,
        "total_gross_prize_usd": gross,
        "capture_fraction": capture,
        "captured_opportunity_usd": gross * capture,
        "emissions": {
            "label": "projection",
            "interpretation": "Separate nonmonetized operational CO2 potential: extra ship lifts assumed electric and removable, plus assumed reducible berth hours. Yard energy unquantified by default. Not an inventory, lifecycle CO2e or achieved reduction; no emissions are monetized.",
            "ship_extra_lift_kwh": ship_kwh,
            "yard_extra_lift_kwh": yard_kwh,
            "berth_auxiliary_kwh": berth_kwh,
            "ship_extra_lift_kgco2": ship_kg,
            "yard_extra_lift_kgco2": yard_kg,
            "berth_auxiliary_kgco2": berth_kg,
            "total_gross_kgco2": total_kg,
            "total_gross_million_tonnes_co2": total_kg / 1_000_000_000,
            "captured_kgco2": total_kg * capture,
            "captured_million_tonnes_co2": total_kg * capture / 1_000_000_000,
        },
    }
    for key, value in list(result.items()):
        if key.endswith("_usd"):
            result[key.removesuffix("_usd") + "_million_usd"] = value / 1_000_000
            result[key.removesuffix("_usd") + "_billion_usd"] = value / 1_000_000_000
    result["annual_extra_ship_million_lifts"] = ship_extra / 1_000_000
    result["annual_extra_yard_million_lifts"] = yard_extra / 1_000_000
    result["annual_primary_ship_million_moves"] = primary / 1_000_000
    result["world_throughput_million_teu"] = context["world_container_throughput_teu"] / 1_000_000
    result["annual_container_thousand_calls"] = v["global_container_calls_per_year"] / 1000
    result["electric_quay_kgco2_per_extra_lift"] = v["marginal_electric_quay_kwh_per_extra_lift"] * v["electricity_kgco2_per_kwh"]
    result["avoided_berth_million_hours"] = avoided_berth_hours / 1_000_000
    result["carrier_planner_million_hours"] = planner_hours / 1_000_000
    return result


def _exposure(inputs, prize):
    context = {key: field["value"] for key, field in inputs["context"].items()}
    mid = {key: field["value"] for key, field in inputs["prize"]["mid"].items()}
    berth_time = mid["global_container_calls_per_year"] * mid["berth_hours_per_call"] * mid["charter_usd_per_day"] / 24
    result = {
        "label": "projection",
        "interpretation": "Commercial terminal-revenue benchmarks plus full annual vessel time at berth, valued only with mid-case call, berth-hour and charter inputs. Exposure is a cost-context frame, not waste, savings, or an amount added to the prize. Terminal charges already include extra handling. No planner or fuel dollars are added.",
        "berth_time_usd": berth_time,
        "berth_time_billion_usd": berth_time / 1_000_000_000,
    }
    crosscheck = {}
    for name in ("low", "mid", "high"):
        revenue = context[f"terminal_revenue_{name}_usd"]
        result[name] = {
            "terminal_revenue_usd": revenue,
            "terminal_revenue_billion_usd": revenue / 1_000_000_000,
            "revenue_per_primary_lift_usd": revenue / prize["mid"]["annual_primary_ship_moves"],
            "total_usd": revenue + berth_time,
            "total_billion_usd": (revenue + berth_time) / 1_000_000_000,
        }
    result["headline_billion_usd"] = round(result["low"]["total_billion_usd"] / 10) * 10
    result["headline_one_percent_billion_usd"] = result["headline_billion_usd"] * 0.01
    result["headline_rounding_rule"] = "Round the lower-end exposure total to the nearest ten billion USD. This is a rounded lower-end frame, not the middle benchmark, a saving, or a precisely measured total. One percent uses that rounded frame."
    for name, case in prize.items():
        crosscheck[name] = {
            benchmark: {
                "ship_handling_share": case["ship_handling_equivalent_usd"] / values["terminal_revenue_usd"],
                "handling_share": case["handling_equivalent_usd"] / values["terminal_revenue_usd"],
                "total_prize_share": case["total_gross_prize_usd"] / values["terminal_revenue_usd"],
            }
            for benchmark, values in result.items() if benchmark in ("low", "mid", "high")
        }
    return result, crosscheck


def calculate(inputs=None):
    """Calculate alternative conditional projections from a complete input bundle.

    Call ``default_inputs()``, edit fields and provenance, then pass the full
    bundle. There are no measured pipeline values or legacy scaling factors.
    """
    inputs = deepcopy(DEFAULT_INPUTS if inputs is None else inputs)
    _validate(inputs)
    result = {
        "schema_version": "stowlm.impact-v6/1",
        "label": "projection",
        "measured_pipeline_results": None,
        "inputs": inputs,
        "pilot": {}, "scale": {}, "prize": {},
    }
    context = {key: field["value"] for key, field in inputs["context"].items()}
    marginal_kwh = context["electric_quay_total_kwh_per_move"] - context["electric_quay_auxiliary_kwh_per_move"]
    result["conditional_emissions"] = {
        "label": "projection",
        "assumption": "Subtract allocated auxiliary energy to proxy marginal lift work. No berth-time or annual emissions estimate; do not apply to mixed/global rehandles.",
        "marginal_kwh_per_comparable_electric_quay_move": marginal_kwh,
        "kgco2e_per_comparable_avoided_electric_quay_move": marginal_kwh * context["thai_grid_kgco2e_per_kwh"],
    }
    for section in ("pilot", "scale"):
        for name, row in inputs[section].items():
            if section == "pilot":
                calls = row["annual_calls"]["value"]
                throughput = calls * row["primary_moves_per_call"]["value"] * context["teu_per_primary_move"]
            else:
                throughput = context["world_container_throughput_teu"] * row["adopted_throughput_share"]["value"]
                calls = throughput / context["teu_per_primary_move"] / row["primary_moves_per_call"]["value"]
            per_call = _per_call(row)
            result[section][name] = {
                "label": "projection",
                "annual_calls": calls,
                "annual_primary_moves": calls * row["primary_moves_per_call"]["value"],
                "annual_throughput_teu": throughput,
                "per_call": per_call,
                "annual": {key: value * calls for key, value in per_call.items()},
            }
    result["prize"] = {name: _prize(row, context) for name, row in inputs["prize"].items()}
    result["exposure"], result["revenue_crosscheck"] = _exposure(inputs, result["prize"])
    local_calls = context["laem_chabang_container_calls_per_year"]
    world_calls = inputs["prize"]["mid"]["global_container_calls_per_year"]["value"]
    share = local_calls / world_calls if world_calls > 0 and local_calls <= world_calls else None
    local_pool = None if share is None else share * result["prize"]["mid"]["total_gross_prize_usd"]
    result["local_projection"] = {"laem_chabang": {
        "label": "projection",
        "annual_calls": local_calls,
        "world_calls": world_calls,
        "share_of_world_calls": share,
        "mid_pool_usd": local_pool,
        "mid_pool_million_usd": None if local_pool is None else local_pool / 1_000_000,
        "headline_mid_pool_million_usd": None if local_pool is None else 5 * round(local_pool / 5_000_000),
        "note": "Illustrative allocation from exact call share, with identical global inputs and average workload per call. Not eligible feeder demand or measured local saving; do not add to the world pool. Headline rounds to the nearest USD5M. Unavailable if world calls are zero or fewer than local calls.",
    }}
    result["capture"] = {
        case: {
            rate: {
                "fraction": row["capture_fraction"]["value"],
                "opportunity_million_usd": result["prize"][case]["total_gross_prize_usd"] * row["capture_fraction"]["value"] / 1_000_000,
                "opportunity_billion_usd": result["prize"][case]["total_gross_prize_usd"] * row["capture_fraction"]["value"] / 1_000_000_000,
            }
            for rate, row in inputs["prize"].items()
        }
        for case in result["prize"]
    }
    result["report_numbers"] = _flatten_numbers(result)
    # Fail closed on arithmetic overflow instead of emitting nonstandard JSON.
    json.dumps(result, allow_nan=False)
    return result


MARKER = re.compile(r"<!-- calc:([\w.]+)\|([^<>\n]+) -->(.*?)<!-- /calc -->", re.DOTALL)


def check_report_numbers(text, result=None):
    """Check visible spans: ``<!-- calc:path|,.0f -->1,234<!-- /calc -->``.

    Currency signs and units belong outside the span; ``.0%`` renders shares.
    Each span is checked against its calculator value and Python format spec.
    Return the number of checked spans; raise ValueError on drift or no spans.
    """
    numbers = (calculate() if result is None else result)["report_numbers"]
    matches = list(MARKER.finditer(text))
    if not matches:
        raise ValueError("report has no calculator number markers")
    if text.count("<!-- calc:") != len(matches):
        raise ValueError("report has malformed calculator number markers")
    for match in matches:
        path, spec, visible = match.groups()
        if path not in numbers:
            raise ValueError(f"unknown calculator report path: {path}")
        expected = format(numbers[path], spec)
        if visible != expected:
            raise ValueError(f"{path}: report {visible!r} differs from calculator {expected!r}")
    return len(matches)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, help="complete JSON input bundle, including provenance")
    parser.add_argument("--json", type=Path, help="write output JSON (otherwise print)")
    parser.add_argument("--check-report", type=Path, help="verify marked report numbers against this calculation")
    args = parser.parse_args(argv)
    result = calculate(json.loads(args.inputs.read_text()) if args.inputs else None)
    if args.check_report:
        check_report_numbers(args.check_report.read_text(), result)
    output = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if args.json:
        args.json.write_text(output)
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
