"""Signed decomposition of linear-vs-exact GM residual (independent review of task 3.7).

Wraps physics.exact_condition so the code's OWN measure_errors sweep records every
condition; then splits the signed residual  gm_lin - gm_exact  into
  fsc   : (FSC_exact - sum FSM_max/Delta)       reserve for every tank slack (<=0: conservative)
  km_ek : (a+b*Delta)/Delta - KM_evenkeel(Delta) support line vs upright even-keel KM (<=0)
  km_tr : KM_evenkeel - KM_exact(trimmed)        trim effect on KM (sign = risk if >0)
  kg    : KG_exact - KG_lin                      box heights + tank VCG at real fill (>0 unsafe)
Usage: uv run python gm_decompose.py <ship> <samples> [laden] [window_fraction]
  laden: replace the 4-box fixture manifest by a synthetic ~350 TEU manifest (std+HC mix)
  window_fraction w: ballast windows [c(1-w)/2.., c(1+w)/2] around half fill -> narrower band
Model cache is redirected to experiments/physics-review/linear/.cache (no repo writes elsewhere).
"""
import runpy, sys, json
from pathlib import Path
import numpy as np
from scipy.optimize import brentq
here = Path(__file__).resolve().parent; root = here.parents[2]
import stowage.linear.cache as C
C._DEFAULT_ROOT = here/".cache"
helper = runpy.run_path(str(root/"experiments/linear-model/validate.py"))
import stowage.linear.physics as P
from stowage.linear.bands import band_for_instance
from stowage.conventions import make_container_id

args = sys.argv[1:]
name, n = args[0], int(args[1]); laden = "laden" in args
nums = [a for a in args[2:] if a != "laden"]; wfrac = float(nums[0]) if nums else None
inst = helper["fleet_instance"](name)
# Same declared synthetic 400 t fixed load at 0.7 Lpp as tests/linear/test_model.py (makes trim band reachable).
v = inst.vessel
inst = inst.model_copy(update={'vessel': v.model_copy(update={'fixed_loads': [*v.fixed_loads, (.7*v.hull.lpp_m, 0., 2., 400_000.)]})})
if laden:
    rng = np.random.default_rng(7); base = {c.id: c for c in inst.containers}
    t20, t40 = base['QZXU0000035'], base['QZXU0000014']
    boxes = []
    n20, n40 = {"feeder_500": (60, 40), "feeder_1000": (120, 80), "feeder_1700": (200, 130)}[name]
    for k in range(n20):
        boxes.append(t20.model_copy(update={'id': make_container_id('RVWU', k+1), 'gross_mass_kg': float(rng.uniform(6e3, 24e3))}))
    for k in range(n40):
        boxes.append(t40.model_copy(update={'id': make_container_id('RVWU', 5000+k), 'iso_type': '45G1', 'reefer': False,
                     'reefer_setpoint_c': None, 'gross_mass_kg': float(rng.uniform(8e3, 28e3))}))
    inst = inst.model_copy(update={'containers': boxes, 'load_list': [b.id for b in boxes]})
band = None
if wfrac is not None:
    win = {t.id: (t.capacity_m3*t.content_density_kg_m3*(1-wfrac)/2, t.capacity_m3*t.content_density_kg_m3*(1+wfrac)/2)
           for t in inst.vessel.tanks if t.adjustable}
    band = band_for_instance(inst, ballast_windows_kg=win)
model = P.build_model(inst, band, _samples=2)
records = []
orig = P.exact_condition
def wrapped(model_, instance, cell, tank, *, geometry=None, cell_vcg_m=None):
    out = orig(model_, instance, cell, tank, geometry=geometry, cell_vcg_m=cell_vcg_m)
    records.append((np.array(cell), np.array(tank), out)); return out
P.exact_condition = wrapped
_, diag = P.measure_errors(model, inst, samples=n, seed=4242)
P.exact_condition = orig
g = P.geometry_for(inst); rho = model.water_density_kg_m3; a, bb = model.km_line
fsm_max = model.tank_fsm_max_kgm.sum(); b_ = model.band
rows = []
for cell, tank, ex in records:
    if not (b_.displacement_min_kg <= ex['displacement_kg'] <= b_.displacement_max_kg and
            b_.draught_min_m <= ex['draught_m'] <= b_.draught_max_m and
            abs(ex['trim_m']) <= b_.max_abs_trim_m and abs(ex['list_rad']) <= b_.max_abs_heel_rad):
        continue
    lin = P._linear_arrays(model, cell, tank); D = ex['displacement_kg']
    T = brentq(lambda t: g.submerged(t, t, 0., rho).displacement_kg-D, .01, g.max_z_m-.01)
    km_ek = g.hydrostatic_row(T, rho).kmt_m
    kg_lin = (model.const_moment_z_kgm+model.cell_vcg_m@cell+model.tank_vcg_m@tank)/D
    rows.append(dict(D=D, err=lin['gm_m']-ex['gm_m'], fsc=ex['fsc_m']-fsm_max/D,
                     km_ek=(a+bb*D)/D-km_ek, km_tr=km_ek-ex['km_m'], kg=ex['kg_m']-kg_lin,
                     trim=ex['trim_m'], gm=ex['gm_m'], trim_err=lin['trim_m']-ex['trim_m'],
                     list_err=float(lin['list_rad']-ex['list_rad'])))
R = {k: np.array([r[k] for r in rows]) for k in rows[0]}
R['rest'] = R['err']-R['fsc']-R['km_ek']-R['km_tr']-R['kg']
print(f"{name}{' laden' if laden else ''} window={wfrac} band {b_.displacement_min_kg/1e3:.0f}-{b_.displacement_max_kg/1e3:.0f} t "
      f"(cargo {model._cargo_mass_kg/1e3:.0f} t), n={len(rows)}, sumFSMmax={fsm_max/1e3:.0f} t.m, exact GM range {R['gm'].min():.2f}-{R['gm'].max():.2f} m")
for k in ('err', 'fsc', 'km_ek', 'km_tr', 'kg', 'rest', 'trim_err', 'list_err'):
    v = R[k]; print(f"  {k:8s} min {v.min():+.4f} max {v.max():+.4f} mean {v.mean():+.4f} max|.| {np.abs(v).max():.4f}")
nofsc = R['err']-R['fsc']
print(f"  code-style margin max|err| = {np.abs(R['err']).max():.4f} m;  unsafe-side max(lin-exact) = {R['err'].max():+.4f} m")
print(f"  residual excluding FSM reserve: max|.| {np.abs(nofsc).max():.4f}, max {nofsc.max():+.4f}")
mid = (b_.displacement_min_kg+b_.displacement_max_kg)/2; dist = np.abs(R['D']-mid)/mid
for lo, hi in ((0, .02), (.02, .05), (.05, .5)):
    s = (dist >= lo) & (dist < hi)
    if s.any():
        print(f"  |D-mid|/mid [{lo},{hi}): n={s.sum():3d} max|err| {np.abs(R['err'][s]).max():.4f} max|km_ek| {np.abs(R['km_ek'][s]).max():.4f} max|err-fsc| {np.abs(nofsc[s]).max():.4f}")
print("  code gm_components diag:", json.dumps(diag.get('gm_components'), default=float))
out = here/f"gm_decompose_{name}{'_laden' if laden else ''}{'' if wfrac is None else f'_w{wfrac}'}.json"
out.write_text(json.dumps({k: v.tolist() for k, v in R.items()}))
