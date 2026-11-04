"""Losslessly transcribe published KCS offsets; no geometric interpolation.

Run: uv run --no-project --python 3.12 experiments/data-fetch/kcs_offsets.py
Source data are inert. readhull.txt specifies the whitespace token format.
"""
from pathlib import Path
import csv
import json

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / 'data/raw/kcs'

def main():
    lines = (RAW / 'kcs.fix').read_text().splitlines()
    tokens = iter('\n'.join(lines[1:]).split())
    station_count = int(next(tokens))
    rows = []
    stations = []
    for station in range(1, station_count + 1):
        x, count = next(tokens), int(next(tokens))
        breadths = [next(tokens) for _ in range(count)]
        heights = [next(tokens) for _ in range(count)]
        stations.append((x, count))
        rows.extend((station, point, x, y, z) for point, (z, y) in enumerate(zip(heights, breadths), 1))
    assert next(tokens, None) is None
    assert station_count == 57 and len(rows) == 5700
    assert all(float(row[3]) >= 0 for row in rows)
    with (RAW / 'offsets_published.csv').open('w', newline='') as out:
        writer = csv.writer(out)
        writer.writerow(('station_index', 'point_index', 'x_m', 'y_half_m', 'z_m'))
        writer.writerows(rows)
    tokens = iter((RAW / 'kcs.prf').read_text().split())
    count = int(next(tokens))
    xs = [next(tokens) for _ in range(count)]
    zs = [next(tokens) for _ in range(count)]
    assert next(tokens, None) is None and count == 210
    with (RAW / 'profile_published.csv').open('w', newline='') as out:
        writer = csv.writer(out)
        writer.writerow(('point_index', 'x_from_ap_m', 'z_above_baseline_m'))
        writer.writerows((i, x, z) for i, (x, z) in enumerate(zip(xs, zs), 1))
    print(json.dumps({'stations': station_count, 'offset_points': len(rows), 'points_per_station': sorted({n for _, n in stations}), 'x_range_m': [min(float(x) for x, _ in stations), max(float(x) for x, _ in stations)], 'z_range_m': [min(float(r[4]) for r in rows), max(float(r[4]) for r in rows)], 'max_half_breadth_m': max(float(r[3]) for r in rows), 'profile_points': count}))

if __name__ == '__main__':
    main()
