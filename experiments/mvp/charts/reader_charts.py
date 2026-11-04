"""Reader and decode-mask charts from recorded evidence; no inference or reruns."""

from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path

from shared_svg import Svg


ADOPTION = "experiments/mvp/reader/v3/results-final/final-adoption-d266.json"
SUMMARY = "experiments/mvp/reader/v3/results-final/sealed-summary.json"
PARSER = "experiments/mvp/reader/v3/parser/sealed-v3-report.json"
SPARK = "experiments/mvp/reader/v3/sealed/spark-summary.json"
MASK = "experiments/mvp/reader/v3/results-final/mask-study/report-base.json"
MASK_DETAIL = "experiments/mvp/reader/v3/results-final/mask-study/report.json"
HEADLINE = "docs/headline.md"
CHART_SOURCES = {
    "reader-exact-match.svg": (ADOPTION, SUMMARY, PARSER, SPARK, HEADLINE),
    "decode-mask-overrides.svg": (MASK, MASK_DETAIL, HEADLINE),
}
SOURCES = tuple(dict.fromkeys(p for paths in CHART_SOURCES.values() for p in paths))

BLUE = "#0072B2"
ORANGE = "#B94B00"
PURPLE = "#70409A"
GREY = "#536574"


def _read(root: Path, name: str) -> dict:
    return json.loads((root / name).read_text())


def _metadata(svg: Svg, root: Path, chart: str, facts: dict) -> None:
    sources = [
        {"path": p, "sha256": hashlib.sha256((root / p).read_bytes()).hexdigest()}
        for p in CHART_SOURCES[chart]
    ]
    payload = json.dumps({"sources": sources, "facts": facts}, sort_keys=True)
    svg.raw('<metadata id="chart-data">' + html.escape(payload) + "</metadata>")


def _counts(data: dict) -> dict:
    count = data["count"]
    exact = data["exact_match_count"]
    return {"count": count, "exact_match_count": exact,
            "exact_match_pct": exact / count * 100}


def _axis(svg: Svg, x: float, y: float, width: float, maximum: int, label: str) -> None:
    svg.line(x, y, x + width, y, stroke="#536574", width=1.5)
    for i in range(6):
        value = maximum * i / 5
        px = x + width * i / 5
        svg.line(px, y, px, y + 6, stroke="#536574")
        svg.text(px, y + 28, f"{value:g}", size=16, anchor="middle")
    svg.text(x + width / 2, y + 61, label, size=18, anchor="middle")


def _reader(root: Path) -> str:
    adoption, summary = _read(root, ADOPTION), _read(root, SUMMARY)
    adopted = dict(adoption["sealed_128"]["swa-last-5"])
    untrained = dict(adoption["sealed_128"]["untrained"])
    v2 = dict(adoption["sealed_128"]["adapter-v2"])
    parser, spark = _counts(_read(root, PARSER)), _counts(_read(root, SPARK))
    untrained.update(_counts(untrained))
    gates = summary["gates"]
    criteria = gates["criteria"]
    spark_min = spark["exact_match_pct"] - criteria["R3_max_shortfall_from_spark_pp"]
    gap = spark["exact_match_pct"] - adopted["exact_match_pct"]
    facts = {"adopted": adopted, "untrained": untrained, "v2": v2,
             "parser": parser, "spark": spark, "gates": gates}
    svg = Svg(1200, 910, "Reader exact match on sealed v3",
              "Recorded sealed-v3 reader counts and gate outcomes. The adopted reader passed R1 and missed R2 and R3.")
    _metadata(svg, root, "reader-exact-match.svg", facts)
    svg.text(36, 47, "Reader exact match on sealed v3", size=29, weight="bold")
    svg.text(36, 80, "Locked before tuning; scored once. Recorded results. Synthetic ships and cargo, simulated port.", size=17)
    svg.text(36, 109, "Glimmer: same model, same laptop, with and without fine-tuning, under the same mask.", size=17)
    svg.text(36, 140, f"Muse Spark reference: {spark['exact_match_count']}/{spark['count']} ({spark['exact_match_pct']:.2f}%)", size=17, fill=PURPLE, weight="bold")
    svg.text(36, 168, "Fixed original Spark reference; not rerun under the updated decoder.", size=16)

    left, width, baseline = 285, 750, 464
    project = lambda pct: left + width * pct / 100
    for tick in range(0, 101, 20):
        svg.line(project(tick), 207, project(tick), baseline, stroke="#e1e6eb")
    svg.line(project(criteria["R1_min_exact_pct"]), 204,
             project(criteria["R1_min_exact_pct"]), baseline, stroke=BLUE, width=2, dash="4 5")
    svg.line(project(spark_min), 204, project(spark_min), baseline, stroke=ORANGE, width=2, dash="2 5")
    svg.line(project(spark["exact_match_pct"]), 204,
             project(spark["exact_match_pct"]), baseline, stroke=PURPLE, width=2.5, dash="9 5")
    for y, label, item, color in [
        (250, "Adopted v3 Reader", adopted, BLUE),
        (335, "Untrained Glimmer", untrained, GREY),
        (420, "No-model parser", parser, ORANGE),
    ]:
        pct = item["exact_match_pct"]
        svg.text(36, y + 6, label, size=20, weight="bold")
        svg.rect(left, y - 17, project(pct) - left, 34, fill=color, rx=3)
        svg.text(1156, y + 6, f"{item['exact_match_count']}/{item['count']}", size=20, anchor="end", weight="bold")
        svg.text(1156, y + 30, f"{pct:.2f}%", size=16, anchor="end")
    svg.text(36, 199, "Reader / comparator", size=16)
    svg.text(1156, 199, "Exact / sentences", size=16, anchor="end")
    _axis(svg, left, baseline, width, 100, "Exact-match sentences (%)")

    svg.rect(36, 550, 1128, 175, fill="#f2f5f7", rx=8)
    svg.text(55, 579, "Adopted v3: passed the 90% gate; missed both stricter gates.", size=21, weight="bold")
    svg.line(58, 607, 93, 607, stroke=BLUE, width=2, dash="4 5")
    svg.text(109, 613, f"R1 PASS: {adopted['exact_match_pct']:.2f}% exact; minimum {criteria['R1_min_exact_pct']:g}% (blue dashed line).", size=18)
    svg.text(58, 650, f"R2 FAIL: {adopted['false_acceptance_count']}/{adopted['loosening_count']} loosening sentences accepted; maximum {criteria['R2_max_false_acceptances']} allowed.", size=18)
    svg.line(58, 680, 93, 680, stroke=ORANGE, width=2, dash="2 5")
    svg.text(109, 687, f"R3 FAIL: {gap:.2f} points below Spark; maximum {criteria['R3_max_shortfall_from_spark_pp']:g} allowed.", size=18)
    svg.text(109, 712, f"R3 minimum {spark_min:.2f}% exact (orange dotted line); Spark is purple, long dashed.", size=16)

    svg.text(36, 768, f"Adoption context (D266): v3 {adopted['exact_match_count']}/{adopted['count']}, {adopted['false_acceptance_count']}/{adopted['loosening_count']} loosening accepted; v2 {v2['exact_match_count']}/{v2['count']}, {v2['false_acceptance_count']}/{v2['loosening_count']} loosening accepted.", size=19, weight="bold")
    svg.text(36, 799, "v3 was adopted for better refusal of unsupported sentences, with two fewer exact matches.", size=17)
    svg.text(36, 844, "Sources: final-adoption-d266.json; sealed-summary.json; parser/sealed-v3-report.json; sealed/spark-summary.json.", size=15)
    svg.text(36, 871, "Source paths and SHA-256 hashes are embedded and listed in the chart README.", size=15)
    return svg.finish()


def _mask(root: Path) -> str:
    report = _read(root, MASK)
    before, after = report["untrained"], report["fine_tuned"]
    facts = {name: {"count": arm["sentences"], "overrides": arm["sentences_top_changed"]}
             for name, arm in (("untrained", before), ("fine_tuned", after))}
    svg = Svg(1200, 720, "Decode-mask overrides before and after fine-tuning",
              "Recorded validation study, not sealed accuracy. Almost all overrides concerned output format; neither arm had an illegal bay or tier. Both remaining overrides concerned sentences the reader should have refused.")
    _metadata(svg, root, "decode-mask-overrides.svg", facts)
    svg.text(36, 46, "Decode-mask overrides before and after fine-tuning", size=28, weight="bold")
    svg.text(36, 79, "Validation sentences; recorded results. Synthetic ships and cargo, simulated port.", size=17)
    svg.text(36, 112, "almost all were the output format, not ship facts; none was an illegal bay or tier", size=18, weight="bold")
    svg.text(36, 145, "both of the 2 were sentences it should have refused; what's left is legal and still wrong,", size=18, weight="bold")
    svg.text(36, 173, "which is why a person confirms.", size=18, weight="bold")
    svg.text(36, 208, "A sentence is counted when the mask overrode the model's first choice at least once.", size=17)
    left, width, baseline = 300, 730, 494
    for value in range(0, 201, 40):
        x = left + width * value / 200
        svg.line(x, 256, x, baseline, stroke="#e1e6eb")
    svg.text(36, 254, "Model / training", size=16)
    svg.text(1158, 254, "Sentences / total", size=16, anchor="end")
    for y, label, arm, color in [(310, "Before: untrained", before, GREY),
                                  (426, "After: tuned v3", after, BLUE)]:
        overrides = arm["sentences_top_changed"]
        svg.text(36, y + 7, label, size=20, weight="bold")
        svg.rect(left, y - 24, width * overrides / 200, 48, fill=color, rx=2)
        svg.text(1158, y + 7, f"{overrides}/{arm['sentences']}", size=26, weight="bold", anchor="end")
    _axis(svg, left, baseline, width, 200, "Validation sentences with at least one override (count)")
    svg.rect(36, 590, 1128, 65, fill="#f2f5f7", rx=8)
    svg.text(54, 617, "This measures intervention by the decode mask, not correctness of the model's reading.", size=18)
    svg.text(54, 643, "End-token swaps are excluded; the same 200-sentence validation sample is used in both arms.", size=17)
    svg.text(36, 688, "Source: results-final/mask-study/report-base.json; full source paths and SHA-256 hashes in the chart README.", size=15)
    return svg.finish()


def build(root: Path) -> dict[str, str]:
    """Return deterministic SVG documents; only read existing recorded files."""
    return {"reader-exact-match.svg": _reader(root), "decode-mask-overrides.svg": _mask(root)}
