"""Checks prevent curve extrapolation and accidental tolerance inflation."""
import csv
import math
from pathlib import Path
import runpy
import pytest

HERE=Path(__file__).resolve().parent
MODULE=runpy.run_path(str(HERE/'compare.py'))

def test_iacs_l5_gz_greater_of_percent_and_absolute():
    assert MODULE['l5_gz'](.7)==.05
    assert MODULE['l5_gz'](-2.)==.1
    assert MODULE['l5_gz'](0.)==.05

def test_published_2022_curves_not_extrapolated_to_sixty():
    rows=list(csv.DictReader((HERE/'marlantes-fig22-samples.csv').open()))
    for row in rows:
        if float(row['heel_deg'])>=55:
            assert row['abs_gz_m']=='' and row['csi_gz_m']==''
    assert float(next(r for r in rows if r['heel_deg']=='30')['csi_gz_m'])==pytest.approx(.74003,abs=1e-5)

def test_published_2025_curve_and_documented_slope_ambiguity():
    rows=list(csv.DictReader((HERE/'secondary/jmse2025_fig28_bare_hull.csv').open()))
    r=next(r for r in rows if float(r['heel_deg'])==5)
    assert float(r['gz_m'])/math.radians(5)>.599+.4
    assert max(float(r['heel_deg']) for r in rows)==44.8
