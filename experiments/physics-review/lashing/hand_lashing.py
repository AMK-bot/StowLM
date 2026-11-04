"""Independent hand calculation of IRS container-securing loads vs stowage.checker.lashing.

Written from the IRS text (Main Rules July 2026 Pt.3 Ch.5 §2; IRS-G-DES-19 July 2025 §§3.3-3.6),
not from the code. Uses the same declared synthetic arrangement (28 mm crossed rods to top of
tier 2, reach 2.438 m, E=200 GPa) so that numbers are comparable.

Run: uv run python experiments/physics-review/lashing/hand_lashing.py
"""
import math
from types import SimpleNamespace

import numpy as np

import stowage.checker.lashing as L
import stowage.checker.visibility as V
from stowage.model import Cell, Placement, ShipState
from stowage.model.fixtures import build_instance
from stowage.conventions import iso6346_check_digit

g = 9.81


def stack_instance(masses, heights, length_ft=20):
    """Same construction as tests/checker/test_lashing.py::_stack_instance (fixture stack 00101D)."""
    instance = build_instance()
    cargo = [c.model_copy(update=dict(length_ft=length_ft, gross_mass_kg=m, height_m=h, oog=None, dg=None))
             for c, m, h in zip(instance.containers * 3, masses, heights)]
    cargo = [c.model_copy(update=dict(id=f"TSTU{i:06d}{iso6346_check_digit(f'TSTU{i:06d}')}")) for i, c in enumerate(cargo)]
    stack = next(s for s in instance.vessel.stacks if s.id == '00101D')
    source = next(c for c in instance.vessel.cells if c.id == '0010182')
    cell_ids = [f'00101{82+2*i:02}' for i in range(len(masses))]
    cells = [Cell(**{**source.model_dump(), 'id': cid, 'vcg_base_m': 9+i*2.591}) for i, cid in enumerate(cell_ids)]
    stack = stack.model_copy(update=dict(cell_ids=cell_ids, max_height_m=40.))
    vessel = instance.vessel.model_copy(update=dict(stacks=[stack], cells=cells))
    state = ShipState(vessel_id=vessel.id, placements=[Placement(slot=cid, container_id=c.id)
                                                       for cid, c in zip(cell_ids, cargo)])
    return instance.model_copy(update=dict(vessel=vessel, containers=cargo)), state


def mock_condition(gm=1.0, T=6.0, mass=9.6e6):
    V_cond = SimpleNamespace(mass_kg=mass, gm_m=gm, t_aft_m=T, t_fwd_m=T)
    V._loading_condition = lambda *_: V_cond
    return V_cond


# ---------------------------------------------------------------- hand motions (rule text)
def hand_motions(Lpp, B, D, Tsc, T, gm, Cb, bilge=True):
    kr = 0.35*B                                  # Table 2.1.2 container ship
    Tr = min(30., 2.3*math.pi*kr/math.sqrt(g*gm))  # §2.1.2.1
    phi = 9000*(1.25-0.025*Tr)*1.0*(1.0 if bilge else 1.2)/((B+75)*math.pi)
    if B < 32.2:
        phi = max(phi, 25.)                      # Guidelines §3.5.2.1.3
    fT = max(0.5, T/Tsc)
    Tp = math.sqrt(2*math.pi*0.6*(1+fT)*Lpp/g)   # §2.1.2.2
    theta = min(20., 1350*Lpp**-0.94*(1+(2.57/math.sqrt(g*Lpp))**1.2))
    a0 = min(0.52, (1.58-0.47*Cb)*(2.4/math.sqrt(Lpp)+34/Lpp-600/Lpp**2))
    surge, sway, heave = 0.2*a0*g, 0.3*a0*g, 0.7*a0*g/math.sqrt(Cb)
    aroll = phi*math.pi/180*(2*math.pi/Tr)**2
    apitch = theta*math.pi/180*(2*math.pi/Tp)**2 if Lpp >= 90 else 0.38*theta/Tp**2
    R = min(D/4+T/2, D/2)
    L0 = max(Lpp, 110.)

    def acc(x, y, z):
        al = 0.7*math.sqrt(surge**2 + L0/325*(g*math.sin(math.radians(theta)) + apitch*(z-R))**2)
        at = math.sqrt(sway**2 + (g*math.sin(math.radians(phi)) + aroll*(z-R))**2)
        av = math.sqrt(heave**2 + (aroll*y)**2 + (apitch*(x-0.45*Lpp))**2)
        return al, at, av
    return SimpleNamespace(Tr=Tr, phi=phi, theta=theta, Tp=Tp, a0=a0, R=R, acc=acc,
                           surge=surge, sway=sway, heave=heave, aroll=aroll, apitch=apitch)


def hand_stack(masses, heights, x, y, base, mot, length_ft=20, kend=3.7e6, longit_rule=True, E=200e9, wind=1.0):
    """Per-END-FRAME quantities, physical reading of §3.6.2-3.6.3 (forces split to two ends)."""
    n = len(masses)
    Lc = {20: 6.058, 40: 12.192}[length_ft]
    W = 2.438
    m_eff = [max(m, 2500 if length_ft == 20 else 3500) for m in masses]
    z = base
    Ft, Fl, Fv, Wt, Wl, zc = [], [], [], [], [], []
    for m, h in zip(m_eff, heights):
        al, at, av = mot.acc(x, y, z+0.45*h)
        Ft.append(m*at/2); Fl.append(m*al/2); Fv.append(m*(g+av)/4)
        Wt.append(wind*max(15700 if length_ft == 20 else 31600, length_ft*0.3048*h*1000)/2)
        Wl.append(max(6400, W*h*1000)/2)
        zc.append(z); z += h
    # equivalent top-of-tier forces §3.6.3.1.1
    F = [0.45*Ft[i] + (0.55*Ft[i+1] + 0.5*Wt[i+1] if i < n-1 else 0) + 0.5*Wt[i] for i in range(n)]
    Q = [sum(F[i:]) for i in range(n)]          # §3.6.3.1.2
    # rod: crossed, anchored at top of tier 2 (index a), compatible displacement (independent derivation)
    a = min(2, n)-1
    zr = sum(heights[:a+1]); lr = math.hypot(W, zr); c, s = W/lr, zr/lr
    kr = E*math.pi*0.028**2/4/lr
    # u_a = sum_{j<=a} (Q_j - H)/k, H = kr c^2 u_a  ->  u_a = sum Q_j/k / (1 + (a+1) kr c^2/k)
    ua = sum(Q[:a+1])/kend/(1+(a+1)*kr*c*c/kend)
    H = kr*c*c*ua; T = H/c
    Qrev = [Q[j]-H if j <= a else Q[j] for j in range(n)]
    # corner post compression/tension (transverse, §3.6.3.3.1 / 3.6.3.4.1)
    comp, tens, comp_l_rule, comp_l_code = [], [], [], []
    for i in range(n):
        hw = [sum(heights[i:k])+0.45*heights[k] for k in range(i, n)]
        hc = [sum(heights[i:k])+0.5*heights[k] for k in range(i, n)]
        Mt = sum(Ft[k]*hw[k-i] + Wt[k]*hc[k-i] for k in range(i, n))
        if i <= a:
            Mt -= H*sum(heights[i:a+1])
        Ml = sum(Fl[k]*hw[k-i] + Wl[k]*hc[k-i] for k in range(i, n))   # per side wall (wind incl., as code)
        vert = sum(Fv[i:])
        comp.append(vert + Mt/W + (T*s if i <= a else 0))
        tens.append(max(0, Mt/W - sum(m_eff[i:])*g/4))
        comp_l_rule.append(vert + Ml/Lc)        # physical: per-side moment / L  == 0.5*sum(M al hw)/L
        comp_l_code.append(vert + 0.5*Ml/Lc)    # code: halves again
    return SimpleNamespace(F=F, Q=Q, Qrev=Qrev, rodT=T, H=H, comp=comp, tens=tens,
                           comp_l_rule=comp_l_rule, comp_l_code=comp_l_code, Ft=Ft, Fv=Fv)


def case_uniform_six():
    print("=== Case 1: six-high 20' 8'6\" deck stack, 20 t each, GM=1.0 m, T=6 m (fixture hull L=120 B=20) ===")
    masses, heights = [20000]*6, [2.591]*6
    inst, state = stack_instance(masses, heights)
    cond = mock_condition(gm=1.0)
    hull = inst.vessel.hull
    Cb = cond.mass_kg/(1025*hull.lpp_m*hull.beam_m*6)
    mot = hand_motions(hull.lpp_m, hull.beam_m, hull.depth_m, hull.scantling_draught_m, 6, 1.0, Cb)
    cell = inst.vessel.cells[0]
    print(f"Cb(T=6)={Cb:.4f} a0={mot.a0:.4f} Tr={mot.Tr:.3f}s phi={mot.phi:.2f}deg theta={mot.theta:.2f}deg "
          f"Tp={mot.Tp:.3f}s R={mot.R:.3f}m aroll={mot.aroll:.4f} apitch={mot.apitch:.4f}")
    code_m = L._motions(hull, 6., 1.0, Cb)
    print(f"code: Tr={code_m.period_roll:.3f} phi={math.degrees(code_m.phi):.2f} theta={math.degrees(code_m.theta):.2f} "
          f"R={code_m.rotation_z:.3f} roll={code_m.roll:.4f} pitch={code_m.pitch:.4f}")
    z = 9.0
    for k in range(6):
        zc = z+0.45*2.591
        print(f"  tier {k+1}: z_cg={zc:.3f} hand(al,at,av)=" + ",".join(f"{v:.4f}" for v in mot.acc(cell.lcg_m, cell.tcg_m, zc))
              + "  code=" + ",".join(f"{v:.4f}" for v in code_m.at(cell.lcg_m, cell.tcg_m, zc)))
        z += 2.591
    rep = L.check_lashing(inst, state, call_index=0)
    out = {}
    for kend in (15.7e6, 3.7e6):
        h = hand_stack(masses, heights, cell.lcg_m, cell.tcg_m, 9.0, mot, kend=kend)
        rack_code, rod_code, *_ = L._end_response(h.Ft, [max(15700, 20*.3048*2.591*1000)/2]*6, heights, kend)
        print(f"  k_end={kend/1e6:.1f} kN/mm: hand Qrev(kN)=" + ",".join(f"{q/1e3:.1f}" for q in h.Qrev)
              + f" rodT={h.rodT/1e3:.1f}kN")
        print(f"             code rack(kN)=" + ",".join(f"{q/1e3:.1f}" for q in rack_code) + f" rodT={rod_code/1e3:.1f}kN")
        print("             hand compression(kN)=" + ",".join(f"{c/1e3:.1f}" for c in h.comp)
              + "  tension(kN)=" + ",".join(f"{t/1e3:.1f}" for t in h.tens))
        out[kend] = h
    hmax = max(max(abs(q) for q in out[k].Qrev) for k in out)/150e3
    cmax = max(max(out[k].comp) for k in out)/848e3
    tmax = max(max(out[k].tens) for k in out)/250e3
    lmax = max(max(out[k].rodT/(0.85*300e3), out[k].H/150e3, out[k].rodT*math.sqrt(1-(out[k].H/out[k].rodT)**2)/225e3) for k in out)
    print(f"  ratios hand: racking={hmax:.4f} corner_post={cmax:.4f} tension={tmax:.4f} lashing={lmax:.4f}")
    m = rep.metrics
    print(f"  ratios code: racking={m['max_racking_ratio']:.4f} corner_post={m['max_corner_post_ratio']:.4f} "
          f"tension={m['max_corner_tension_ratio']:.4f} lashing={m['max_lashing_ratio']:.4f} "
          f"fitting={m['max_fitting_ratio']:.4f} long_rack={m['max_longitudinal_racking_ratio']:.4f}")
    print("  violations:", [v.message[:60] for v in rep.violations])


def case_longitudinal_factor():
    print("\n=== Case 2: longitudinal corner-post moment factor (§3.6.3.3.2) ===")
    # Force a pure-longitudinal case: monkeypatch motions so at=0, al=14 m/s2, av=0.
    masses, heights = [20000]*3, [2.591]*3
    inst, state = stack_instance(masses, heights)
    mock_condition()

    class M:
        period_roll = 10.; phi = 0.
        def at(self, x, y, z): return 14., 0., 0.
    orig = L._motions
    L._motions = lambda *a, **k: M()
    try:
        rep = L.check_lashing(inst, state, call_index=0)
    finally:
        L._motions = orig
    W, Lc = 2.438, 6.058
    # bottom tier i=0: per side wall forces
    Fl = [20000*14/2]*3; Wl = [max(6400, W*2.591*1000)/2]*3; Wt = [max(15700, 20*.3048*2.591*1000)/2]*3
    hw = [0.45*2.591, 2.591+0.45*2.591, 2*2.591+0.45*2.591]
    hc = [0.5*2.591, 1.5*2.591, 2.5*2.591]
    vert = 3*20000*g/4
    Ml = sum(f*h for f, h in zip(Fl, hw)) + sum(w*h for w, h in zip(Wl, hc))
    # transverse: wind only, minus rod relief (rod anchored tier 2) -- upper bound w/o relief:
    Mt = sum(w*h for w, h in zip(Wt, hc))
    print(f"  hand physical/rule (0.5*sum(M*al*hw)/L, M = total mass): compression = {(vert+Ml/Lc)/1e3:.1f} kN")
    print(f"  hand reproducing code (0.5 * per-side moment / L):        compression = {(vert+0.5*Ml/Lc)/1e3:.1f} kN")
    print(f"  transverse (wind only, no rod relief) upper bound:        compression = {(vert+Mt/W)/1e3:.1f} kN")
    print(f"  code max_corner_post_ratio*848 = {rep.metrics['max_corner_post_ratio']*848:.1f} kN")


def case_heavy_on_top_and_tension():
    print("\n=== Case 3: heavy over light (2.5 t x2 under 30 t x2), GM=2.5 m ===")
    masses, heights = [2500, 2500, 30000, 30000], [2.591]*4
    inst, state = stack_instance(masses, heights)
    mock_condition(gm=2.5)
    rep = L.check_lashing(inst, state, call_index=0)
    print("  code:", {k: round(v, 3) for k, v in rep.metrics.items() if 'ratio' in k},
          "phi", round(math.degrees(rep.metrics['roll_angle_rad']), 2))
    print("  violations:", sorted({v.message.split(':')[1][:40] for v in rep.violations if ':' in v.message}))


def case_cb_definition():
    print("\n=== Case 4: Cb at loading draught (code) vs at scantling draught (Pt.3 Ch.1 §2.1.6) ===")
    from stowage.vessel.fleet import load_ship_params
    p = load_ship_params('feeder_500')['hull']
    for T, Cb_lc in ((3.5, 0.80), (5.0, 0.85), (7.06, p['block_coefficient'])):
        a0 = lambda cb: min(.52, (1.58-.47*cb)*(2.4/math.sqrt(107.8)+34/107.8-600/107.8**2))
        print(f"  feeder_500 T={T}: a0(Cb_lc={Cb_lc:.3f})={a0(Cb_lc):.4f}  a0(Cb_sc={p['block_coefficient']:.3f})={a0(p['block_coefficient']):.4f}"
              f"  heave ratio={a0(Cb_lc)/math.sqrt(Cb_lc)/(a0(p['block_coefficient'])/math.sqrt(p['block_coefficient'])):.3f}")


def case_sensitivity():
    print("\n=== Case 5: sensitivity of case 1 (hand model) to rod modulus and wind on inner stacks ===")
    mot = hand_motions(120, 20, 11, 7, 6, 1.0, 9.6e6/(1025*120*20*6))
    for label, kw in (("baseline E=200 GPa, wind on", {}), ("E=140 GPa", dict(E=140e9)),
                      ("E=100 GPa", dict(E=100e9)), ("no wind (sheltered inner stack)", dict(wind=0.0))):
        rs = [hand_stack([20000]*6, [2.591]*6, 46.9, -1.3, 9.0, mot, kend=k, **kw) for k in (15.7e6, 3.7e6)]
        print(f"  {label:32s} racking={max(max(abs(q) for q in r.Qrev) for r in rs)/150e3:.3f} "
              f"rodT={max(r.rodT for r in rs)/1e3:.1f} kN compression={max(max(r.comp) for r in rs)/848e3:.3f} "
              f"tension={max(max(r.tens) for r in rs)/250e3:.3f}")
    # 4-high light-top stack that passes in the tests: how close is it?
    rs = [hand_stack([20000, 8000, 3500, 2500], [2.591]*4, 46.9, -1.3, 9.0, mot, kend=k) for k in (15.7e6, 3.7e6)]
    rw = [hand_stack([20000, 8000, 3500, 2500], [2.591]*4, 46.9, -1.3, 9.0, mot, kend=k, wind=0.0) for k in (15.7e6, 3.7e6)]
    print(f"  light-top 20/8/3.5/2.5 t: racking {max(max(abs(q) for q in r.Qrev) for r in rs)/150e3:.3f} "
          f"(no wind {max(max(abs(q) for q in r.Qrev) for r in rw)/150e3:.3f}); tension {max(max(r.tens) for r in rs)/250e3:.3f} "
          f"(no wind {max(max(r.tens) for r in rw)/250e3:.3f})")


if __name__ == "__main__":
    case_sensitivity()
    case_uniform_six()
    case_longitudinal_factor()
    case_heavy_on_top_and_tension()
    case_cb_definition()
