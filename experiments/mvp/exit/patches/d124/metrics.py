"""Paired survival gate and dependency-free measured learning curves."""
from __future__ import annotations

import csv
import math
from pathlib import Path
from statistics import mean
from xml.sax.saxutils import escape

from llm.exit.outcomes import checker_unavailable, outcome_counts


def _rows(rows):
    result = {}
    for row in rows:
        identity = str(row.get('instance_id', row.get('instance', row.get('id', ''))))
        if not identity or identity in result:
            raise ValueError('validation rows require unique instance_id')
        passed = (not checker_unavailable(row)
                  and row.get('passed', row.get('own_plan_passed', False)) is True)
        moves = row.get('weighted_moves', row.get('weighted_nonproductive'))
        if passed and (not isinstance(moves, (int, float)) or not math.isfinite(moves) or moves < 0):
            raise ValueError(f'passing row lacks finite nonnegative measured moves: {identity}')
        result[identity] = (passed, moves)
    return result


def compare_validation(parent_rows, child_rows) -> dict:
    """Higher own-plan pass count wins; ties use moves only on mutual passes.

    No repair/fallback plans belong in these rows. Missing IDs or empty validation
    cannot authorize an adapter replacement. Exact ties keep the parent.
    """
    parent_rows, child_rows = list(parent_rows), list(child_rows)
    parent, child = _rows(parent_rows), _rows(child_rows)
    if not parent or parent.keys() != child.keys():
        raise ValueError('parent and child must cover the same nonempty fixed validation set')
    unavailable_ids = {str(row.get('instance_id', row.get('instance', row.get('id', ''))))
                       for row in [*parent_rows, *child_rows] if checker_unavailable(row)}
    # D105/D110: only verified passes count; unavailable is not a physics
    # failure and also not a pass. Preserve every paired map in the denominator.
    pc, cc = sum(x[0] for x in parent.values()), sum(x[0] for x in child.values())
    mutual = [i for i in parent if parent[i][0] and child[i][0]]
    pm = mean(parent[i][1] for i in mutual) if mutual else None
    cm = mean(child[i][1] for i in mutual) if mutual else None
    accepted = cc > pc or (cc == pc and bool(mutual) and cm < pm)
    reason = ('higher_pass_count' if cc > pc else 'lower_pass_count' if cc < pc else
              'fewer_mutual_pass_moves' if accepted else 'no_mutual_passes' if not mutual else 'no_strict_improvement')
    return {'accepted': accepted, 'reason': reason, 'validation_count': len(parent),
            'parent_outcomes': outcome_counts(parent_rows), 'child_outcomes': outcome_counts(child_rows),
            'pass_count_denominator': len(parent),
            'available_pair_count': len(parent)-len(unavailable_ids),
            'unavailable_ids': sorted(unavailable_ids),
            'parent_pass_count': pc, 'child_pass_count': cc,
            'parent_pass_rate': pc / len(parent), 'child_pass_rate': cc / len(parent),
            'mutual_pass_count': len(mutual), 'parent_mutual_mean_moves': pm,
            'child_mutual_mean_moves': cm}


def append_learning_curve(path: str | Path, row: dict) -> None:
    """Append measured generation values; rewrite safely when columns evolve."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    old, fields = [], []
    if path.exists():
        with path.open(newline='') as stream:
            reader = csv.DictReader(stream)
            fields = list(reader.fieldnames or [])
            old = list(reader)
    fields += [name for name in row if name not in fields]
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(old + [row])
    temporary.replace(path)


def write_learning_plot(csv_path: str | Path, svg_path: str | Path) -> None:
    """Plot measured pass rates and mutual-pass moves; missing points stay missing."""
    with Path(csv_path).open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    width, height = 850, 440
    colors = ('#2e86de', '#d35400')
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
           '<rect width="100%" height="100%" fill="white"/>',
           '<text x="45" y="32" font-family="sans-serif" font-size="22">StowLM generations · measured validation</text>']
    panels = [('child_pass_rate', 'Own-plan pass rate', 0, 1),
              ('child_mutual_mean_moves', 'Weighted moves on mutual passes', None, None)]
    for panel, (key, title, lower, upper) in enumerate(panels):
        points = []
        for index, row in enumerate(rows):
            raw = row.get(key, '')
            if raw not in ('', 'None', None):
                value = float(raw)
                if math.isfinite(value):
                    points.append((index, value))
        x0, y0, pw, ph = 65 + panel * 410, 85, 335, 265
        if lower is None:
            lower = 0
            upper = max([p[1] for p in points] + [1]) * 1.1
        svg.append(f'<text x="{x0}" y="65" font-family="sans-serif" font-size="14">{escape(title)}</text>')
        svg.append(f'<path d="M{x0},{y0} V{y0+ph} H{x0+pw}" fill="none" stroke="#555"/>')
        for index, value in points:
            x = x0 + index / max(1, len(rows)-1) * pw
            y = y0 + ph - (value-lower)/(upper-lower)*ph
            generation = rows[index].get('generation', str(index))
            svg.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="{colors[panel]}"><title>Generation {escape(generation)}: {value:g}</title></circle>')
            svg.append(f'<text x="{x:.2f}" y="{y0+ph+22}" font-family="sans-serif" font-size="10" text-anchor="middle">{escape(generation)}</text>')
        for value in (lower, upper):
            y = y0 + ph - (value-lower)/(upper-lower)*ph
            svg.append(f'<text x="{x0-8}" y="{y+4:.2f}" text-anchor="end" font-family="sans-serif" font-size="11">{value:.2g}</text>')
    svg.append('<text x="45" y="418" font-family="sans-serif" font-size="12">Child candidates; acceptance uses paired mutual passes. Empty panels mean no measured observations.</text></svg>')
    Path(svg_path).parent.mkdir(parents=True, exist_ok=True)
    Path(svg_path).write_text('\n'.join(svg))
