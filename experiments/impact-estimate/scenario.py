"""Explicit economic/time EXTRAPOLATION from 12–22-box maps, not measured impact.

Used only by --scenario. All prices are retail-charge proxies; time savings and
transfer to a 500-box call are assumptions. No network, planner or workers run.
"""

from __future__ import annotations

import json
from math import floor
from pathlib import Path

AS_OF = "2026-10-31"
LABEL = "EXTRAPOLATION from 12–22 boxes; not measured operational savings"
ROOT = Path(__file__).resolve().parents[2]

SOURCES = {
    "esco": {
        "url": "https://www.esco.co.th/escotariff/consignee.html",
        "source_date": "2026-04-01", "date_kind": "tariff page updated",
        "access_date": AS_OF,
        "locator": "Consignee sheet, item 8; excludes VAT",
        "scope": "20-foot extra movement requested by consignee/customs, not export-rehandle marginal cost",
    },
    "chennai": {
        "url": "https://www.dpworld.com/india/-/media/project/dpwg/dpwg-tenant/apac/india/media-files/ports-and-terminals/chennai/tariff/cctl-sor---wef-1st-may-2025.pdf?rev=ceba2186d9df4e2e928898993b4792ec",
        "source_date": "2025-05-01", "date_kind": "tariff effective date",
        "access_date": AS_OF, "locator": "PDF page 8, sections 3.7 and 3.8",
        "scope": "Historical 2025 Indian foreign-going retail charges subject to annual indexation; not a current 2026 quote or Thai marginal resource cost",
    },
    "hutchison": {
        "url": "https://hutchisonports.co.th/hpt-celebrates-the-long-anticipated-official-opening-of-terminal-d-at-laem-chabang-port/",
        "source_date": "2021-12-09", "date_kind": "CMS publication date; opening event 2019-01-30",
        "publication_date_evidence_url": "https://hutchisonports.co.th/wp-json/wp/v2/posts/403?_fields=date,date_gmt,modified,link,title",
        "access_date": AS_OF, "locator": "Push for Innovation, QC productivity paragraph",
        "scope": "Operator says remote-control QC handles 30 or more moves/hour; not a restow/hatch cycle measurement",
    },
    "contex": {
        "url": "https://www.vhbs.de/index.php?id=28&L=1",
        "source_date": "2026-10-29", "date_kind": "rate assessment date",
        "access_date": AS_OF, "locator": "New ConTex table, 29.10.2026 row",
        "scope": "Daily charter market assessments; not actual avoided cash expenditure",
    },
    "bsaa": {
        "url": "https://thaibsaa.com/images/documents/Annual24-25_Final.pdf",
        "source_date": "2024-2025", "date_kind": "report edition; statistics calendar 2024",
        "access_date": AS_OF, "locator": "Report page 6 (PDF page 6), Laem Chabang paragraph",
        "scope": "All container-vessel calls, not a feeder-only eligibility count; TEU context only",
    },
    "world_bank": {
        "url": "https://documents1.worldbank.org/curated/en/099091925073051415/pdf/P509479-9781ba16-3dbc-4064-9d8f-8eaf9951e09d.pdf",
        "source_date": "2025", "date_kind": "publication year; statistics 2024",
        "access_date": AS_OF, "locator": "Table 5.1, printed page 44 (PDF page 60), Thailand row",
        "scope": "Thailand-wide time in port by call size, not Laem Chabang-only or a reducible baseline",
    },
    "planner_context": {
        "url": "https://www.jstage.jst.go.jp/article/pjsai/JSAI2019/0/JSAI2019_2C5E502/_pdf/-char/ja",
        "source_date": "2019", "date_kind": "conference year",
        "access_date": AS_OF, "locator": "Page 2, stowage-planning paragraph",
        "scope": "2–3 hours to allocate all containers for each port; whole task, not time per replan",
    },
    "headline": {
        "url": None, "source_path": "docs/headline.md; experiments/mvp/frontier/RESULTS.md",
        "source_date": "2026-10-31", "date_kind": "headline-contract date",
        "access_date": AS_OF, "locator": "Provisional blind pooled-30 comparison; 25 mutually passed maps",
        "scope": "Rounded 39% reduction versus common two-step practice on synthetic small maps",
    },
    "decomposition": {
        "url": None,
        "source_path": ["experiments/mvp/frontier/runs/test-0.9/AUDIT.json",
                        "experiments/mvp/strategy/move_breakdown.json"],
        "source_date": None, "date_kind": "original artifact date not established; repository snapshot accessed below",
        "access_date": AS_OF, "locator": "14 mutually passed small test maps, same filter as frontier/dev/test_tables.py",
        "scope": "v0.9 category decomposition, a different cohort/version from provisional blind result",
    },
}


def sourced(value, unit: str, source: str, note: str) -> dict:
    return {"value": value, "unit": unit, "status": "sourced", "source_id": source,
            **SOURCES[source], "note": note}


def assumption(value, unit: str, note: str) -> dict:
    return {"value": value, "unit": unit, "status": "ASSUMPTION", "url": None,
            "source_date": None, "access_date": None, "declared_date": AS_OF, "note": note}


def definition(value, unit: str, note: str) -> dict:
    return {"value": value, "unit": unit, "status": "unit definition", "url": None,
            "source_date": None, "access_date": None, "declared_date": AS_OF, "note": note}


def raw_category_counts(measured: dict) -> dict:
    """Reconstruct categories from the original source rows; restows already in lifts."""
    split = measured["observed_test_decomposition_context_only"]
    ids = set(split["instance_ids"])
    audits = {r["instance_id"]: r for r in json.loads((ROOT / split["sources"][0]).read_text())}
    baselines = {r["instance_id"]: r for r in json.loads((ROOT / split["sources"][1]).read_text())}
    keys = ("relocations", "restow_now", "hatch_now", "restow_later", "hatch_later")
    totals = {
        "frontier": {key: sum(audits[i][key] for i in ids) for key in keys},
        "two_step": {key: sum(baselines[i]["twostep"][key] for i in ids) for key in keys},
    }
    saved = {key: totals["two_step"][key] - totals["frontier"][key] for key in keys}
    if any(v < 0 for v in saved.values()) or sum(saved.values()) <= 0:
        raise ValueError("Category transfer requires positive net savings without a negative category")
    return {"maps": len(ids), "instance_ids": sorted(ids), "unit": "lifts summed across maps",
            "totals": totals, "saved": saved,
            "note": "Restow values already count off-and-back-on lifts; do not multiply by two again."}


def input_ledger(measured: dict, raw: dict) -> dict:
    bundle = measured["inputs"]
    return {
        "export_boxes_per_call": assumption(500, "physical boxes/call", "Illustrative export exchange; not vessel capacity or TEU."),
        "boxes_per_section": sourced(22, "physical boxes/section", "headline", "Upper measured size (range 12–22); using it as section denominator is a transfer assumption below."),
        "transfer_retention": assumption({"low": .10, "mid": .25, "high": .50}, "share", "Retained small-map effect after scaling; unvalidated at 500 boxes."),
        "later_credit": assumption({n: c["projected_later_credit"] for n, c in measured["cases"].items()}, "share of projected later savings", "0/50/100% credit; not probabilities or confidence limits."),
        "section_scaling": assumption(True, "enabled", "Scale each small-map category by 500/22 and retention; neither independent sections nor linear benefits are established."),
        "category_transfer": assumption(True, "enabled", "Transfer v0.9 category-saving shares to blind 39% bundle and then to larger calls."),
        "blind_baseline_lifts_per_map": sourced(bundle["baseline_lifts_per_map"], "lifts/map", "headline", "Provisional rounded baseline; replace complete bundle after test v3."),
        "blind_avoided_fraction": sourced(bundle["avoided_fraction"], "share", "headline", "Provisional rounded reduction; not an operational improvement rate."),
        "blind_mutual_maps": sourced(bundle["mutual_maps"], "maps", "headline", "Matched blind maps both arms passed."),
        "raw_category_counts": sourced(raw, "lifts", "decomposition", "Reconstructed source totals and exact saved category shares."),
        "yard_charge_thb_per_lift": sourced(350, "THB/lift", "esco", "20-foot extra movement, consignee/customs request; excluding VAT."),
        "fx_thb_per_usd": assumption(35, "THB/USD", "Named FX assumption; not a live quote or sourced exchange rate."),
        "restow_charge_usd_per_event": sourced(41.72, "USD/20-foot via-quay restow", "chennai", "Landing on quay and reshipping, one complete event."),
        "restow_lifts_per_event": sourced(2, "lifts/event", "chennai", "Via-quay event has landing and reshipping; divide price by two, not multiply source counts."),
        "hatch_charge_usd_per_cycle": sourced(56.78, "USD/open-close cycle", "chennai", "With quay landing; tariff explicitly covers opening and closing."),
        "hatch_lifts_per_cycle": sourced(2, "lifts/cycle", "chennai", "Tariff explicitly charges half for opening or closing alone."),
        "retail_proxy_transfer": assumption(True, "enabled", "Apply 20-foot retail prices to saved work at all ports; actual box mix and marginal cost unknown. Hatch proxy is per cover, not box size."),
        "qc_lifts_per_hour": sourced(30, "lifts/crane-hour", "hutchison", "Use lower endpoint of operator's 30+ claim."),
        "qc_cycle_transfer": assumption(True, "enabled", "Apply container-move rate to restows and hatch lifts at current and later ports; not measured cycle times."),
        "yard_minutes_per_lift": assumption(2, "equipment minutes/lift", "No sourced local yard cycle; keep separate from elapsed vessel time."),
        "quay_critical_path_fraction": assumption({"low": 0, "mid": .25, "high": .50}, "share of quay work", "Only this fraction becomes elapsed berth time; no crane schedule simulated. Yard time never added."),
        "charter_usd_per_day": sourced({"low": 17855, "mid": 18495, "high": 35982}, "USD/day", "contex", "Low 1,100 TEU 12-month; mid 1,100 TEU 6-month; high 1,800 TEU 12-month assessments; types/tenors differ."),
        "charter_transfer": assumption(True, "enabled", "Value released berth time at charter hire/24; opportunity value, not automatically avoided cash or revenue."),
        "planner_before_minutes_per_replan": assumption({"low": 30, "mid": 60, "high": 120}, "planner minutes/replan", "Assumed time per replan, not sourced whole-planning hours."),
        "planner_after_minutes_per_replan": assumption({"low": 30, "mid": 30, "high": 60}, "planner minutes/replan", "End-to-end human task assumption; includes review. No runtime subtracted from whole-task hours."),
        "replans_per_call": assumption(1, "replans/eligible call", "Illustrative frequency, not measured."),
        "operating_weeks_per_year": assumption(52, "weeks/year", "No cancellations or skipped weeks."),
        "eligible_calls_per_week": assumption(1, "eligible calls/week", "One assumed eligible directional stream, not both visits or a vessel-count multiplier."),
        "port_container_calls_per_year": sourced(9325, "container-vessel calls/calendar 2024", "bsaa", "Scale population only; actual eligible/adopting subset not known."),
        "port_teu_context_only": sourced(9554673, "TEU/calendar 2024", "bsaa", "Context only; never multiplied by per-box or per-call benefit."),
        "port_eligible_adoption_share": assumption({"low": .01, "mid": .05, "high": .10}, "share of calls", "Combined eligibility and uptake assumption; floor to whole calls, never 100% scaling."),
        "annual_identical_calls": assumption(True, "enabled", "Every eligible adopted call has identical illustrative exchange, category savings and prices; later benefits attributed once to originating call."),
        "thailand_port_stay_context_only": sourced({"under_500_moves": 23.0, "501_to_1000_moves": 28.2, "1001_to_1500_moves": 25.4}, "hours in port/call, 2024", "world_bank", "Context only; includes wider port time, not a reducible berth baseline or used in calculations."),
        "whole_planning_context_only": sourced([2, 3], "hours/whole port allocation task", "planner_context", "Context only; not a replan measurement and never used in savings calculations."),
        "minutes_per_hour": definition(60, "minutes/hour", "Unit conversion."),
        "hours_per_day": definition(24, "hours/day", "Unit conversion."),
    }


def annual_totals(per_call: dict, calls: int) -> dict:
    return {"label": LABEL, "calls": calls, "unit": "annual totals over assumed identical eligible adopted calls",
            **{key: value * calls for key, value in per_call.items() if isinstance(value, (int, float))}}


def scenario_report(measured: dict) -> dict:
    raw = raw_category_counts(measured)
    inputs = input_ledger(measured, raw)
    value = lambda key: inputs[key]["value"]
    total_saved = value("blind_baseline_lifts_per_map") * value("blind_avoided_fraction")
    total_raw = sum(raw["saved"].values())
    section_components = {key: total_saved * count / total_raw for key, count in raw["saved"].items()}
    yard_price = value("yard_charge_thb_per_lift") / value("fx_thb_per_usd")
    restow_price = value("restow_charge_usd_per_event") / value("restow_lifts_per_event")
    hatch_price = value("hatch_charge_usd_per_cycle") / value("hatch_lifts_per_cycle")
    annual_stream_calls = value("operating_weeks_per_year") * value("eligible_calls_per_week")
    cases = {}
    for name in ("low", "mid", "high"):
        scale = value("export_boxes_per_call") / value("boxes_per_section") * value("transfer_retention")[name]
        later_credit = value("later_credit")[name]
        components = {key: count * scale * (later_credit if key.endswith("later") else 1)
                      for key, count in section_components.items()}
        yard = components["relocations"]
        now_restow = components["restow_now"]
        now_hatch = components["hatch_now"]
        later_restow = components["restow_later"]
        later_hatch = components["hatch_later"]
        now_qc = now_restow + now_hatch
        later_qc = later_restow + later_hatch
        now_handling = yard * yard_price + now_restow * restow_price + now_hatch * hatch_price
        later_handling = later_restow * restow_price + later_hatch * hatch_price
        now_berth = now_qc / value("qc_lifts_per_hour") * value("quay_critical_path_fraction")[name]
        later_berth = later_qc / value("qc_lifts_per_hour") * value("quay_critical_path_fraction")[name]
        hourly_charter = value("charter_usd_per_day")[name] / value("hours_per_day")
        ship_time = (now_berth + later_berth) * hourly_charter
        planner_hours = ((value("planner_before_minutes_per_replan")[name]
                          - value("planner_after_minutes_per_replan")[name])
                         / value("minutes_per_hour") * value("replans_per_call"))
        per_call = {
            "label": LABEL,
            "yard_lifts": yard, "restow_now_lifts": now_restow, "hatch_now_lifts": now_hatch,
            "credited_restow_later_lifts": later_restow, "credited_hatch_later_lifts": later_hatch,
            "this_port_lifts": yard + now_qc, "credited_projected_later_lifts": later_qc,
            "total_credited_lifts": sum(components.values()),
            "this_port_handling_charge_proxy_usd": now_handling,
            "credited_later_handling_charge_proxy_usd": later_handling,
            "handling_charge_proxy_usd": now_handling + later_handling,
            "yard_equipment_hours": yard * value("yard_minutes_per_lift") / value("minutes_per_hour"),
            "this_port_quay_work_hours": now_qc / value("qc_lifts_per_hour"),
            "credited_later_quay_work_hours": later_qc / value("qc_lifts_per_hour"),
            "quay_work_hours": (now_qc + later_qc) / value("qc_lifts_per_hour"),
            "this_port_berth_hours": now_berth, "credited_later_berth_hours": later_berth,
            "berth_hours": now_berth + later_berth,
            "this_port_ship_time_opportunity_usd": now_berth * hourly_charter,
            "credited_later_ship_time_opportunity_usd": later_berth * hourly_charter,
            "ship_time_opportunity_usd": ship_time,
            "combined_proxy_usd": now_handling + later_handling + ship_time,
            "planner_hours": planner_hours,
        }
        adopted_calls = floor(value("port_container_calls_per_year") * value("port_eligible_adoption_share")[name])
        cases[name] = {"label": LABEL, "section_scale_after_retention": scale,
                       "per_call": per_call,
                       "annual_52_call_stream": annual_totals(per_call, annual_stream_calls),
                       "annual_adoption_subset": annual_totals(per_call, adopted_calls)}
    return {
        "label": LABEL, "as_of": AS_OF,
        "relationship_to_measured_layer": "Separate optional scenario. Original measured/provisional bundle is unchanged.",
        "sources": SOURCES, "inputs": inputs, "raw_category_reconstruction": raw,
        "derived_price_proxies": {"label": LABEL, "yard_usd_per_lift": yard_price,
                                  "restow_usd_per_lift": restow_price, "hatch_usd_per_lift": hatch_price},
        "per_section_components_before_extrapolation": {"unit": "provisional allocated saved lifts/map", **section_components},
        "formulas": ["section_total_saved = blind_baseline_lifts_per_map * blind_avoided_fraction",
                     "section_category = section_total_saved * raw_category_saved / sum(raw_category_saved)",
                     "call_category = section_category * (export_boxes_per_call / boxes_per_section) * transfer_retention; later categories additionally * later_credit",
                     "handling_charge_proxy = yard_lifts * (350 / 35) + restow_lifts * (41.72 / 2) + hatch_lifts * (56.78 / 2)",
                     "quay_work_hours = (restow_lifts + hatch_lifts) / 30; berth_hours = quay_work_hours * critical_path_fraction",
                     "ship_time_opportunity = berth_hours * charter_usd_per_day / 24",
                     "planner_hours = (assumed_before_minutes - assumed_after_minutes) / 60 * replans_per_call",
                     "annual_stream_calls = 52 operating weeks * 1 eligible call/week; adoption_calls = floor(9325 * assumed_eligible_adoption_share)",
                     "annual quantity = per_call quantity * eligible adopted calls; later-port savings counted once at origin"],
        "cases": cases,
        "limits": ["Every scaled result is EXTRAPOLATION from 12–22 boxes, not a measurement at 500 boxes. Medium/full-ship calls are not solved.",
                   "Low credits only this-port modelled work; none of the scenarios establishes realised deployment savings.",
                   "Mid/high include projected later-port work; no future loads and fixed coordinates remain assumptions in those projections.",
                   "Handling retail-charge proxies and ship-time opportunity values concern different parties; combined figure is not one party's cash saving or profit.",
                   "No wage, fuel, extra intake, accident probability, safety dollars, instruction-loss dollars or software implementation cost is estimated.",
                   "Planner hours are an explicit assumed before/after replan task; safety and instruction-following remain unmonetized.",
                   "Context port-stay and whole-planning hours are never used as reducible baselines. TEU never scales benefits.",
                   "Adoption cases assume eligibility and uptake together; they are not a forecast, confidence interval or 100% port extrapolation."],
    }


def print_scenario(report: dict) -> None:
    for scope, scope_label in (("per_call", "PER ILLUSTRATIVE 500-BOX CALL"),
                               ("annual_52_call_stream", "ANNUAL 52-CALL STREAM"),
                               ("annual_adoption_subset", "ANNUAL ASSUMED ADOPTION SUBSET")):
        print(f"\n{LABEL.upper()} — {scope_label}")
        print("case | calls | saved lifts | handling-charge proxy USD | ship-time opportunity USD | combined proxy USD | yard equipment h | quay work h | berth h | planner h")
        for name, case in report["cases"].items():
            r = case[scope]
            print(f"{name} | {r.get('calls', 1)} | {r['total_credited_lifts']:.6f} | "
                  f"{r['handling_charge_proxy_usd']:.6f} | {r['ship_time_opportunity_usd']:.6f} | "
                  f"{r['combined_proxy_usd']:.6f} | {r['yard_equipment_hours']:.6f} | "
                  f"{r['quay_work_hours']:.6f} | {r['berth_hours']:.6f} | {r['planner_hours']:.6f}")
    print("EXTRAPOLATION SOURCE LEDGER (as accessed 2026-10-31; exact original artifact dates remain unknown where stated):")
    for name, source in report["sources"].items():
        print(f"{name}: {source.get('url') or source.get('source_path')}; "
              f"source date {source['source_date']} ({source['date_kind']}); "
              f"accessed {source['access_date']}; {source['locator']}; {source['scope']}")
    print("EXTRAPOLATION INPUT LEDGER (assumptions have no source URL; declared 2026-10-31):")
    for name, item in report["inputs"].items():
        val = item["value"] if name != "raw_category_counts" else "reconstructed totals in JSON"
        print(f"{name}: {val} [{item['unit']}] {item['status']}; {item['note']}")
    for note in report["limits"]:
        print(note)
