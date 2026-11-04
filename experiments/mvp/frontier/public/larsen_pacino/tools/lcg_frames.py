"""Appendix II LCG against the hydrostatic LCG bounds, per instance, under candidate frames.
Also the moment that release + lightship alone give, to see which LCG a plan can reach."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import evaluator as ev, published as pub
ROOT = "/Users/anda/Projects/meta-hackathon-stowage/data/raw/larsen-pacino"
print("inst v  disp  o  curve  lo_o hi_o | scaled lo hi | A2 | A2-curve? dev range | UL")
for k in range(1, 28):
    v = ev.read_vessel(pub.path_vessel(ROOT, k)); I = ev.read_instance(pub.path_instance(ROOT, k))
    a = pub.APPENDIX2[k-1]; w = a['disp']
    o = ev.hydro_bracket(v.hydro, w)
    h0, h1 = v.hydro[o-1], v.hydro[o]
    f = (w - h0[0]) / (h1[0] - h0[0])
    curve = h1[2] + f * (h1[1] - h1[2])     # lcg_max[o] is the curve value at o-1, lcg_min[o] at o
    lo = h1[1] * h1[0] / w; hi = h1[2] * h1[0] / w
    light = sum(b.const_w * b.lcg for b in v.bays)
    relm = 0; relw = 0
    for b in I.release:
        bay = b.pos[0]; relm += b.weight * v.bays[bay].lcg; relw += b.weight
    # moment the load must add to sit at bound, and needed mean LCG of the load
    wload = w - sum(b.const_w for b in v.bays) - relw
    need = ((lo + hi) / 2 * w - light - relm) / max(wload, 1)
    print(f"{k:2d} {'SML'[(k-1)//9]} {w:7.0f} {o:2d} curve {curve:6.2f} raw[{h1[1]:6.2f},{h1[2]:6.2f}] scaled[{lo:6.2f},{hi:6.2f}] A2 {a['lcg']:5.2f} dev[{lo-curve:5.2f},{hi-curve:5.2f}] UL {pub.TABLE2[k-1]['UL']:3d} rel+light LCG {(light+relm)/(relw+sum(b.const_w for b in v.bays)):6.2f} needed load LCG {need:6.1f}")
