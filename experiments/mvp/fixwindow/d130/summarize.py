"""Summarize actual D130 receipts and verify each new reference sidecar."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('d130_reference_audit', HERE / 'reference_audit.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
ROOT = audit.ROOT


def main():
    inv = json.loads((HERE / 'inventory.json').read_text())
    epoch = audit.source_epoch()
    summaries, details, rows = [], [], []
    for group, ids in inv['groups'].items():
        refs = [r for r in inv['references'] if r['ref_id'] in ids]
        checks = sorted((HERE / 'checks' / group).glob('*/check.json'),
                        key=lambda p: int(p.parent.name), reverse=True)
        selected = next((p for p in checks
            if json.loads(p.read_text()).get('current_tree_evidence_valid')
            and json.loads(p.read_text())['source_after'] == epoch), None)
        first = refs[0]
        summary = {'group': group, 'ship': first['ship'], 'references': len(refs),
                   'plan_sha256': first['plan_sha256'], 'ballast_present': first['ballast_present'],
                   'current_epoch_check': selected is not None}
        if selected is None:
            summaries.append(summary)
            continue
        check = json.loads(selected.read_text())
        report = json.loads((selected.parent / 'report.json').read_text())
        summary.update(passed=check['passed'], checker_s=check['checker_s'], wall_s=check['wall_s'],
            cpu_s=check['cpu_s'], peak_rss_bytes=check['peak_rss_bytes'],
            report=str((selected.parent / 'report.json').relative_to(ROOT)),
            hard_rules=sorted({v['rule_id'] for v in check['hard_violations']}),
            check_record=str(selected.relative_to(ROOT)))
        for violation in report['violations']:
            if violation['severity'] != 'hard':
                continue
            parts = [name for name, part in report['parts'].items() if violation in part['violations']]
            stages = [p for p in parts if p.startswith('condition:')] or parts or ['global/full-checker']
            for stage in stages:
                details.append({'group': group, 'plan_sha256': first['plan_sha256'], 'ship': first['ship'],
                                'stage': stage, **violation})
        for ref in refs:
            path = ROOT / ref['path']
            assert audit.digest(path.read_bytes()) == ref['file_sha256'], f'Old file changed: {path}'
            assert audit.digest(audit.canonical(audit.extract(path, ref['pointer'], ref['line']))) == ref['plan_sha256']
            stem = f'{path.name}.d130-{epoch["sha256"][:12]}-{ref["ref_id"]}-{check["attempt"]}'
            sidecar = path.with_name(stem + '.report.json')
            binding = path.with_name(stem + '.binding.json')
            assert json.loads(sidecar.read_text()) == report
            bound = json.loads(binding.read_text())
            assert bound['reference'] == ref and bound['input_bound'] and bound['current_tree_evidence_valid']
            rows.append({**ref, 'passed': check['passed'], 'report': str(sidecar.relative_to(ROOT)),
                         'binding': str(binding.relative_to(ROOT))})
        summaries.append(summary)
    details = list({audit.digest(audit.canonical(d)): d for d in details}.values())
    complete = len(rows) == len(inv['references'])
    record = {'created_utc': audit.utc(), 'source_epoch': epoch['sha256'], 'complete': complete,
              'reference_count': len(inv['references']), 'distinct_group_count': len(summaries),
              'checked_references': len(rows), 'passing_references': sum(r['passed'] for r in rows),
              'groups': summaries, 'references': rows, 'limiting_hard_rules': details,
              'all_original_reference_files_unchanged': complete,
              'all_report_sidecars_equal_actual_fresh_bound_check': complete}
    audit.save(HERE / 'SUMMARY.json', record)
    lines = ['# D130 stored showcase references — current live full checker', '',
        f'Source epoch `{epoch["sha256"]}`. {len(rows)}/{len(inv["references"])} stored references have fresh, '
        f'exact-bound full reports; {sum(r["passed"] for r in rows)} pass. '
        f'{len(summaries)} distinct exact input/full-plan pairs. No app verdict cache was used.', '',
        'Deduplication preserves every plan field, including ID and provenance; only canonical JSON formatting/key order '
        'is normalized. One fresh subprocess full check runs per distinct pair. Before copying that real report beside '
        'each duplicate reference, the production `report_matches_inputs` gate validates that exact representation. '
        'Every original reference file hash is rechecked and preserved. Full source/parameter/KCS hashes are captured '
        'before and after each check, and this summary requires the current epoch. All three frozen input hashes match '
        '`EVAL-V2-REPORT.json`; all local instance copies match their frozen input.', '',
        'The app advertises `showcase-v2-gold` versus `showcase-v2-twostep` for feeder_1700 and feeder_500. '
        'Its recording guide labels gold as a stand-in, reports a tie (37 and29 weighted moves), and excludes feeder_800. '
        'This audit does not swap the app, solve, finalize, repair, or infer instance infeasibility from a failing stored candidate.', '',
        '| Ship | Ballast stored | References | Current checker | Checker s | Fresh worker s | Plan SHA256 |',
        '|---|---|---:|---|---:|---:|---|']
    for row in sorted(summaries, key=lambda r: (r['ship'], not r['ballast_present'], r['group'])):
        verdict = ('PASS' if row.get('passed') else 'FAIL') if row['current_epoch_check'] else 'PENDING'
        lines.append(f'| {row["ship"]} | {row["ballast_present"]} | {row["references"]} | {verdict} | '
            f'{row.get("checker_s", 0):.3f} | {row.get("wall_s", 0):.3f} | `{row["plan_sha256"]}` |')
    lines += ['', '## Limiting hard rules from actual reports', '',
        'Blank measurements/limits mean the checker supplied no numeric field; no limit is invented. '
        'Messages identify the actual quantity when several quantities share a rule ID. '
        'Cargo-only raw plans are checked unchanged, with omitted ballast left omitted.', '',
        '| Plan SHA prefix | Checker part / voyage stage | Rule | Measured | Limit | Unit | Message |',
        '|---|---|---|---:|---:|---|---|']
    for d in sorted(details, key=lambda r: (r['plan_sha256'], r['stage'], r['rule_id'], r['message'])):
        cells = [d['plan_sha256'][:12], d['stage'], d['rule_id'], d.get('measured'),
                 d.get('limit'), d.get('unit'), d['message']]
        lines.append('| ' + ' | '.join('' if c is None else str(c).replace('|', '\\|') for c in cells) + ' |')
    lines += ['', '## Every stored representation', '',
        '| Reference | Pointer | Verdict | Plan SHA prefix | New full report |', '|---|---|---|---|---|']
    for r in rows:
        pointer = '$' + ''.join(f'[{p}]' if isinstance(p, int) else '.'+p for p in r['pointer'])
        if r['line'] is not None:
            pointer += f' (JSONL line {r["line"]+1})'
        lines.append(f'| `{r["path"]}` | `{pointer}` | {"PASS" if r["passed"] else "FAIL"} | '
                     f'`{r["plan_sha256"][:12]}` | `{r["report"]}` |')
    lines += ['', '## Limits and compute receipts', '',
        'CPU1/native1/noGPU. The qualified D126 private stability cache is linked by exact file into this lane\'s '
        'private cache; native cache qualification remains authoritative. Full-vessel cache misses and numerical '
        'cache misses stay private. A write audit hook rejects mutations outside this lane and authorized new sidecars. '
        'The inherited linear cache is read-only. Each fresh worker has an external240s watchdog; only its recorded '
        'Popen PID is terminated/killed and reaped on timeout. `nice -n10` is attempted but macOS sandbox denies '
        'setpriority (recorded in worker stdout); no bypass was attempted. `ps` is sandbox-denied; admission uses '
        'ledger reservations, uptime, memory pressure and measured Mach CPU accounting.', '',
        'Pilot estimate60s at1core/4GB; actual is in `pilot-process/process.json`. Remaining estimate180s from '
        'the measured pilot ×8groups ×1.4margin; actual is in `remaining-process/process.json`. '
        'Ledger/history contain only this lane\'s own reservation updates. `inventory.json` lists every scanned '
        'and pruned path and all parse errors. Timeline/cache directories are excluded; older nonmatching showcase '
        'calls do not enter the requested three-call inventory. No TEST bodies were read.', '',
        'Tool commands: `uv run --offline --no-sync python experiments/mvp/fixwindow/d130/reference_audit.py inventory`; '
        'then admitted `run --maximum 1 --limit 240` and `run --maximum 8 --limit 240`, each wrapped by the '
        'existing read-only `fixwindow/run_checked.py` with outputs under this lane. Exact arguments, native environment, '
        'owned PIDs, wall/CPU and peak RSS are in process receipts.']
    (HERE / 'SUMMARY.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps({k: record[k] for k in ('complete', 'reference_count', 'distinct_group_count',
                                           'checked_references', 'passing_references', 'source_epoch')}))
    return 0 if complete else 2


if __name__ == '__main__':
    raise SystemExit(main())
