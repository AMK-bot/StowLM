"""Keep original rung gates separate from the best checked follow-up portfolio."""
from pathlib import Path
import json

HERE = Path(__file__).resolve().parent


def main():
    manifest = json.loads((HERE/'MANIFEST.json').read_text())
    bounds = {r['input']: r for r in json.loads((HERE/'bound_ladder_results.json').read_text())}
    for row in json.loads((HERE/'bound_ports_results.json').read_text()):
        bounds[row['input']] = row
    runs = ('f60-1', 'f70-lp', 'train4-lp', 'polish-f70-500', 'quick-medium-c3')
    evidence = []
    for run in runs:
        for path in (HERE/'results'/run).glob('D*.json'):
            evidence.append(dict(json.loads(path.read_text()), evidence=str(path)))
    rows = []
    lines = ['# Fixed TRAIN fill ladder', '',
             'Original rung gates use f60-1 / f70-lp; best uses checked warm follow-ups too.', '',
             '| Ship/fill/distribution/seed | Original replay/full | Best lifts | Reloc | Hatch now/later | Restow now/later | Bound | Gap | V6 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for selected in manifest:
        path = selected['input']
        group = [r for r in evidence if r['input'] == path]
        original = next(r for r in group if f"/{'f60-1' if selected['rung']=='f60' else 'f70-lp'}/" in r['evidence'])
        passed = [r for r in group if r['passed']]
        best = min(passed, key=lambda r: (r['lifts'], r['wall_s'])) if passed else None
        certificate = bounds[path]
        assert certificate['input_sha256'] == selected['input_sha256']
        lb = certificate['lower_bound']
        assert best is None or best['lifts'] >= lb
        row = dict(selected, original_replay=bool(original['method_metadata'].get('replay_legal')),
                   original_passed=original['passed'], original_wall_s=original['wall_s'],
                   passed=best is not None, lifts=best['lifts'] if best else None,
                   parts=best['parts'] if best else None, lower_bound=lb,
                   gap=best['lifts']-lb if best else None,
                   evidence=best['evidence'] if best else None)
        rows.append(row)
        label = '/'.join(Path(path).parts[-3:]).replace('feeder_', '').replace('seed-', '').replace('.json', '')
        p = row['parts']
        count = lambda value: str(int(value)) if value is not None else '—'
        lines.append(f"| {label} | {int(row['original_replay'])}/{int(row['original_passed'])} | "
                     f"{count(row['lifts'])} | {count(p['relocations']) if p else '—'} | "
                     f"{str(p['hatch_now'])+'/'+str(p['hatch_later']) if p else '—'} | "
                     f"{str(p['restow_now'])+'/'+str(p['restow_later']) if p else '—'} | "
                     f"{count(lb)} | {count(row['gap'])} | {count(selected['v6_lifts']) if selected['v6_passed'] else 'fail'} |")
    (HERE/'ladder_comparison.json').write_text(json.dumps(rows, indent=2)+'\n')
    (HERE/'LADDER.md').write_text('\n'.join(lines)+'\n')
    print(f'Wrote {len(rows)} maps; {sum(r["passed"] for r in rows)} checked portfolio passes.')


if __name__ == '__main__':
    main()
