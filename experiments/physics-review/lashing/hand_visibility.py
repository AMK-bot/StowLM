"""Independent SOLAS V/22.1.1-.2 sightline hand calculations vs stowage.checker.visibility.

Hand method: in SHIP coordinates, the sea is the plane z = Ta + (Tf-Ta) x / Lpp (no heel).
For each roof corner P, the line from the eye E through P meets the sea at Q (solve the
plane equation). The blind distance is (Q - Bow_wl) projected on the sea-plane forward axis.
Only corners inside the +-10 deg wedge (azimuth measured at the eye) are used, so the test
cases put the stack on the centreline with half-width < tan(10 deg) * distance.

Run: uv run python experiments/physics-review/lashing/hand_visibility.py
"""
import math
from types import SimpleNamespace

import numpy as np

import stowage.checker.visibility as V
from stowage.model import Cell, Placement
from stowage.model.fixtures import build_instance
from stowage.conventions import iso6346_check_digit


class Plane:
    """Geometry mock consistent with z_sea(x) = Ta + (Tf-Ta) x/Lpp."""
    def __init__(self, lpp):
        self.lpp = lpp

    def waterplane_normal(self, ta, tf, heel):
        n = np.array([(ta-tf)/self.lpp, 0., 1.])
        return n/np.linalg.norm(n)


def set_condition(ta, tf, lpp):
    c = SimpleNamespace(t_aft_m=ta, t_fwd_m=tf, heel_rad=0., geometry=Plane(lpp))
    V._loading_condition = lambda *_: c


def stack(instance, x, y, heights, base=9.0):
    """Single deck stack at (x, y) built on fixture stack 00101D with len(heights) tiers."""
    st = next(s for s in instance.vessel.stacks if s.id == '00101D')
    src = next(c for c in instance.vessel.cells if c.id == '0010182')
    ids = [f'00101{82+2*i:02}' for i in range(len(heights))]
    cells = [Cell(**{**src.model_dump(), 'id': cid, 'lcg_m': x, 'tcg_m': y}) for cid in ids]
    st = st.model_copy(update=dict(cell_ids=ids, base_z_m=base, max_height_m=99., hatch_cover_id=None))
    instance.vessel = instance.vessel.model_copy(update=dict(stacks=[st], cells=cells))
    boxes = []
    for i, h in enumerate(heights):
        cid = f"TSTU{i:06d}{iso6346_check_digit(f'TSTU{i:06d}')}"
        boxes.append(instance.containers[1].model_copy(update=dict(id=cid, height_m=h, oog=None, dg=None, length_ft=20)))
    instance.containers = boxes
    instance.onboard.placements = [Placement(slot=cid, container_id=b.id) for cid, b in zip(ids, boxes)]
    return instance


def hand_blind(ta, tf, lpp, eye, bow_x, x, y, top, length=6.058, width=2.438):
    def zsea(xx):
        return ta + (tf-ta)*xx/lpp
    E = np.array(eye)
    best = 0.
    wedge = math.tan(math.radians(10))
    fwd = np.array([1., 0., (tf-ta)/lpp]); fwd /= np.linalg.norm(fwd)
    bow = np.array([bow_x, 0., zsea(bow_x)])
    for xx in (x-length/2, x+length/2):
        for yy in (y-width/2, y+width/2):
            if abs(yy) > wedge*(xx-E[0]):
                continue
            P = np.array([xx, yy, top])
            d = P-E
            # solve E_z + s d_z = Ta + (Tf-Ta)(E_x + s d_x)/lpp
            k = (tf-ta)/lpp
            s = (zsea(E[0]) - E[2])/(d[2] - k*d[0])
            if s <= 0:
                return math.inf
            Q = E + s*d
            best = max(best, float((Q-bow)@fwd))
    return best


def run():
    inst0 = build_instance()
    lpp, loa = inst0.vessel.hull.lpp_m, inst0.vessel.hull.loa_m
    br = inst0.vessel.bridge
    print(f"fixture: Lpp={lpp} LOA={loa} bridge x={br.x_m} eye z={br.eye_z_m} bow x={br.bow_x_m}; limit=min(2LOA,500)={min(2*loa,500)}")
    cases = [
        ("even keel T=5, 3x8'6\" at x=100", 5., 5., 100., [2.591]*3),
        ("trim by head 2 m (Ta=4,Tf=6)", 4., 6., 100., [2.591]*3),
        ("trim by stern 2 m (Ta=6,Tf=4)", 6., 4., 100., [2.591]*3),
        ("even keel, 4x9'6\" HC near bow x=120", 5., 5., 120., [2.896]*4),
        ("even keel, 4x8'6\" near bow x=120", 5., 5., 120., [2.591]*4),
        ("light ship Ta=4.5 Tf=2.5 (stern trim), 5xHC at x=100", 4.5, 2.5, 100., [2.896]*5),
        ("deep, by head Ta=6 Tf=7, 5xHC at x=100", 6., 7., 100., [2.896]*5),
    ]
    for name, ta, tf, x, hs in cases:
        inst = stack(build_instance(), x, 0., hs)
        set_condition(ta, tf, lpp)
        rep = V.check_visibility(inst, inst.onboard, call_index=0)
        top = 9.0 + sum(hs)
        hand = hand_blind(ta, tf, lpp, (br.x_m, 0., br.eye_z_m), br.bow_x_m, x, 0., top)
        code = rep.metrics['blind_distance_m']
        print(f"{name:55s} top z={top:6.3f}  hand={hand:9.3f} m  code={code:9.3f} m  diff={code-hand:+.2e}  "
              f"viol={[v.rule_id for v in rep.violations]}")

    # Sector check: one 20' box with roof above eye level, centreline, x=40 (fwd of bridge at x=15)
    print("\nV/22.1.2 sector: single box above eye level on centreline, x=40")
    inst = stack(build_instance(), 40., 0., [20.])
    set_condition(5., 5., lpp)
    rep = V.check_visibility(inst, inst.onboard, call_index=0)
    near = 40-6.058/2-br.x_m
    hand = 2*math.atan(1.219/near)
    print(f"  hand sector = 2 atan(1.219/{near:.3f}) = {math.degrees(hand):.3f} deg; code max sector = "
          f"{math.degrees(rep.metrics['max_blind_sector_rad']):.3f} deg; blind={rep.metrics['blind_distance_m']}")

    # Sensitivity: how many 8'6" tiers allowed at x=100 even keel T=5 (hand), and eye-height sensitivity
    print("\nTier capacity at x=100 (centreline), T=5 even keel, eye z=25 (fixture):")
    for n in range(1, 7):
        hb = hand_blind(5., 5., lpp, (br.x_m, 0., br.eye_z_m), br.bow_x_m, 100., 0., 9.0+2.591*n)
        print(f"  {n} tiers: blind={hb:8.2f} m {'FAIL' if hb > min(2*loa, 500) else 'ok'}")
    print("Same with eye taken 1.5 m aft of bridge front (x=13.5) -> effect of conning-position offset:")
    for n in (3, 4):
        a = hand_blind(5., 5., lpp, (br.x_m, 0., br.eye_z_m), br.bow_x_m, 100., 0., 9.0+2.591*n)
        b = hand_blind(5., 5., lpp, (br.x_m-1.5, 0., br.eye_z_m), br.bow_x_m, 100., 0., 9.0+2.591*n)
        print(f"  {n} tiers: eye at front {a:.2f} m, 1.5 m aft {b:.2f} m")


if __name__ == "__main__":
    run()
