"""Planning charts from recorded 3.1 results and curated ext-1 figures."""

from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
import textwrap

from shared_svg import Svg


BASE = "experiments/mvp/pipeline/sealed/runs/final-3/"
REPORT = BASE + "report.json"
STORY = BASE + "story-figures.json"
SOURCES = (REPORT, STORY, "docs/headline.md")
FIGURES = "experiments/mvp/pipeline/sealed/measurements/ext-1/FIGURES.json"
CHART_SOURCES = {
    "pipeline-passes.svg": SOURCES,
    "instruction-binding.svg": SOURCES + (FIGURES,),
    "ext1-passes.svg": (FIGURES, "docs/headline.md"),
}
BLUE = "#0072b2"
ORANGE = "#b54700"
MUTED = "#465568"
MIXED_VERSION_HEADER = "3.1 on frozen v3 (a11b4202…); ext-1 on v4 (acf2f275…), planning code byte-identical"

PARTIAL_RUN = (
    "Two earlier attempts on the same maps stopped early when our own frozen-code guard tripped. "
    "In the first, a test rewrote a pinned fixture; in the second, the engine wrote scratch files "
    "inside its pinned folder. No planning code changed. They completed 36 and 91 rows; both are "
    "published as they are and not pooled with the final run. Where the same rows ran again in the "
    "final run, attempt 1 matched the final run on 36 of the 38 rows it opened (status and lifts; "
    "37 of 38 on verdict alone) and attempt 2 on 91 of 91, reported separately."
)
ENVELOPE = (
    "Envelope: up to 195 boxes in a half-ship section of 500–800 TEU feeder ships, at most 70% of "
    "that section; empty section on arrival, one loading call. Other bays hold fixed synthetic cargo."
)


def _resolve(data: dict, pointer: str):
    value = data
    for token in pointer.strip("/").split("/"):
        value = value[token.replace("~1", "/").replace("~0", "~")]
    return value


def _paragraph(svg: Svg, text: str, y: float, *, size=15, width=146, fill=MUTED):
    """Wrap captions into explicit SVG text; no browser layout dependency."""
    for line in textwrap.wrap(text, width=width):
        svg.text(40, y, line, size=size, fill=fill)
        y += size + 7
    return y


def _metadata(svg: Svg, root: Path, chart: str, facts: dict):
    value = {
        "chart": chart,
        "sources": [
            {"path": path, "sha256": hashlib.sha256((root / path).read_bytes()).hexdigest()}
            for path in CHART_SOURCES[chart + ".svg"]
        ],
        "facts": facts,
    }
    svg.raw('<metadata id="chart-data">' + html.escape(json.dumps(value, sort_keys=True)) + '</metadata>')


def _header(svg: Svg, title: str, label: str, version_header=None):
    svg.text(40, 46, title, size=29, weight="bold")
    y = _paragraph(svg, label, 78, size=15, width=145)
    svg.text(40, y + 3, "Synthetic ships and cargo, simulated port · planning step from a clean load list", size=16)
    svg.text(40, y + 28, version_header or "Templated sentences · frozen standard profile · frozen pipeline v3 (a11b4202…)", size=15, fill=MUTED)


def _axis(svg: Svg, left: float, right: float, top: float, bottom: float, label: str):
    for p in range(0, 101, 20):
        x = left + (right - left) * p / 100
        svg.line(x, top, x, bottom, stroke="#dde3ea", dash="3 5")
        svg.text(x, bottom + 25, str(p), size=15, anchor="middle")
    svg.line(left, bottom, right, bottom, stroke="#657487")
    svg.text((left + right) / 2, bottom + 55, label, size=17, anchor="middle")


def _interval(svg: Svg, left: float, right: float, y: float, row: dict, color: str, square=False):
    lo, hi = row["exact95"]
    a, b = left + (right - left) * lo, left + (right - left) * hi
    x = left + (right - left) * row["passes"] / row["n"]
    svg.line(a, y, b, y, stroke=color, width=3)
    for end in (a, b):
        svg.line(end, y - 6, end, y + 6, stroke=color, width=2)
    if square:
        svg.rect(x - 5, y - 5, 10, 10, color)
    else:
        svg.circle(x, y, 6, color)
    return f'{row["passes"]}/{row["n"]} · n = {row["n"]} · {lo * 100:.1f}–{hi * 100:.1f}%'


def _row(report: dict, cohort: str, ship: str, arm: str, prefix: str):
    data = _resolve(report, prefix + "/arms/" + arm)
    pointer = prefix + "/arms/" + arm
    return {
        "cohort": cohort,
        "ship": ship,
        "arm": arm,
        "passes": data["passes_L0"],
        "n": data["expected_maps"],
        "exact95": data["passes_L0_exact95"],
        "source": {"path": REPORT, "json_pointer": pointer},
    }


def _footer(svg: Svg, y: float):
    y = _paragraph(svg, ENVELOPE, y)
    y = _paragraph(svg, "1,700 TEU test maps are outside this envelope wording. Cohorts are separate; no tiny/small maps are pooled with the envelope.", y + 9)
    y = _paragraph(svg, PARTIAL_RUN, y + 13)
    _paragraph(svg, "Source: final-3/report.json and story-figures.json · recorded results only; no planning rerun", y + 13, size=14)


def _passes(root: Path, report: dict, label: str):
    groups = [
        ("tiny15", "500-800_TEU", "Tiny · 500–800 TEU", "/cohorts/tiny15/ship_groups/500-800_TEU"),
        ("tiny15", "1700_TEU", "Tiny · 1,700 TEU", "/cohorts/tiny15/ship_groups/1700_TEU"),
        ("small30", "500-800_TEU", "Small · 500–800 TEU", "/cohorts/small30/ship_groups/500-800_TEU"),
        ("small30", "1700_TEU", "Small · 1,700 TEU", "/cohorts/small30/ship_groups/1700_TEU"),
        ("envelope24", "feeder_500", "Envelope · 500 TEU", "/cohorts/envelope24/by_ship/feeder_500"),
        ("envelope24", "feeder_800", "Envelope · 800 TEU", "/cohorts/envelope24/by_ship/feeder_800"),
        ("envelope24", "all", "Envelope · total (same maps)", "/cohorts/envelope24"),
    ]
    svg = Svg(1320, 1280, "Full-checker passes by cohort and ship size", label + ". " + PARTIAL_RUN)
    _header(svg, "Full-checker passes by cohort and ship size", label)
    svg.circle(48, 161, 6, BLUE)
    svg.text(64, 167, "Pipeline (no voyage instruction)", size=16, fill=BLUE)
    svg.rect(380, 156, 10, 10, ORANGE)
    svg.text(399, 167, "Simple two-step planner", size=16, fill=ORANGE)
    svg.text(40, 197, "Cohort and ship size", size=16, weight="bold")
    svg.text(960, 197, "Passes / maps · n · exact 95% interval", size=15, weight="bold")
    svg.text(40, 218, "1,700 TEU: outside envelope wording", size=14, fill=MUTED)
    left, right, top, bottom = 390, 920, 236, 826
    _axis(svg, left, right, top, bottom, "Full-checker pass rate (%)")
    rows = []
    for index, (cohort, ship, name, prefix) in enumerate(groups):
        y = 263 + index * 82
        if index in (2, 4, 6):
            svg.line(40, y - 32, 1280, y - 32, stroke="#cbd5e1")
        svg.text(40, y + 4, name, size=18, weight="bold" if index == 6 else "normal")
        if index == 6:
            svg.text(40, y + 25, "Summary of the two envelope rows above", size=14, fill=MUTED)
        for arm, offset, color in (("pipeline", -10, BLUE), ("twostep", 16, ORANGE)):
            row = _row(report, cohort, ship, arm, prefix)
            rows.append(row)
            caption = _interval(svg, left, right, y + offset, row, color, square=arm == "twostep")
            svg.text(960, y + offset + 5, caption, size=16, fill=color)
    y = _paragraph(svg, "Dots / squares show observed rates; bars show exact two-sided Clopper–Pearson 95% intervals from the report.", 913, size=15)
    y = _paragraph(svg, "Pass means L0: full checker, completion, every confirmed instruction obeyed. Relaxed plans are not passes.", y + 3, size=15)
    y = _paragraph(svg, "Two-step: a simple two-step planner, modelled from one field expert's description. No like-for-like lift comparison is available.", y + 3, size=15)
    _footer(svg, y + 20)
    _metadata(svg, root, "pipeline-passes", {"rows": rows, "label": label})
    return svg.finish()


def _figure_value(value):
    """FIGURES wraps original observations, but pooled values are plain JSON."""
    return value["value"] if isinstance(value, dict) and "value" in value else value


def _figure_observation(figures: dict, pointer: str):
    data = _resolve(figures, pointer)
    return {
        "passes": _figure_value(data["numerator"]),
        "n": _figure_value(data["denominator"]),
        "exact95": _figure_value(data["exact95"]),
        "source": {"path": FIGURES, "json_pointer": pointer},
    }


def _extension_footer(svg: Svg, figures: dict, y: float):
    y = _paragraph(svg, ENVELOPE, y)
    y = _paragraph(svg, "Confirmation is exact expected-rule match, not a human planner confirmation. These are templated sentences.", y + 9)
    y = _paragraph(svg, "SECONDARY pooling combines completed 3.1 and ext-1 envelope maps only; it is not one experiment. No tiny/small maps or interrupted attempts enter the sum.", y + 9)
    y = _paragraph(svg, figures["secondary_pooled"]["scope"], y + 9)
    y = _paragraph(svg, _figure_value(figures["original_partial_run_disclosure"]), y + 13)
    _paragraph(svg, "Source: measurements/ext-1/FIGURES.json · recorded results only; no planning or checker rerun", y + 13, size=14)


def _instruction(root: Path, report: dict, label: str, figures: dict):
    binding_pointer = "/cohorts/envelope24/instruction_binding"
    binding = _resolve(report, binding_pointer)
    broad_pointer = "/cohorts/envelope24/arms/instruction"
    broad = _resolve(report, broad_pointer)
    facts = {
        "binding": {
            "passes": binding["binding_obedient_passes"],
            "n": binding["rule_broken_maps"],
            "exact95": binding["binding_obedient_exact95"],
            "source": {"path": REPORT, "json_pointer": binding_pointer},
        },
        "broad": {
            "passes": broad["passes_L0"],
            "n": broad["expected_maps"],
            "exact95": broad["passes_L0_exact95"],
            "source": {"path": REPORT, "json_pointer": broad_pointer},
        },
        "already_obeyed": binding["already_obeyed"],
        "label": label,
        "extension_binding": _figure_observation(figures, "/primary/binding"),
        "secondary_binding": _figure_observation(figures, "/secondary_pooled/binding"),
        "extension_label": _figure_value(figures["primary"]["label"]),
        "secondary_label": figures["secondary_pooled"]["label"],
    }
    svg = Svg(1320, 1400, "Instruction binding: original and fresh extension", label + ". " + facts["extension_label"] + ". " + PARTIAL_RUN)
    _header(svg, "Instruction binding: changed a plan that broke the rule", label, MIXED_VERSION_HEADER)
    _paragraph(svg, facts["extension_label"], 158, size=15)
    svg.text(40, 197, "Population / result", size=16, weight="bold")
    svg.text(960, 197, "Passes / maps · n · exact 95% interval", size=15, weight="bold")
    left, right = 450, 920
    _axis(svg, left, right, 210, 714, "Checked, instruction-obedient plans (%)")
    svg.raw('<g id="binding-original">')
    svg.text(40, 239, "Binding subset", size=20, weight="bold")
    svg.text(40, 265, "3.1 · first plan broke the rule", size=17)
    svg.text(40, 289, "New checked plan obeyed it", size=15, fill=MUTED)
    caption = _interval(svg, left, right, 258, facts["binding"], BLUE)
    svg.text(960, 264, caption, size=16, fill=BLUE)
    svg.raw('</g><g id="binding-ext1">')
    svg.text(40, 349, "Ext-1 binding", size=20, weight="bold")
    svg.text(40, 375, "Fresh maps · first plan broke the rule", size=17)
    svg.text(40, 399, "New checked plan obeyed it", size=15, fill=MUTED)
    caption = _interval(svg, left, right, 368, facts["extension_binding"], BLUE)
    svg.text(960, 374, caption, size=16, fill=BLUE)
    svg.raw('</g><g id="binding-broad-original">')
    svg.text(40, 459, "Broader count", size=20, weight="bold")
    svg.text(40, 485, "3.1 · all instructed envelope voyages", size=17)
    svg.text(40, 509, "Includes first plans already obeying", size=15, fill=MUTED)
    caption = _interval(svg, left, right, 478, facts["broad"], ORANGE, square=True)
    svg.text(960, 484, caption, size=16, fill=ORANGE)
    svg.raw('</g>')
    svg.line(40, 548, 1280, 548, stroke="#cbd5e1")
    _paragraph(svg, facts["secondary_label"], 575, size=14)
    svg.raw('<g id="binding-secondary">')
    svg.text(40, 629, "SECONDARY pooled binding", size=20, weight="bold")
    svg.text(40, 655, "3.1 + ext-1 · descriptive arithmetic", size=17)
    svg.text(40, 679, "First plans that broke the rule only", size=15, fill=MUTED)
    caption = _interval(svg, left, right, 648, facts["secondary_binding"], MUTED)
    svg.text(960, 654, caption, size=16, fill=MUTED)
    svg.raw('</g>')
    y = _paragraph(svg, "Exact two-sided Clopper–Pearson 95% intervals. Binding requires a same-map no-rule baseline that is a complete, fresh-audited L0 pass and fails the intended-rule recheck; the rule and hash must match the instruction allocation.", 810, size=15)
    y = _paragraph(svg, "The numerator requires a complete, obedient, fresh-audited instructed L0 pass. Already-obeyed and unavailable counterfactuals remain separate (code15 binding-definition erratum).", y + 8, size=15)
    y = _paragraph(svg, "In 3.1, on the other 6 of 24 voyages, the first plan already obeyed the rule, which proves nothing about instruction binding.", y + 8, size=16, width=137)
    y = _paragraph(svg, "Confirmation here is exact expected-rule match against an answer key, not a human planner confirmation; these were templated sentences.", y + 8, size=15)
    y = _paragraph(svg, "The 3.1 broader count, 18/24, is a different denominator; it is not the binding result. Passes require a full checker pass and completion.", y + 8, size=15)
    y = _paragraph(svg, "3.1 uses frozen v3 (a11b4202…); ext-1 uses frozen v4 (acf2f275…), with byte-identical planning code.", y + 8, size=15)
    _extension_footer(svg, figures, y + 22)
    _metadata(svg, root, "instruction-binding", facts)
    return svg.finish()


def _extension_passes(root: Path, figures: dict):
    original_label = _figure_value(figures["separate_original_3_1"]["label"])
    extension_label = _figure_value(figures["primary"]["label"])
    secondary_label = figures["secondary_pooled"]["label"]
    svg = Svg(1320, 1260, "Envelope full-checker passes: original and fresh extension", original_label + ". " + extension_label + ". " + secondary_label)
    svg.text(40, 46, "Envelope full-checker passes: original and fresh extension", size=29, weight="bold")
    svg.text(40, 82, "Synthetic ships and cargo, simulated port · planning step from a clean load list", size=16)
    svg.text(40, 109, MIXED_VERSION_HEADER, size=15, fill=MUTED)
    svg.circle(48, 145, 6, BLUE)
    svg.text(64, 151, "Pipeline (no voyage instruction)", size=16, fill=BLUE)
    svg.rect(380, 140, 10, 10, ORANGE)
    svg.text(399, 151, "Simple two-step planner", size=16, fill=ORANGE)
    svg.text(40, 191, "Separate experiments / secondary sum", size=16, weight="bold")
    svg.text(960, 191, "Passes / maps · n · exact 95% interval", size=15, weight="bold")
    left, right = 390, 920
    _axis(svg, left, right, 210, 730, "Full-checker pass rate (%)")
    groups = [
        ("original_3_1", "Original 3.1", "separate_original_3_1", 246, original_label),
        ("ext1", "Fresh Ext-1", "primary", 416, extension_label),
        ("secondary_pooled", "SECONDARY pooled", "secondary_pooled", 636, secondary_label),
    ]
    rows = []
    for cohort, name, key, label_y, run_label in groups:
        if cohort == "secondary_pooled":
            svg.line(40, 552, 1280, 552, stroke="#cbd5e1")
            svg.text(40, 580, "Secondary description of the two completed experiments above", size=16, weight="bold")
        svg.raw(f'<g id="passes-{cohort}">')
        svg.text(40, label_y, name, size=21, weight="bold")
        for arm, offset, color in (("pipeline", -14, BLUE), ("twostep", 12, ORANGE)):
            row = _figure_observation(figures, f"/{key}/arms/{arm}")
            row.update({"cohort": cohort, "arm": arm})
            rows.append(row)
            caption = _interval(svg, left, right, label_y + offset, row, color, square=arm == "twostep")
            svg.text(960, label_y + offset + 5, caption, size=16, fill=color)
        _paragraph(svg, run_label, label_y + 61, size=15)
        svg.raw('</g>')
    y = _paragraph(svg, "Dots / squares show observed rates; bars show recorded exact two-sided Clopper–Pearson 95% intervals.", 821, size=15)
    y = _paragraph(svg, "Pass means L0: full checker and completion. Relaxed plans are not passes. All registered maps remain in each denominator.", y + 6, size=15)
    y = _paragraph(svg, "Two-step: a simple two-step planner, modelled from one field expert's description.", y + 6, size=15)
    _extension_footer(svg, figures, y + 20)
    _metadata(svg, root, "ext1-passes", {
        "rows": rows, "label": extension_label,
        "original_label": original_label, "secondary_label": secondary_label,
    })
    return svg.finish()


def build(root: Path) -> dict[str, str]:
    report = json.loads((root / REPORT).read_text())
    story = json.loads((root / STORY).read_text())
    figures = json.loads((root / FIGURES).read_text())
    label = report["label"]
    if story["run"]["label"]["value"] != label:
        raise ValueError("Story and report run labels differ")
    # The story's observations are pinned to this exact report; reject drift.
    report_sha = hashlib.sha256((root / REPORT).read_bytes()).hexdigest()
    if story["run"]["label"]["source"]["sha256"] != report_sha:
        raise ValueError("Story provenance does not match report bytes")
    return {
        "pipeline-passes.svg": _passes(root, report, label),
        "instruction-binding.svg": _instruction(root, report, label, figures),
        "ext1-passes.svg": _extension_passes(root, figures),
    }
