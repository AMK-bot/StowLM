"""Interim report only; binding_report replaces it after all independent audits."""
from datetime import datetime, timezone
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN = HERE/'runs/binding'


def main():
    cases = json.loads((HERE/'binding-cases.json').read_text())['cases']
    plans = {p.stem: json.loads(p.read_text()) for p in (RUN/'plans').glob('*.json')}
    audits = {p.stem: json.loads(p.read_text()) for p in (RUN/'audits').glob('*.json')}
    lines = ['# RUNNING — Frontier under an instruction', '',
        f"Updated {datetime.now(timezone.utc).isoformat()}. {len(plans)}/20 planner records, {len(audits)}/20 independent audits. "
        'This is an interim checkpoint, not a completed binding claim. No failed case is retried.', '',
        'Twenty validation-only cases were fixed before planning: five per constraint type, literal sealed sentences, '
        'deliberately selected for violating stored baselines. Spark parsed all 20 exactly for $0.02685175. '
        'Two workers, native solvers one each, load limit 128, no repair.', '',
        '| Case | Sentence | Parsed constraint | Before | After | Planner check | Independent audit | Binding | Wall s |',
        '|---|---|---|---:|---:|---|---|---|---:|']
    for case in cases:
        cid = case['case_id']
        parsed = json.loads((RUN/'parser'/f'{cid}.json').read_text())
        row, audit = plans.get(cid, {}), audits.get(cid, {})
        wall = row.get('wall_s')
        lines.append(f"| {cid} | {case['sentence'].replace('|', '/')} | "
            f"`{json.dumps(parsed['parsed'].get('constraint'), separators=(',', ':'))}` | "
            f"{row.get('unconstrained_moves', case['baseline_weighted'])} | {row.get('constrained_moves', 'pending')} | "
            f"{row.get('passed', 'pending')} | {audit.get('passed', 'pending')} | {audit.get('binding', 'pending')} | "
            f"{round(wall, 1) if wall is not None else 'pending'} |")
    lines += ['', 'The final report will include fresh full-checker/mask replay results, intrinsic binding decisions '
              '(software cover-map mismatch alone never counts), detailed move changes for OnePortPerHatch, and spend reconciliation.']
    (HERE/'BINDING.md').write_text('\n'.join(lines)+'\n')
    print(f'{len(plans)}/20 planner records; {len(audits)}/20 independent audits')


if __name__ == '__main__':
    main()
