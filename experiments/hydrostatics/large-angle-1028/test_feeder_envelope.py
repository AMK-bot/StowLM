"""Checks of experimental shape probes and unchanged checker-area plumbing."""
import ast
import importlib.util
import json
from pathlib import Path
import sys
import numpy as np
import pytest
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from feeder_envelope import geometry_variant
from stowage.vessel.fleet import load_ship_params
from stowage.checker.stability_geometry import StabilityGeometry

@pytest.fixture(scope='module')
def hull():
 p=load_ship_params('feeder_500');return p,StabilityGeometry(p)

@pytest.mark.parametrize('variant',['sealed_cover_0p6','no_coaming_buoyancy','parent19_same_depth','forecastle_2m'])
def test_probe_preserves_all_upright_underwater_volume_and_original(hull,variant):
 p,g=hull; fingerprint=g._geometry_digest; source=[q.copy() for s in g.sections for q in s]
 h=geometry_variant(g,p,variant);t=g.design_draught_m-.1
 a=g.submerged(t,t,0);b=h.submerged(t,t,0)
 # Removing boxes also removes their longitudinal quadrature breakpoints;
 # that produces sub-millilitre roundoff/quadrature differences, not a change
 # to the underwater surface. Bounds remain far below physical tolerances.
 assert b.volume_m3==pytest.approx(a.volume_m3,abs=1e-6)
 assert b.centroid_m==pytest.approx(a.centroid_m,abs=2e-8)
 assert g._geometry_digest==fingerprint
 assert all(np.array_equal(a,b) for a,b in zip(source,[q for s in g.sections for q in s]))


def test_added_cover_and_removed_coamings_are_nested_solids(hull):
 p,g=hull;add=geometry_variant(g,p,'sealed_cover_0p6');remove=geometry_variant(g,p,'no_coaming_buoyancy')
 # This holds the WATERPLANE fixed, not displaced volume; nesting is exact.
 v=lambda h:h.submerged(g.design_draught_m,g.design_draught_m,np.radians(40)).volume_m3
 assert v(remove)<v(g)<v(add)


def test_manual_numeric_functions_unchanged_after_arrival_api_repair():
 old=ast.parse((HERE/'followup/feeder-driver-C1-source.txt').read_text())
 new=ast.parse((HERE/'feeder_envelope.py').read_text())
 for name in ['manual_instance','geometry_variant','evaluate']:
  a=next(n for n in old.body if isinstance(n,ast.FunctionDef) and n.name==name)
  b=next(n for n in new.body if isinstance(n,ast.FunctionDef) and n.name==name)
  assert ast.dump(a,include_attributes=False)==ast.dump(b,include_attributes=False)


def test_reported_baseline_areas_equal_unchanged_production_check_pilot():
 log=(HERE/'feeder_pilot.log').read_text(); start=log.index('{\n')
 production,_=json.JSONDecoder().raw_decode(log[start:])
 row=json.loads((HERE/'followup/feeder_500-C1.json').read_text())['rows'][0]
 for key in ['area_0_30_mrad','area_0_40_mrad','area_30_40_mrad','area_0_flood_mrad']:
  assert row['metrics'][key]==pytest.approx(production['metrics'][key],abs=1e-10)
