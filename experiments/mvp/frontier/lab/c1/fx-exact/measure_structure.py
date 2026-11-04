"""Reproduce the first fixed small/medium graph diagnostics."""
import json
from pathlib import Path

from catalog import load_set, ROOT
from graph import summarize, build_graphs
from stowage.io import load_instance


if __name__ == '__main__':
    rows = []
    folder = Path(__file__).resolve().parent / 'results'
    for name in ('quick_small', 'quick_medium'):
        instance = load_instance(ROOT / load_set(name)[0])
        rows.append(summarize(instance))
        incidence, _, _ = build_graphs(instance)
        lines = ['graph incidence {']
        lines.extend('  ' + json.dumps(node) + ';' for node in sorted(incidence))
        lines.extend('  ' + json.dumps(left) + ' -- ' + json.dumps(right) + ';'
                     for left in sorted(incidence) for right in sorted(incidence[left])
                     if left < right)
        lines.append('}')
        (folder / f'{name}-incidence.dot').write_text('\n'.join(lines) + '\n')
    target = Path(__file__).resolve().parent / 'results/STRUCTURE.json'
    target.write_text(json.dumps(rows, indent=2) + '\n')
    print(json.dumps(rows, indent=2))
