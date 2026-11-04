"""Tiny independent offset sanity check, not a production hydrostatics solver."""
from pathlib import Path
import csv
from collections import defaultdict

raw = Path(__file__).resolve().parents[2] / 'data/raw/kcs'
stations = defaultdict(list)
for row in csv.DictReader((raw / 'offsets_published.csv').open()):
    stations[float(row['x_m'])].append((float(row['y_half_m']), float(row['z_m'])))

def immersed_area(points, draft=10.8):
    # Close the half-section through the centerplane; clip above the waterline.
    points = [(0.0, points[0][1]), *points, (0.0, points[-1][1])]
    clipped = []
    for a, b in zip(points, points[1:] + points[:1]):
        ina, inb = a[1] <= draft, b[1] <= draft
        if ina: clipped.append(a)
        if ina != inb:
            t = (draft - a[1]) / (b[1] - a[1])
            clipped.append((a[0] + t * (b[0] - a[0]), draft))
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(clipped, clipped[1:] + clipped[:1])))

areas = [(x, immersed_area(p)) for x, p in sorted(stations.items())]
# APPROXIMATION: polygonal section boundaries and trapezoidal longitudinal
# integration validate source scale/half-breadths only; not production hull physics.
volume = sum((b[0] - a[0]) * (a[1] + b[1]) / 2 for a, b in zip(areas, areas[1:]))
moment = sum((b[0] - a[0]) * (a[0] * a[1] + b[0] * b[1]) / 2 for a, b in zip(areas, areas[1:]))
cb = volume / (230 * 32.2 * 10.8)
print(f'polygon/trapezoid volume={volume:.6f} m3; Cb={cb:.8f}; delta_vs_52030={100 * (volume/52030-1):.6f}%')
print(f'polygon/trapezoid LCB={moment / volume:.6f} m from AP; {100 * (moment / volume - 115) / 230:.6f}% Lpp forward of midship')
assert abs(volume/52030-1) < 0.005
assert abs(cb-.6505) < .0033
assert abs(100 * (moment / volume - 115) / 230 + 1.48) < .1
