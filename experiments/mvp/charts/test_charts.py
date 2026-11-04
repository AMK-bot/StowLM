"""Source-backed checks for the published static charts; no model or solver runs.

Run from the repository root:
    UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python -m unittest discover -s experiments/mvp/charts -p 'test_*.py'
"""

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET


CHARTS = Path(__file__).resolve().parent
ROOT = CHARTS.parents[2]
PIPELINE = "experiments/mvp/pipeline/sealed/runs/final-3/"
EXT_FIGURES = "experiments/mvp/pipeline/sealed/measurements/ext-1/FIGURES.json"
READER = "experiments/mvp/reader/v3/"
FINAL = READER + "results-final/"
SVG = "{http://www.w3.org/2000/svg}"
FILENAMES = {
    "pipeline-passes.svg", "instruction-binding.svg",
    "reader-exact-match.svg", "decode-mask-overrides.svg",
    "ext1-passes.svg",
}


def read_json(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def visible_text(svg):
    """Read text nodes only: metadata cannot make an unlabelled number pass."""
    return " ".join(" ".join(node.itertext()) for node in svg.iter(SVG + "text"))


def interval_text(bounds):
    return f"{100 * bounds[0]:.1f}–{100 * bounds[1]:.1f}%"


def figure_values(record):
    """Read the committed chart-ready values, without opening raw Ext-1 runs."""
    return (record["numerator"]["value"], record["denominator"]["value"],
            record["exact95"]["value"])


class ChartsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="stowlm-charts-test-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.output = Path(cls.temporary.name)
        result = subprocess.run(
            [sys.executable, str(CHARTS / "generate.py"), "--output-dir", str(cls.output)],
            cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode:
            raise AssertionError(f"Chart generation failed:\n{result.stdout}\n{result.stderr}")
        cls.svgs = {name: ET.parse(cls.output / name).getroot() for name in FILENAMES}
        cls.metadata = {}
        for name, svg in cls.svgs.items():
            nodes = list(svg.iter(SVG + "metadata"))
            if len(nodes) != 1:
                raise AssertionError(f"{name}: expected one provenance metadata node")
            cls.metadata[name] = json.loads(nodes[0].text)

    def _one(self, nodes, context):
        self.assertEqual(len(nodes), 1, context)
        return nodes[0]

    def _label(self, svg, label):
        return self._one([
            node for node in svg.iter(SVG + "text")
            if "".join(node.itertext()) == label
        ], f"unique visible label: {label}")

    def _scale(self, svg, maximum):
        """Calibrate from visible ticks, independently of renderer/metadata."""
        for node in svg.iter():
            self.assertNotIn("transform", node.attrib, "unhandled coordinate transform")
        ticks = [node for node in svg.iter(SVG + "text")
                 if node.get("text-anchor") == "middle"
                 and re.fullmatch(r"\d+", node.text or "")]
        zero = self._one([n for n in ticks if n.text == "0"], "zero tick")
        end = self._one([n for n in ticks if n.text == str(maximum)], "maximum tick")
        left, right = float(zero.get("x")), float(end.get("x"))
        self.assertGreater(right, left)
        for tick in ticks:
            self.assertAlmostEqual(float(tick.get("y")), float(zero.get("y")))
            self.assertAlmostEqual(float(tick.get("x")),
                                   left + (right - left) * float(tick.text) / maximum)
        # Tick text alone must not conceal a collapsed or shifted axis line.
        self._one([
            line for line in svg.iter(SVG + "line")
            if float(line.get("x1")) == left and float(line.get("x2")) == right
            and float(line.get("y1")) == float(line.get("y2"))
            and 0 < float(zero.get("y")) - float(line.get("y1")) < 40
        ], "axis baseline agrees with ticks")
        return lambda value: left + (right - left) * value / maximum

    def _interval_row(self, svg, label, offset, square, passes, n, bounds, project,
                      caption_offset):
        label_y = float(self._label(svg, label).get("y"))
        y = label_y + offset
        tag = "rect" if square else "circle"
        marks = [node for node in svg.iter(SVG + tag)
                 if abs((float(node.get("y", "0")) + float(node.get("height")) / 2
                         if square else float(node.get("cy"))) - y) < 0.01]
        mark = self._one(marks, f"{label}: unique {tag}")
        x = (float(mark.get("x")) + float(mark.get("width")) / 2
             if square else float(mark.get("cx")))
        self.assertAlmostEqual(x, project(100 * passes / n), msg=f"{label}: point x")
        if square:
            self.assertAlmostEqual(float(mark.get("width")), 10)
            self.assertAlmostEqual(float(mark.get("height")), 10)
        else:
            self.assertGreater(float(mark.get("r")), 0)
        interval = self._one([
            line for line in svg.iter(SVG + "line")
            if float(line.get("y1")) == y == float(line.get("y2"))
        ], f"{label}: unique horizontal interval")
        expected = [project(100 * value) for value in bounds]
        for attr, value in zip(("x1", "x2"), expected):
            self.assertAlmostEqual(float(interval.get(attr)), value,
                                   msg=f"{label}: interval {attr}")
        caps = [line for line in svg.iter(SVG + "line")
                if float(line.get("y1")) == y - 6
                and float(line.get("y2")) == y + 6]
        self.assertEqual(len(caps), 2, f"{label}: two interval caps")
        caps.sort(key=lambda line: float(line.get("x1")))
        for cap, value in zip(caps, expected):
            self.assertAlmostEqual(float(cap.get("x1")), value,
                                   msg=f"{label}: cap x1")
            self.assertAlmostEqual(float(cap.get("x2")), value,
                                   msg=f"{label}: cap x2")
        caption = f"{passes}/{n} · n = {n} · {interval_text(bounds)}"
        self._one([
            node for node in svg.iter(SVG + "text")
            if float(node.get("y")) == y + caption_offset and node.text == caption
        ], f"{label}: source values beside their mark")

    def _assert_pipeline_geometry(self, svg):
        report = read_json(PIPELINE + "report.json")["cohorts"]
        project = self._scale(svg, 100)
        groups = [
            ("Tiny · 500–800 TEU", report["tiny15"]["ship_groups"]["500-800_TEU"]),
            ("Tiny · 1,700 TEU", report["tiny15"]["ship_groups"]["1700_TEU"]),
            ("Small · 500–800 TEU", report["small30"]["ship_groups"]["500-800_TEU"]),
            ("Small · 1,700 TEU", report["small30"]["ship_groups"]["1700_TEU"]),
            ("Envelope · 500 TEU", report["envelope24"]["by_ship"]["feeder_500"]),
            ("Envelope · 800 TEU", report["envelope24"]["by_ship"]["feeder_800"]),
            ("Envelope · total (same maps)", report["envelope24"]),
        ]
        for label, group in groups:
            for arm, offset, square in (("pipeline", -14, False), ("twostep", 12, True)):
                raw = group["arms"][arm]
                self._interval_row(svg, label, offset, square, raw["passes_L0"],
                                   raw["expected_maps"], raw["passes_L0_exact95"], project, 5)

    def _assert_instruction_geometry(self, svg):
        cohort = read_json(PIPELINE + "report.json")["cohorts"]["envelope24"]
        binding, broad = cohort["instruction_binding"], cohort["arms"]["instruction"]
        project = self._scale(svg, 100)
        self._interval_row(svg, "Binding subset", 19, False,
                           binding["binding_obedient_passes"], binding["rule_broken_maps"],
                           binding["binding_obedient_exact95"], project, 6)
        self._interval_row(svg, "Broader count", 19, True, broad["passes_L0"],
                           broad["expected_maps"], broad["passes_L0_exact95"], project, 6)
        figures = read_json(EXT_FIGURES)
        for label, section in (("Ext-1 binding", "primary"),
                               ("SECONDARY pooled binding", "secondary_pooled")):
            self._interval_row(svg, label, 19, False,
                               *figure_values(figures[section]["binding"]), project, 6)

    def _extension_pass_rows(self):
        figures = read_json(EXT_FIGURES)
        for cohort, label, section in (
            ("original_3_1", "Original 3.1", "separate_original_3_1"),
            ("ext1", "Fresh Ext-1", "primary"),
            ("secondary_pooled", "SECONDARY pooled", "secondary_pooled"),
        ):
            for arm, offset, square in (("pipeline", -14, False), ("twostep", 12, True)):
                record = figures[section]["arms"][arm]
                yield cohort, label, arm, offset, square, record, f"/{section}/arms/{arm}"

    def _assert_extension_geometry(self, svg):
        project = self._scale(svg, 100)
        for _, label, _, offset, square, record, _ in self._extension_pass_rows():
            self._interval_row(svg, label, offset, square, *figure_values(record), project, 5)

    def _bar_row(self, svg, label, offset, height, count, n, value, project):
        label_y = float(self._label(svg, label).get("y"))
        y = label_y + offset
        bar = self._one([
            rect for rect in svg.iter(SVG + "rect")
            if abs(float(rect.get("y", "0")) + float(rect.get("height")) / 2 - y) < 0.01
        ], f"{label}: unique bar")
        self.assertAlmostEqual(float(bar.get("x")), project(0), msg=f"{label}: bar origin")
        self.assertAlmostEqual(float(bar.get("width")), project(value) - project(0),
                               msg=f"{label}: source-derived bar width")
        self.assertAlmostEqual(float(bar.get("height")), height)
        self._one([
            node for node in svg.iter(SVG + "text")
            if float(node.get("y")) == label_y and node.text == f"{count}/{n}"
        ], f"{label}: count beside its bar")

    def _assert_reader_geometry(self, svg):
        sealed = read_json(FINAL + "final-adoption-d266.json")["sealed_128"]
        project = self._scale(svg, 100)
        for label, raw in (
            ("Adopted v3 Reader", sealed["swa-last-5"]),
            ("Untrained Glimmer", sealed["untrained"]),
            ("No-model parser", read_json(READER + "parser/sealed-v3-report.json")),
        ):
            self._bar_row(svg, label, -6, 34, raw["exact_match_count"], raw["count"],
                          100 * raw["exact_match_count"] / raw["count"], project)
        criteria = read_json(FINAL + "sealed-summary.json")["gates"]["criteria"]
        spark = read_json(READER + "sealed/spark-summary.json")
        spark_pct = 100 * spark["exact_match_count"] / spark["count"]
        for dash, value in (("4 5", criteria["R1_min_exact_pct"]),
                            ("2 5", spark_pct - criteria["R3_max_shortfall_from_spark_pp"]),
                            ("9 5", spark_pct)):
            line = self._one([
                node for node in svg.iter(SVG + "line")
                if node.get("stroke-dasharray") == dash
                and float(node.get("y2")) - float(node.get("y1")) > 200
            ], f"reference/threshold line {dash}")
            self.assertAlmostEqual(float(line.get("x1")), project(value))
            self.assertAlmostEqual(float(line.get("x2")), project(value))

    def _assert_mask_geometry(self, svg):
        raw = read_json(FINAL + "mask-study/report-base.json")
        project = self._scale(svg, 200)
        for label, arm in (("Before: untrained", "untrained"), ("After: tuned v3", "fine_tuned")):
            count, n = raw[arm]["sentences_top_changed"], raw[arm]["sentences"]
            self._bar_row(svg, label, -7, 48, count, n, count, project)

    def test_pipeline_mark_and_interval_geometry(self):
        self._assert_pipeline_geometry(self.svgs["pipeline-passes.svg"])

    def test_instruction_mark_and_interval_geometry(self):
        self._assert_instruction_geometry(self.svgs["instruction-binding.svg"])

    def test_extension_mark_and_interval_geometry(self):
        self._assert_extension_geometry(self.svgs["ext1-passes.svg"])

    def test_reader_bar_and_reference_geometry(self):
        self._assert_reader_geometry(self.svgs["reader-exact-match.svg"])

    def test_mask_bar_geometry(self):
        self._assert_mask_geometry(self.svgs["decode-mask-overrides.svg"])

    def test_geometry_checks_reject_misdrawn_marks(self):
        # Correct text/metadata must not rescue a misdrawn chart. Only mutate copies.
        cases = [
            ("pipeline-passes.svg", self._assert_pipeline_geometry, "circle[@cy='253']", "cx"),
            ("pipeline-passes.svg", self._assert_pipeline_geometry, "rect[@y='274']", "x"),
            ("pipeline-passes.svg", self._assert_pipeline_geometry, "line[@y1='253'][@y2='253']", "x2"),
            ("pipeline-passes.svg", self._assert_pipeline_geometry, "line[@y1='247'][@y2='259']", "x1"),
            ("instruction-binding.svg", self._assert_instruction_geometry, "circle[@cy='258']", "cx"),
            ("instruction-binding.svg", self._assert_instruction_geometry, "rect[@y='473']", "x"),
            ("instruction-binding.svg", self._assert_instruction_geometry, "line[@y1='258'][@y2='258']", "x2"),
            ("reader-exact-match.svg", self._assert_reader_geometry, "rect[@y='233']", "width"),
            ("reader-exact-match.svg", self._assert_reader_geometry, "line[@stroke-dasharray='9 5']", "x1"),
            ("decode-mask-overrides.svg", self._assert_mask_geometry, "rect[@y='402']", "width"),
        ]
        for name, check, selector, attr in cases:
            with self.subTest(chart=name, mark=selector, attribute=attr):
                svg = deepcopy(self.svgs[name])
                node = svg.find(".//" + SVG + selector)
                self.assertIsNotNone(node, "mutation must target an existing plotted mark")
                node.set(attr, str(float(node.get(attr)) + 10))
                with self.assertRaises(AssertionError):
                    check(svg)
        svg = deepcopy(self.svgs["reader-exact-match.svg"])
        adopted = self._label(svg, "Adopted v3 Reader")
        untrained = self._label(svg, "Untrained Glimmer")
        adopted.text, untrained.text = untrained.text, adopted.text
        with self.assertRaises(AssertionError):
            self._assert_reader_geometry(svg)

    def test_extension_geometry_checks_reject_each_misdrawn_row(self):
        # Every new displayed row is checked locally; unchanged facts or a
        # correct caption elsewhere must not conceal a shifted mark or cap.
        cases = [
            ("ext1-passes.svg", self._assert_extension_geometry, label, offset, square, 5)
            for _, label, _, offset, square, _, _ in self._extension_pass_rows()
        ]
        cases += [
            ("instruction-binding.svg", self._assert_instruction_geometry, label, 19, False, 6)
            for label in ("Ext-1 binding", "SECONDARY pooled binding")
        ]
        for name, check, label, offset, square, caption_offset in cases:
            original = self.svgs[name]
            y = float(self._label(original, label).get("y")) + offset
            mark_tag = "rect" if square else "circle"
            mark_attr = "x" if square else "cx"
            candidates = [
                ("point", [node for node in original.iter(SVG + mark_tag)
                           if (float(node.get("y", "0")) + float(node.get("height")) / 2
                               if square else float(node.get("cy"))) == y], mark_attr),
                ("interval", [node for node in original.iter(SVG + "line")
                              if float(node.get("y1")) == y == float(node.get("y2"))], "x2"),
                ("left cap", [node for node in original.iter(SVG + "line")
                              if float(node.get("y1")) == y - 6
                              and float(node.get("y2")) == y + 6], "x1"),
                ("caption", [node for node in original.iter(SVG + "text")
                             if float(node.get("y")) == y + caption_offset
                             and re.match(r"\d+/\d+ · n = ", node.text or "")], None),
            ]
            for kind, nodes, attr in candidates:
                with self.subTest(chart=name, row=label, square=square, mutation=kind):
                    if kind == "left cap":
                        self.assertEqual(len(nodes), 2)
                        node = min(nodes, key=lambda n: float(n.get("x1")))
                    else:
                        node = self._one(nodes, f"existing {label} {kind}")
                    mutated = deepcopy(original)
                    position = list(original.iter()).index(node)
                    target = list(mutated.iter())[position]
                    if attr is None:
                        target.text = "999/999 · n = 999 · 0.0–100.0%"
                    else:
                        target.set(attr, str(float(target.get(attr)) + 10))
                    with self.assertRaises(AssertionError):
                        check(mutated)
        # Swapping labels must also fail even when all source-derived numbers
        # and drawing coordinates remain unchanged.
        for name, check, labels in (
            ("ext1-passes.svg", self._assert_extension_geometry,
             ("Fresh Ext-1", "SECONDARY pooled")),
            ("instruction-binding.svg", self._assert_instruction_geometry,
             ("Ext-1 binding", "SECONDARY pooled binding")),
        ):
            mutated = deepcopy(self.svgs[name])
            first, second = (self._label(mutated, label) for label in labels)
            first.text, second.text = second.text, first.text
            with self.subTest(chart=name, mutation="swapped population labels"):
                with self.assertRaises(AssertionError):
                    check(mutated)

    def test_regeneration_matches_published_bytes(self):
        self.assertEqual({p.name for p in self.output.glob("*.svg")}, FILENAMES)
        for name in sorted(FILENAMES):
            with self.subTest(chart=name):
                self.assertEqual((self.output / name).read_bytes(), (CHARTS / name).read_bytes())
        # The manifest is generated from the same exact source bytes.
        self.assertEqual((self.output / "README.md").read_bytes(), (CHARTS / "README.md").read_bytes())

    def test_provenance_hashes_match_sources_and_manifest(self):
        manifest = (self.output / "README.md").read_text(encoding="utf-8")
        for name, metadata in self.metadata.items():
            with self.subTest(chart=name):
                self.assertTrue(metadata["sources"])
                self.assertIn(name, manifest)
                paths = set()
                for source in metadata["sources"]:
                    path = source["path"]
                    self.assertFalse(Path(path).is_absolute())
                    self.assertNotIn("..", Path(path).parts)
                    self.assertNotIn(path, paths)
                    paths.add(path)
                    digest = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                    self.assertEqual(source["sha256"], digest)
                    self.assertIn(path, manifest)
                    self.assertIn(digest, manifest)

    def test_self_contained_accessible_svg(self):
        for name, svg in self.svgs.items():
            with self.subTest(chart=name):
                self.assertEqual(svg.tag, SVG + "svg")
                self.assertIn("viewBox", svg.attrib)
                self.assertTrue(list(svg.iter(SVG + "title")))
                self.assertTrue(list(svg.iter(SVG + "desc")))
                for node in svg.iter():
                    local = node.tag.rsplit("}", 1)[-1].lower()
                    self.assertNotIn(local, {"script", "foreignobject", "image", "iframe"})
                    for attr, value in node.attrib.items():
                        attr = attr.rsplit("}", 1)[-1].lower()
                        self.assertFalse(attr.startswith("on"), f"{name}: event handler")
                        if attr in {"href", "src"}:
                            self.assertTrue(value.startswith("#"), f"{name}: external resource")
                        for target in re.findall(r"url\(([^)]*)\)", value):
                            self.assertTrue(target.strip(" '\"").startswith("#"))
                    if local == "style":
                        css = "".join(node.itertext()).lower()
                        self.assertNotIn("@import", css)
                        self.assertNotIn("@font-face", css)
                        for target in re.findall(r"url\(([^)]*)\)", css):
                            self.assertTrue(target.strip(" '\"").startswith("#"))
                text = visible_text(svg)
                self.assertIn("synthetic ships and cargo, simulated port", text.lower())

    def test_axes_state_the_measure_units_and_population(self):
        labels = {
            "pipeline-passes.svg": ("Full-checker pass rate (%)", "Cohort and ship size"),
            "ext1-passes.svg": ("Full-checker pass rate (%)", "Separate experiments / secondary sum"),
            "instruction-binding.svg": ("Checked, instruction-obedient plans (%)", "Population / result"),
            "reader-exact-match.svg": ("Exact-match sentences (%)", "Reader / comparator"),
            "decode-mask-overrides.svg": ("Validation sentences with at least one override (count)", "Model / training"),
        }
        for name, expected in labels.items():
            with self.subTest(chart=name):
                text = visible_text(self.svgs[name])
                for label in expected:
                    self.assertIn(label, text)

    def test_pipeline_rows_equal_recorded_counts_and_exact_intervals(self):
        report = read_json(PIPELINE + "report.json")
        rows = self.metadata["pipeline-passes.svg"]["facts"]["rows"]
        expected = {}
        for cohort_key in ("tiny15", "small30", "envelope24"):
            cohort = report["cohorts"][cohort_key]
            grouping = "by_ship" if cohort_key == "envelope24" else "ship_groups"
            groups = dict(cohort[grouping])
            if cohort_key == "envelope24":
                groups["all"] = cohort
            for ship, group in groups.items():
                for arm in ("pipeline", "twostep"):
                    data = group["arms"][arm]
                    expected[(cohort_key, ship, arm)] = (
                        data["passes_L0"], data["expected_maps"], data["passes_L0_exact95"],
                    )
        self.assertEqual(len(rows), len(expected))
        actual_keys = [(r["cohort"], r["ship"], r["arm"]) for r in rows]
        self.assertEqual(Counter(actual_keys), Counter(expected.keys()))
        text = visible_text(self.svgs["pipeline-passes.svg"])
        for row in rows:
            key = row["cohort"], row["ship"], row["arm"]
            passes, n, bounds = expected[key]
            with self.subTest(row=key):
                self.assertEqual((row["passes"], row["n"], row["exact95"]), (passes, n, bounds))
                self.assertIn(f"{passes}/{n}", text)
                self.assertIn(f"n = {n}", text)
                self.assertIn(interval_text(bounds), text)
        self.assertIn("exact", text.lower())
        self.assertIn("95%", text.replace("95 %", "95%"))
        self.assertIn("1,700", text)
        self.assertIn("outside", text.lower())

    def test_instruction_binding_distinguishes_broad_denominator(self):
        cohort = read_json(PIPELINE + "report.json")["cohorts"]["envelope24"]
        binding = cohort["instruction_binding"]
        broad = cohort["arms"]["instruction"]
        expected = {
            "binding": (binding["binding_obedient_passes"], binding["rule_broken_maps"], binding["binding_obedient_exact95"]),
            "broad": (broad["passes_L0"], broad["expected_maps"], broad["passes_L0_exact95"]),
        }
        facts = self.metadata["instruction-binding.svg"]["facts"]
        text = visible_text(self.svgs["instruction-binding.svg"])
        for key, (passes, n, bounds) in expected.items():
            self.assertEqual((facts[key]["passes"], facts[key]["n"], facts[key]["exact95"]), (passes, n, bounds))
            self.assertIn(f"{passes}/{n}", text)
            self.assertIn(interval_text(bounds), text)
        self.assertEqual(facts["already_obeyed"], binding["already_obeyed"])
        self.assertIn("broad", text.lower())
        self.assertIn("already obeyed", text.lower())
        self.assertIn("not a human", text.lower())

    def test_extension_rows_equal_figures_counts_intervals_and_provenance(self):
        facts = self.metadata["ext1-passes.svg"]["facts"]
        rows = facts["rows"]
        expected = list(self._extension_pass_rows())
        self.assertEqual(len(rows), len(expected))
        self.assertEqual(Counter((row["cohort"], row["arm"]) for row in rows),
                         Counter((cohort, arm) for cohort, _, arm, *_ in expected))
        indexed = {(row["cohort"], row["arm"]): row for row in rows}
        for cohort, _, arm, _, _, record, pointer in expected:
            row = indexed[cohort, arm]
            with self.subTest(cohort=cohort, arm=arm):
                self.assertEqual((row["passes"], row["n"], row["exact95"]), figure_values(record))
                self.assertEqual(row["source"], {"path": EXT_FIGURES, "json_pointer": pointer})

    def test_extended_binding_values_equal_figures_without_pooling_context_row(self):
        figures = read_json(EXT_FIGURES)
        facts = self.metadata["instruction-binding.svg"]["facts"]
        for key, section in (("extension_binding", "primary"),
                             ("secondary_binding", "secondary_pooled")):
            row = facts[key]
            with self.subTest(binding=section):
                self.assertEqual((row["passes"], row["n"], row["exact95"]),
                                 figure_values(figures[section]["binding"]))
                self.assertEqual(row["source"], {
                    "path": EXT_FIGURES, "json_pointer": f"/{section}/binding",
                })
        # The original broad 18/24 remains contextual and must never be used
        # as the 13/18 component of the secondary binding result.
        original = figures["separate_original_3_1"]["binding"]
        extension = figures["primary"]["binding"]
        pooled = figures["secondary_pooled"]["binding"]
        self.assertEqual(pooled["numerator"]["value"],
                         original["numerator"]["value"] + extension["numerator"]["value"])
        self.assertEqual(pooled["denominator"]["value"],
                         original["denominator"]["value"] + extension["denominator"]["value"])

    def test_extension_labels_keep_original_fresh_and_secondary_results_separate(self):
        figures = read_json(EXT_FIGURES)
        for name in ("ext1-passes.svg", "instruction-binding.svg"):
            svg = self.svgs[name]
            text = visible_text(svg)
            with self.subTest(chart=name):
                header = self._label(svg, "3.1 on frozen v3 (a11b4202…); ext-1 on v4 (acf2f275…), planning code byte-identical")
                self.assertLess(float(header.get("y")), 160, "version disclosure belongs in the header")
                self.assertNotIn("frozen pipeline v3 (a11b4202…)", text)
                self.assertIn(figures["separate_original_3_1"]["label"]["value"], text)
                self.assertIn(figures["primary"]["label"]["value"], text)
                self.assertIn(figures["secondary_pooled"]["label"], text)
                sources = {record["path"] for record in self.metadata[name]["sources"]}
                self.assertIn(EXT_FIGURES, sources)
                self.assertFalse(any("/runs/ext-1/" in path for path in sources))
                self.assertIn("same synthetic generator family", text.lower())
                self.assertIn("not new voyage types", text.lower())
                self.assertIn("not a human planner", text.lower())
                self.assertIn("up to 195 boxes", text)
                self.assertIn("half-ship section", text)
                self.assertIn("70%", text)
        ext = self.svgs["ext1-passes.svg"]
        first, fresh, secondary = (float(self._label(ext, label).get("y")) for label in
                                   ("Original 3.1", "Fresh Ext-1", "SECONDARY pooled"))
        self.assertLess(first, fresh)
        self.assertLess(fresh, secondary)

    def test_extension_drawing_extents_and_text_anchors_fit_the_canvas(self):
        # Source-only structural bounds check; glyph widths depend on the
        # embedding system font and are deliberately not guessed here.
        for name in ("ext1-passes.svg", "instruction-binding.svg"):
            svg = self.svgs[name]
            left, top, width, height = map(float, svg.get("viewBox").split())
            right, bottom = left + width, top + height
            for node in svg.iter():
                tag = node.tag.rsplit("}", 1)[-1]
                if tag == "line":
                    xs = float(node.get("x1")), float(node.get("x2"))
                    ys = float(node.get("y1")), float(node.get("y2"))
                elif tag == "circle":
                    x, y, r = (float(node.get(attr)) for attr in ("cx", "cy", "r"))
                    xs, ys = (x - r, x + r), (y - r, y + r)
                elif tag == "rect":
                    x, y, w, h = (float(node.get(attr, "0")) for attr in ("x", "y", "width", "height"))
                    self.assertGreaterEqual(w, 0)
                    self.assertGreaterEqual(h, 0)
                    xs, ys = (x, x + w), (y, y + h)
                elif tag == "text":
                    x, y, size = (float(node.get(attr, "0")) for attr in ("x", "y", "font-size"))
                    xs, ys = (x, x), (y - size, y + size / 4)
                else:
                    continue
                with self.subTest(chart=name, element=tag, x=xs, y=ys):
                    self.assertGreaterEqual(min(xs), left)
                    self.assertLessEqual(max(xs), right)
                    self.assertGreaterEqual(min(ys), top)
                    self.assertLessEqual(max(ys), bottom)

    def test_reader_scores_and_gate_outcomes_equal_sources(self):
        adoption = read_json(FINAL + "final-adoption-d266.json")
        summary = read_json(FINAL + "sealed-summary.json")
        facts = self.metadata["reader-exact-match.svg"]["facts"]
        expected = {
            "adopted": adoption["sealed_128"][adoption["adopted"]["name"]],
            "v2": adoption["sealed_128"]["adapter-v2"],
            "untrained": adoption["sealed_128"]["untrained"],
            "parser": read_json(READER + "parser/sealed-v3-report.json"),
            "spark": read_json(READER + "sealed/spark-summary.json"),
        }
        text = visible_text(self.svgs["reader-exact-match.svg"])
        for arm, raw in expected.items():
            with self.subTest(arm=arm):
                for key, value in facts[arm].items():
                    self.assertEqual(value, raw[key])
                self.assertEqual(facts[arm]["exact_match_count"], raw["exact_match_count"])
                self.assertEqual(facts[arm]["count"], raw["count"])
                self.assertIn(f"{raw['exact_match_count']}/{raw['count']}", text)
        self.assertEqual(facts["gates"], summary["gates"])
        for gate in ("R1", "R2", "R3"):
            outcome = "PASS" if summary["gates"][gate] else "FAIL"
            self.assertRegex(text, rf"{gate}\s*[:·—–-]?\s*{outcome}")
        criteria = summary["gates"]["criteria"]
        self.assertIn(f"{criteria['R1_min_exact_pct']}%", text)
        self.assertIn(f"maximum {criteria['R2_max_false_acceptances']} allowed", text)
        shortfall = criteria["R3_max_shortfall_from_spark_pp"]
        self.assertRegex(text, rf"maximum {shortfall:g}(?:\.0+)? allowed")
        spark_minimum = expected["spark"]["exact_match_pct"] - criteria["R3_max_shortfall_from_spark_pp"]
        self.assertIn(f"{spark_minimum:.2f}%", text)
        self.assertIn(f"{abs(summary['gates']['difference_from_spark_pp']):.2f}", text)
        adopted = expected["adopted"]
        self.assertIn(f"{adopted['false_acceptance_count']}/{adopted['loosening_count']}", text)
        calculated_gates = {
            "R1": adopted["exact_match_pct"] >= criteria["R1_min_exact_pct"],
            "R2": adopted["false_acceptance_count"] <= criteria["R2_max_false_acceptances"],
            "R3": adopted["exact_match_pct"] >= spark_minimum,
        }
        self.assertEqual(calculated_gates, {gate: summary["gates"][gate] for gate in calculated_gates})
        self.assertEqual(calculated_gates, {gate: adoption["gates_swa_last_5"][gate] for gate in calculated_gates})
        self.assertIn("reference", text.lower())
        self.assertIn("not rerun", text.lower())

    def test_mask_override_counts_and_caveats_equal_recording(self):
        raw = read_json(FINAL + "mask-study/report-base.json")
        facts = self.metadata["decode-mask-overrides.svg"]["facts"]
        text = visible_text(self.svgs["decode-mask-overrides.svg"])
        for arm in ("untrained", "fine_tuned"):
            self.assertEqual(facts[arm]["count"], raw[arm]["sentences"])
            self.assertEqual(facts[arm]["overrides"], raw[arm]["sentences_top_changed"])
            self.assertIn(f"{raw[arm]['sentences_top_changed']}/{raw[arm]['sentences']}", text)
            self.assertEqual(raw[arm]["by_reason"]["bay_not_on_ship"], 0)
            self.assertEqual(raw[arm]["by_reason"]["tier_over_cap"], 0)
        for phrase in (
            "validation", "output format, not ship facts", "none was an illegal bay or tier",
            "sentences it should have refused", "legal and still wrong", "person confirms",
        ):
            self.assertIn(phrase, text.lower())

    def test_plan_labels_preserve_protocol_disclosure(self):
        report = read_json(PIPELINE + "report.json")
        for name in ("pipeline-passes.svg", "instruction-binding.svg"):
            text = visible_text(self.svgs[name])
            with self.subTest(chart=name):
                self.assertIn(report["label"], text)
                self.assertIn("two earlier attempts", text.lower())
                self.assertIn("our own frozen-code guard", text.lower())
                self.assertIn("completed 36 and 91 rows", text.lower())
                self.assertIn("both are published", text.lower())
                self.assertIn("not pooled with the final run", text.lower())
                self.assertIn("37 of 38 on verdict alone", text.lower())
                self.assertIn("91 of 91", text.lower())
                self.assertNotIn("lift saving", text.lower().replace("no lift saving", ""))


if __name__ == "__main__":
    unittest.main()
