"""Design-value sanity calculations for sourced-values.md (research only; edits nothing).

Run: uv run python experiments/physics-review/design/sourced_values.py

APPROXIMATION throughout: preliminary-design rules of thumb (Watson-Gilfillan steel
weight, Watson machinery weight, Kupras-style VCG ratios) and an approximate ICLL
Type B freeboard (eCFR 46 CFR 42.20, identical tables to ICLL 1966 Reg 28-38).
Coefficients marked UNVERIFIED were taken from secondary sources / memory and must
be checked against the primary text before anything is described as sourced.
"""
from __future__ import annotations
import math, tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FT = 0.3048
RHO = 1.025

# Type B tabular freeboard, 46 CFR 42.20-15(b)(1), feet -> inches (subset used).
TYPE_B = {340: 53.2, 350: 55.7, 360: 58.2, 400: 68.2, 410: 70.7, 520: 98.1, 530: 100.6}
TYPE_A = {350: 48.8, 360: 50.7, 400: 58.8, 410: 60.9, 520: 82.7, 530: 84.5}


def interp(tab, x):
    ks = sorted(tab)
    for a, b in zip(ks, ks[1:]):
        if a <= x <= b:
            return tab[a] + (tab[b] - tab[a]) * (x - a) / (b - a)
    raise ValueError(x)


def freeboard_mm(L, D, s_frac):
    """Approximate summer freeboard, Type B, flush deck with ZERO sheer.

    s_frac: effective length of enclosed superstructures (forecastle line I) / L.
    Ignores Cb>0.68 correction (KCS Cb~0.65), bow height, deck-line position and
    stringer thickness (assumed 0). 46 CFR 42.20-15/-30/-60/-65.
    """
    Lf = L / FT
    tab = interp(TYPE_B, Lf) * 25.4
    R = L / 0.48 if L < 120 else 250.0                      # 42.20-30 (metric form)
    depth = max(0.0, (D - L / 15) * R)
    full_ded = (34 + (min(Lf, 400) - 279) / (400 - 279) * 8) * 25.4 if Lf < 400 else 42 * 25.4
    pct = {0.0: 0, 0.1: 5, 0.2: 10, 0.3: 15}[s_frac]          # table 42.20-60(b)(2) line I
    ded = full_ded * pct / 100
    aft = ((0.1*Lf+10) + 3*(0.0444*Lf+4.44) + 3*(0.0111*Lf+1.11)) / 8
    fwd = ((0.2*Lf+20) + 3*(0.0888*Lf+8.88) + 3*(0.0222*Lf+2.22)) / 8
    sheer = (aft + fwd) / 2 * 25.4 * (0.75 - s_frac / 2)     # zero actual sheer
    return dict(tabular=tab, depth=depth, sheer=sheer, superstructure=-ded,
                total=tab + depth + sheer - ded, typeA=interp(TYPE_A, Lf) * 25.4)


def lightship(L, B, D, T, Cb, mcr_kw, rpm, deckhouse_h, hdb, slow_speed):
    """Component build-up. Returns (mass t, KG m, breakdown)."""
    # Steel: Watson & Gilfillan (1977) Ws = K E^1.36 [1 + 0.5(Cb' - 0.70)],
    # E = L(B+T) + 0.85 L (D-T) + 0.85 sum(l1 h1) + 0.75 sum(l2 h2).
    # K ~ 0.036 container ships (published range 0.033-0.040 for E 6000-13000; our
    # E is below that range -> extrapolation). Forecastle 0.1L x 2.4 m assumed.
    dh_len = 0.085 * L                                       # generator deckhouse x=0.025..0.11L
    fc = 0.10 * L * 2.4
    E = L*(B+T) + 0.85*L*(D-T) + 0.85*fc + 0.75*dh_len*deckhouse_h
    cb1 = Cb + (1-Cb)*(0.8*D - T)/(3*T)
    Ws = 0.036 * E**1.36 * (1 + 0.5*(cb1 - 0.70))
    # Split steel: deckhouse steel ~0.055 t/m3 enclosed volume (UNVERIFIED heuristic),
    # width 0.6B; forecastle steel ~0.25 t/m2 of deck area x height factor (heuristic).
    w_dh = 0.055 * dh_len * 0.6*B * deckhouse_h
    w_fc = 0.10*L*0.8*B * 0.35
    w_coam = 0.02 * Ws                                       # hatch coamings ~2% of steel (heuristic)
    w_hull = Ws - w_dh - w_fc - w_coam
    # Kupras-type hull steel VCG (UNVERIFIED exact form, as quoted in Watson 1998):
    kg_hull = 0.01*D*(46.6 + 0.135*(0.81-Cb)*(L/D)**2) + 0.008*D*(L/B - 6.5)
    kg_dh, kg_fc, kg_coam = D + deckhouse_h/2, D + 1.2, D + 0.3
    # Machinery: Watson (1998) main engine 9.38 (slow) / 12.0 (medium) x (MCR/N)^0.84,
    # remainder 0.69 MCR^0.70 (UNVERIFIED coefficients). KG = hdb + 0.35 (D - hdb).
    w_me = (9.38 if slow_speed else 12.0) * (mcr_kw/rpm)**0.84
    w_rem = 0.69 * mcr_kw**0.70
    Wm = w_me + w_rem
    kg_m = hdb + 0.35*(D - hdb)
    # Outfit: Wo = Co L B, Co ~ 0.28 t/m2 for small container ships (UNVERIFIED, Watson chart
    # range); includes hatch covers, cell guides, lashing, accommodation outfit.
    Wo = 0.28 * L * B
    kg_o = D + 1.25 if L <= 125 else D + 1.25 + 0.01*(L-125)   # Kupras outfit VCG (UNVERIFIED)
    parts = dict(hull=(w_hull, kg_hull), coamings=(w_coam, kg_coam), deckhouse=(w_dh, kg_dh),
                 forecastle=(w_fc, kg_fc), outfit=(Wo, kg_o), machinery=(Wm, kg_m))
    m = sum(w for w, _ in parts.values()) * 1.02             # 2% design margin
    kg = sum(w*z for w, z in parts.values()) * 1.02 / m
    return m, kg, dict(E=E, Ws=Ws, Wo=Wo, Wm=Wm, parts=parts)


SHIPS = {
    # n: (L, B, D, T, MCR kW, rpm, deckhouse height above D, hdb, slow-speed?)
    500: (107.8, 19.2, 8.25, 7.06, 5940, 600, 19.0, 1.3, False),   # Arctic Trader 9L38 (brochure p1)
    '500@D9.2': (107.8, 19.2, 9.20, 7.06, 5940, 600, 19.0, 1.3, False),
    1000: (124.41, 22.5, 11.3, 8.70, 7999, 500, 19.0, 1.5, False),  # Dina Trader MaK 9M43C
    1700: (160.0, 27.6, 14.0, 9.52, 15820, 105, 19.0, 1.5, True),   # 7S60MC-C (listing)
}
DISP = {500: 9727.1, '500@D9.2': 9727.1, 1000: 16211.3, 1700: 27985.0}  # derived_hydrostatics


def main():
    for n, (L, B, D, T, mcr, rpm, hdh, hdb, slow) in SHIPS.items():
        m, kg, br = lightship(L, B, D, T, 0.6494, mcr, rpm, hdh, hdb, slow)
        print(f'\n== feeder_{n}: D-T={D-T:.2f} m  L/D={L/D:.2f}  B/D={B/D:.2f}')
        print(f'  steel E={br["E"]:.0f}  Ws={br["Ws"]:.0f} t  Wo={br["Wo"]:.0f} t  Wm={br["Wm"]:.0f} t')
        for k, (w, z) in br['parts'].items():
            print(f'    {k:10s} {w:7.0f} t  KG {z:5.2f} m')
        print(f'  lightship {m:.0f} t  KG {kg:.2f} m = {kg/D:.3f} D  LS/disp {m/DISP[n]:.3f}  DWT {DISP[n]-m:.0f} t')
        for s in (0.0, 0.2):
            f = freeboard_mm(L, D, s)
            print(f'  freeboard S={s}L: tab {f["tabular"]:.0f} +depth {f["depth"]:.0f} +sheer {f["sheer"]:.0f} '
                  f'{f["superstructure"]:+.0f} = {f["total"]:.0f} mm -> max T {D - f["total"]/1000:.2f} m '
                  f'(actual D-T {1000*(D-T):.0f}); TypeA tab {f["typeA"]:.0f}')
    # Real-ship check: Dina Trader assigned freeboard 2.609 m (brochure p1, GL notation).
    f = freeboard_mm(124.41, 11.3, 0.2)
    print(f'\nDina Trader check: approx required {f["total"]:.0f} mm vs assigned 2609 mm')
    # Tank totals vs public anchors.
    for n in (500, 1000, 1700):
        d = tomllib.load(open(ROOT/f'params/fleet/feeder_{n}.toml', 'rb'))
        wb = sum(t['capacity_m3'] for t in d['tanks'] if t['kind'] == 'ballast')
        fo = sum(t['capacity_m3'] for t in d['tanks'] if t['kind'] == 'fuel_oil')
        dwt = d['derived_hydrostatics']['implied_deadweight_kg']/1e3
        print(f'feeder_{n}: WB {wb:.0f} m3 = {wb*1.025/dwt:.1%} of synthetic DWT; FO {fo:.0f} m3')
    for name, wb, dwt in [('Arctic Trader', 2976*1.025, 6832), ('Dina Trader', 4000, 11150),
                          ('Vega Tau', 4671.8, 11797), ('Bardu', 9721*1.025, 34000)]:
        print(f'{name}: WB {wb:.0f} t = {wb/dwt:.1%} of DWT')


if __name__ == '__main__':
    main()
