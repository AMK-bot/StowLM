"""Fills VALIDATION.template.md with tables built from evaluator output, writing VALIDATION.md."""
import contextlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import summarize as s  # noqa: E402


def cap(fn, *a):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*a)
    return buf.getvalue().rstrip()


def ratios():
    lines = ["| Run | Inst | Feasible | Mean ratio | Geo mean | Median | Min | Max | Inst ≤ 1 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for spec in (("c60", "obj", "Calibrated 60 s vs Obj60"), ("l60", "obj", "Literal 60 s vs Obj60"),
                 ("c600", "obj600", "Calibrated 600 s vs Obj600 (subset)")):
        if s.load(spec[0]):
            lines.append(s.ratio_block(*spec))
    return "\n".join(lines)


t = open(f"{HERE}/VALIDATION.template.md").read()
reps = {"@@RATIOS@@": ratios(), "@@TABLE_C60@@": cap(s.table, "c60"), "@@TABLE_L60@@": cap(s.table, "l60"),
        "@@TABLE_C600@@": cap(s.table, "c600", "obj600"), "@@KPIS_C60@@": cap(s.kpis, "c60"),
        "@@CONTRIB_C60@@": cap(s.contrib, "c60"), "@@CONTRIB_L60@@": cap(s.contrib, "l60")}
for k, v in reps.items():
    t = t.replace(k, v)
open(f"{HERE}/VALIDATION.md", "w").write(t)
