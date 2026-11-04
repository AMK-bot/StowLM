#!/usr/bin/env python3
"""Render only recorded results; no inference, fitting, or experiment execution."""

import argparse
import hashlib
from pathlib import Path

import pipeline_charts
import reader_charts


ROOT = Path(__file__).resolve().parents[3]
MODULES = (pipeline_charts, reader_charts)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate(output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    charts = {}
    chart_sources = {}
    for module in MODULES:
        rendered = module.build(ROOT)
        for name, svg in rendered.items():
            if name in charts:
                raise ValueError(f"Duplicate chart filename: {name}")
            charts[name] = svg
            chart_sources[name] = tuple(getattr(module, "CHART_SOURCES", {}).get(name, module.SOURCES))
            (output_dir / name).write_text(svg, encoding="utf-8")

    lines = [
        "# Recorded results charts",
        "",
        "Static, self-contained SVGs for the StowLM site and README. No new model or planning runs.",
        "The opaque white canvas keeps labels readable when embedded on light or dark pages;",
        "labels and marker shapes supplement a colour-blind-safe palette. No external fonts or scripts.",
        "",
        "Regenerate from the repository root:",
        "",
        "```sh",
        "UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python experiments/mvp/charts/generate.py",
        "UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python -m unittest discover -s experiments/mvp/charts -p 'test_*.py' -v",
        "```",
        "",
        "`--output-dir PATH` writes a fresh set elsewhere. Python standard library only.",
        "Intervals are the recorded exact two-sided Clopper–Pearson 95% intervals, not newly estimated intervals.",
        "The tests regenerate all charts, compare bytes, compare visible values with the recorded sources,",
        "and independently derive each row's marker, interval and bar geometry from source numbers and axis ticks.",
        "",
        "## Charts and sources",
        "",
        "Paths below are relative to the repository root; SHA-256 covers the exact source bytes.",
        "Embedded SVG metadata also records the plotted values and source provenance.",
        "",
    ]
    for name in sorted(charts):
        lines += [f"### [{name}]({name})", "", f"SVG SHA-256: `{sha256(output_dir / name)}`", ""]
        for source in sorted(set(chart_sources[name])):
            lines += [f"- `{source}` — `{sha256(ROOT / source)}`"]
        lines += [""]
    lines += [
        "## Claim labels and assumptions",
        "",
        f"- `docs/headline.md` — `{sha256(ROOT / 'docs/headline.md')}`",
        "- Original 3.1 results report frozen pipeline v3, held-out maps after two interrupted attempts;",
        "  they are not the pre-registered sealed claim. Interrupted attempts are disclosed and never pooled.",
        "- `ext1-passes.svg` shows 3.1 beside the fresh held-out extension, run after the freeze;",
        "  not the pre-registered sealed claim. The extension ran on v4, with planning code byte-identical to v3.",
        "  All extension and secondary pooled numbers come only from ext-1/FIGURES.json.",
        "- `instruction-binding.svg` keeps the 3.1 binding result beside the fresh extension result.",
        "  Binding requires a complete fresh-audited L0 baseline that breaks the intended rule,",
        "  followed by a complete, obedient, fresh-audited instructed L0 pass; already-obeyed cases stay separate.",
        "- Pooled rows are SECONDARY descriptive figures combining completed 3.1 and ext-1 envelope maps,",
        "  not one experiment. They exclude interrupted attempts, tiny/small maps and sensitivity arms.",
        "- Tiny/small results distinguish 500–800 TEU from 1,700 TEU; the latter is outside the envelope.",
        "  Envelope results refer to a half-ship section, up to 195 boxes, up to 70% fill,",
        "  empty on arrival with one loading call. Synthetic ships and cargo, simulated port.",
        "- Full-checker passes are L0; relaxed plans are excluded. No lift saving is claimed.",
        "- Reader results are the spent sealed-v3 set; Spark is the fixed original reference, not rerun.",
        "  D266 adopted v3 despite the earlier retention decision; R1 passes, R2 and R3 fail.",
        "- Mask results are validation diagnostics of first-choice overrides, with terminal-token swaps excluded.",
        "  Almost all concern output format, none an illegal bay or tier; both residual sentences should",
        "  have been refused, and legal output can still be wrong, so a person confirms.",
        "",
    ]
    (output_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")
    return sorted(charts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    for name in generate(args.output_dir):
        print(args.output_dir / name)


if __name__ == "__main__":
    main()
