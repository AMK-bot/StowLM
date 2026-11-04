"""Render D205/D206 development gates from freshly audited hybrid trial rows.

Usage: report.py runs/val --output experiments/mvp/hybrid/HYBRID.md
Incomplete reports are explicitly provisional. --require-complete rejects them.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

try:
    from .tables import audited_pass, extra_origin, gates, origin, paired, retained, interval, sign, selected_extra
    from .validation import require_active_directory, require_manifest, require_row
except ImportError:
    # Do not import frontier/tables.py if frontier already occupies sys.path.
    import importlib.util
    spec = importlib.util.spec_from_file_location('hybrid_report_tables', Path(__file__).with_name('tables.py'))
    tables = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tables)
    audited_pass, extra_origin, gates, origin, paired, retained, interval, sign, selected_extra = (getattr(tables, k) for k in
        ('audited_pass', 'extra_origin', 'gates', 'origin', 'paired', 'retained', 'interval', 'sign', 'selected_extra'))
    spec = importlib.util.spec_from_file_location('hybrid_report_validation', Path(__file__).with_name('validation.py'))
    validation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validation)
    require_active_directory, require_manifest, require_row = (getattr(validation, k) for k in
        ('require_active_directory', 'require_manifest', 'require_row'))

ARMS = ('none', 'llm', 'random')
HERE = Path(__file__).resolve().parent
OUTPUT_ROOT = Path(os.environ.get('HYBRID_WRITE_ROOT', HERE)).resolve()


def load_rows(run_dir: Path, manifest=None) -> dict:
    run_dir = Path(run_dir)
    require_active_directory(run_dir)
    if manifest is None:
        manifest = json.loads((run_dir/'manifest.json').read_text())
    require_manifest(manifest)
    arms = {arm: {} for arm in ARMS}
    for arm in ARMS:
        for path in sorted((run_dir / arm).glob('*.json')):
            if path.name.endswith('.audit.json'):
                continue
            row = json.loads(path.read_text())
            if not isinstance(row, dict) or 'instance_id' not in row:
                continue
            iid = row['instance_id']
            if iid in arms[arm]:
                raise ValueError(f'duplicate {arm}/{iid}')
            require_row(row, manifest=manifest, expected_iid=path.stem, expected_arm=arm,
                        expected_input=manifest['maps'].get(iid))
            inp = str(row.get('input', '')).replace('\\', '/')
            if '.small.' not in iid or '/val/small/' not in '/' + inp or any(
                    part in inp.split('/') for part in ('test', 'test-v3', 'showcase')):
                raise ValueError(f'non-validation-small row refused: {path}')
            if row.get('passed'):
                origin(row)  # Missing ancestry must never silently count as non-LLM.
                if row.get('weighted') is None:
                    raise ValueError(f'passing row lacks weighted objective: {path}')
            arms[arm][iid] = row
    return arms


def _date(row: dict, end: bool = False):
    names = ('end_utc', 'ended_at', 'end') if end else ('start_utc', 'started_at', 'start')
    for name in names:
        value = row.get(name)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
            except ValueError:
                pass
    return None


def _timeout_marker(row: dict) -> bool:
    status = ' '.join(str(row.get(k, '')) for k in ('status', 'error', 'finalize_status', 'message')).lower()
    return bool(row.get('timed_out') or row.get('timeout') or any(
        marker in status for marker in ('timeout', 'timed out', 'time limit', 'time_limit')))


def _trial_timeout(row: dict) -> bool:
    return _timeout_marker(row) or _finisher_timeouts(row) > 0


def _finisher_timeouts(row: dict) -> int:
    # The same finish can appear in tries and *_finish; count each label once.
    finishes = {r.get('label', str(j)): r for j, r in enumerate(row.get('tries', []))}
    for key, value in row.items():
        if key.endswith('_finish') and isinstance(value, dict):
            finishes[value.get('label', key)] = value
    budget = float(row.get('finalize_s', 300))
    def timed_out(attempt):
        exhausted = (attempt.get('finalize_status') != 'ok' and
                     (float(attempt.get('finalize_s', 0)) >= .9 * budget or
                      'budget' in str(attempt.get('message', '')).lower()))
        return _timeout_marker(attempt) or exhausted
    return sum(timed_out(r) for r in finishes.values())


def _number(value, places=3):
    return '—' if value is None else f'{value:.{places}f}'


def _numeric_line(label, left, right):
    ids = sorted(left.keys() & right.keys())
    pairs = [(float(left[i]), float(right[i])) for i in ids]
    if not pairs:
        return f'| {label} | 0 | — | — | — | 0 / 0 / 0 | 1 | — |'
    a, b = sum(x for x, _ in pairs)/len(pairs), sum(y for _, y in pairs)/len(pairs)
    fewer, more = sum(x < y for x, y in pairs), sum(x > y for x, y in pairs)
    ci = interval(pairs)
    interval_text = '—' if ci is None else f'{ci[0]:+.2%} to {ci[1]:+.2%}'
    gain = f'{1-a/b:+.2%}' if b else '—'
    return (f'| {label} | {len(pairs)} | {a:.3f} | {b:.3f} | {gain} | '
            f'{fewer} / {len(pairs)-fewer-more} / {more} | {sign(fewer, more):.8g} | {interval_text} |')


def _diagnostics(arms, manifest):
    costs = {a: {i: r['weighted'] for i, r in arms[a].items() if audited_pass(r, manifest)} for a in ARMS}
    head = ['| Comparison | Maps | Left mean | Right mean | Savings | Fewer / equal / more | Exact sign p | Bootstrap 95% CI |',
            '|---|---:|---:|---:|---:|---|---:|---|']
    declared_aa = set((manifest.get('groups') or {}).get('aa', []))
    lines = ['', 'A/A timing noise: the primary control uses only the predeclared A/A map group, '
             'and requires that no extra start entered either relevant arm’s searched top two. '
             'Other actual software-only repeats are exploratory and reported separately. '
             'A magnitude ≥3% is a noise-floor warning for interpreting H2/H3, '
             'without changing their fixed gate labels.', '', *head]
    noisy = []
    exploratory = []
    for left, right in (('llm', 'none'), ('random', 'none'), ('llm', 'random')):
        actual = {i for i in costs[left].keys() & costs[right].keys()
               if not selected_extra(arms[left][i], left) and not selected_extra(arms[right][i], right)}
        ids = actual & declared_aa
        lines.append(_numeric_line(f'A/A declared {left} vs {right}', {i: costs[left][i] for i in ids},
                                   {i: costs[right][i] for i in ids}))
        extras = actual - declared_aa
        exploratory.append(_numeric_line(f'A/A exploratory actual no-extra {left} vs {right}',
                            {i: costs[left][i] for i in extras}, {i: costs[right][i] for i in extras}))
        baseline = sum(costs[right][i] for i in ids)
        if baseline and abs(1-sum(costs[left][i] for i in ids)/baseline) >= .03:
            noisy.append(f'{left} vs {right}')
        contaminated = [i for i in declared_aa if i in arms[left] and i in arms[right]
                        and (selected_extra(arms[left][i], left) or selected_extra(arms[right][i], right))]
        if contaminated:
            exploratory.append(f'Primary A/A {left} vs {right} excluded {len(contaminated)} declared control maps '
                               'because an extra start was actually searched: ' + ', '.join(sorted(contaminated)) + '.')
    lines += ['', *head, *exploratory]
    if not declared_aa:
        lines += ['', 'No predeclared A/A group is present; primary timing-noise validation is unavailable.']
    lines += ['', ('A/A magnitude ≥3% for ' + ', '.join(noisy) +
                   '; H2/H3 effects cannot be distinguished from this timing-noise check.') if noisy else
              'No available A/A comparison reaches a 3% magnitude; this does not prove absence of timing noise.']
    aa_lines, lines = lines, []
    without = {a: {i: r for i, r in arms[a].items() if '40100025' not in i} for a in ARMS}
    lines += ['', 'H2 sensitivity to the predeclared outlier map 40100025; all means remain paired. '
              'The without-map calculation is diagnostic and does not replace H2.', '', *head,
              _numeric_line('LLM vs none, all maps', costs['llm'], costs['none']),
              _numeric_line('LLM vs none, excluding 40100025',
                            {i: c for i, c in costs['llm'].items() if '40100025' not in i},
                            {i: c for i, c in costs['none'].items() if '40100025' not in i})]
    reduced = gates(without, manifest)
    lines += ['', f"H2 without 40100025: {'met' if reduced['H2'] else 'not met'} (diagnostic)."]
    portfolio = {i: min(costs[a][i] for a in ('llm', 'none') if i in costs[a])
                 for i in costs['llm'].keys() | costs['none'].keys()}
    lines += ['', f'Post hoc checker-picked portfolio `min(llm, none)`: {len(portfolio)}/{manifest["n_maps"]} passes. '
              'It removes search-slot displacement after both arms have run and uses twice the arm budget; '
              'it is not an experimental arm or a replacement for H2.', '', *head,
              _numeric_line('Post hoc portfolio vs none', portfolio, costs['none'])]
    sensitivity_lines, lines = lines, []
    admitted = {a: {i: min(float(c['cost']) for c in r.get('candidates', {}).get('admitted', []))
                    for i, r in arms[a].items() if r.get('candidates', {}).get('admitted')}
                for a in ('llm', 'random')}
    baseline_starts = {i: min(float(s['objective']) for s in r.get('starts', []))
                       for i, r in arms['none'].items() if r.get('starts')}
    lines += ['', 'Start-level comparison uses compacted/replay-admitted candidates before the sight gate and search. '
              'A start has not yet passed the finisher/checker, so this table is not a final-plan result.', '', *head,
              _numeric_line('Best LLM admitted vs best none start', admitted['llm'], baseline_starts),
              _numeric_line('Best random admitted vs best none start', admitted['random'], baseline_starts),
              _numeric_line('Best LLM admitted vs best random admitted', admitted['llm'], admitted['random']), '',
              '| Arm | Stored files | Files per map (min–max) | Completed candidates | Distinct candidates | Replay-admitted | Maps with extra searched |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for arm in ('llm', 'random'):
        rows = list(arms[arm].values())
        metadata = [r.get('candidates', {}) for r in rows]
        sizes = [m.get('n_files', 0) for m in metadata]
        size_range = f'{min(sizes)}–{max(sizes)}' if sizes else '—'
        lines.append(f"| {arm} | {sum(m.get('n_files', 0) for m in metadata)} | "
                     f"{size_range} | {sum(m.get('n_complete', 0) for m in metadata)} | {sum(m.get('n_distinct', 0) for m in metadata)} | "
                     f"{sum(len(m.get('admitted', [])) for m in metadata)} | {sum(selected_extra(r, arm) for r in rows)} |")
    start_lines, lines = lines, []
    reference = manifest.get('reference_rows', {})
    ref_costs = {i: r.get('weighted') for i, r in reference.items() if r.get('passed') and r.get('weighted') is not None}
    mutual = costs['none'].keys() & ref_costs.keys()
    ratios = []
    for iid in sorted(mutual):
        old = {(r.get('start'), r.get('start_objective')): r for r in reference[iid].get('runs', [])}
        for run in arms['none'][iid].get('runs', []):
            previous = old.get((run.get('start'), run.get('start_objective')))
            if previous and previous.get('iterations', 0) > 0:
                ratios.append(float(run.get('iterations', 0))/previous['iterations'])
    lines += ['', 'Frontier strength validity check: fresh `none` must have mean ≤12.14×1.03 = 12.5042 moves '
              'and reach ≥70% of sealed val-0.9 iterations on matching start labels/objectives. '
              'Matched-map reference means are also reported for context. '
              'A miss labels the measurement as a weakened frontier; it does not change the numerical gate formulas.']
    fresh_mean_all = sum(costs['none'].values())/len(costs['none']) if costs['none'] else None
    if mutual:
        fresh_mean = sum(costs['none'][i] for i in mutual)/len(mutual)
        old_mean = sum(ref_costs[i] for i in mutual)/len(mutual)
        slow = sum(ratio < .7 for ratio in ratios)
        weakened = fresh_mean_all > 12.14*1.03 or slow > 0
        lines += ['', f"Fresh none mean {fresh_mean:.3f} vs reference {old_mean:.3f} on {len(mutual)} maps; "
                  f"all fresh none passes mean {fresh_mean_all:.3f}, fixed +3% limit 12.5042; "
                  f'{slow}/{len(ratios)} matching search runs below 70% of reference iterations. '
                  f"{'Measured a weakened frontier.' if weakened else 'No weakening detected by the available checks.'}"]
        if not ratios:
            lines += ['Iteration validity check unavailable: no matching reference start/objective runs.']
    else:
        lines += ['', 'Frontier strength diagnostic unavailable: no sealed matching reference passes.']
        if fresh_mean_all is not None:
            lines.append(f'All fresh none passes mean {fresh_mean_all:.3f}, fixed +3% limit 12.5042. '
                         + ('Measured a weakened frontier.' if fresh_mean_all > 12.14*1.03 else
                            'Mean criterion passes; iteration criterion remains unavailable.'))
    return dict(start=start_lines, aa=aa_lines, sensitivity=sensitivity_lines, strength=lines)


def render(arms: dict, *, manifest: dict, expected: int | None = None, run_dir: str = '') -> tuple[str, bool]:
    require_manifest(manifest)
    expected = manifest['n_maps'] if expected is None else expected
    if manifest['n_maps'] != expected:
        raise ValueError('expected map count differs from the sealed manifest')
    if run_dir:
        require_active_directory(run_dir)
    for arm in ARMS:
        for iid, row in arms[arm].items():
            require_row(row, manifest=manifest, expected_iid=iid, expected_arm=arm,
                        expected_input=manifest['maps'].get(iid))
            if row.get('passed'):
                origin(row)
    rows = [r for arm in ARMS for r in arms[arm].values()]
    same_ids = all(set(arms[a]) == set(arms['none']) for a in ARMS)
    audit_pending = sum(bool(r.get('passed')) and not audited_pass(r, manifest) for r in rows)
    infrastructure_errors = sum(bool(r.get('error')) for r in rows)
    complete = (same_ids and all(len(arms[a]) == expected for a in ARMS) and audit_pending == 0
                and all(r.get('completed') is True for r in rows) and infrastructure_errors == 0)
    stats = gates(arms, manifest)
    diagnostics = _diagnostics(arms, manifest)
    lines = ['# Hybrid frontier development (D199 route 1; D205/D206 protocol)', '',
             '**Status: final.**' if complete else '**Status: incomplete; gate results are provisional.**', '',
             f'Results: {len(rows)}/{expected * 3} completed trials on {expected} validation-small maps. '
             f'Passing trials awaiting a valid fresh-process audit: {audit_pending}. '
             f'Rows with execution/audit errors: {infrastructure_errors}.', '',
             f'Development only: this cohort uses {expected} maps declared before measurement. '
             + (f"D205 selects {len(manifest.get('groups', {}).get('entering', []))} entry maps and "
                f"{len(manifest.get('groups', {}).get('aa', []))} no-entry A/A maps. "
                if manifest.get('groups') else '')
             + 'These validation results select a recipe; '
             'performance claims await one fresh test-v3 evaluation with LLM vs none as the primary comparison. '
             'No test-v3 input is used here.', '',
             'Stored candidates only; no decoding or repair. Seed 0; search 6 s per box capped at 120 s; '
             'finisher 300 s. The arms share one map-interleaved queue and the same total search budget. '
             'Native library threads are capped at one; CPU only. '
             'Only a full-checker pass confirmed by a fresh-process audit counts below.', '',
             f'Run directory: `{run_dir}`.', '',
             f"Sealed cohort: `{manifest['cohort_id']}`; immutable runtime: `{manifest['runtime']['id']}`.", '',
             'Report and resume use the same row validator: the cohort, exact trial identity and binding, '
             'final-plan SHA-256, three agreeing costs, complete replay/checker audit, and no repair are required. '
             'D206 admission requires one-minute load ≤45 and a recommended-core mask with ≥8 set bits; '
             'the leader core-state reading must be less than 120 s old, and four lanes remain the cap. '
             'The symmetric outer watchdog allows 3600 s for planning and 600 s for audit, '
             'with 5 s terminate and kill grace periods.', '',
             '| Arm | Completed | Audited passes | Trial timeouts | Finisher timeouts | Worker wall time (s) |',
             '|---|---:|---:|---:|---:|---:|']
    if run_dir and (Path(run_dir)/'HOLD').exists():
        lines[2] = '**Status: armed and held for skill-check Part 2; new trial starts blocked.**'
    for arm in ARMS:
        rs = list(arms[arm].values())
        lines.append(f"| {arm} | {len(rs)}/{expected} | {stats['passes'][arm]}/{expected} | "
                     f"{sum(_trial_timeout(r) for r in rs)} | {sum(_finisher_timeouts(r) for r in rs)} | "
              f"{sum(float(r.get('wall_s', 0)) for r in rs):.1f} |")
    lines += ['', 'Trial timeouts count trials with any timed-out finisher or explicit process timeout. '
              'Finisher timeouts count distinct attempts: explicit timeout/time-limit/budget messages, '
              'or a non-ok finish using ≥90% of its 300 s allocation. Duplicate fallback records are counted once.']
    lines += diagnostics['start'] + diagnostics['aa']
    lines += ['', 'Means and sign tests use only maps where both arms passed the fresh audit. '
              'Exact two-sided sign tests exclude ties; positive savings mean the left arm used fewer moves.', '',
              'Bootstrap intervals resample paired maps 10,000 times with seed 0.', '',
              '| Comparison | Mutual maps | Left mean moves | Right mean moves | Savings | Fewer | Equal | More | Exact sign p | Bootstrap 95% CI |',
              '|---|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for left, right in (('llm', 'none'), ('random', 'none'), ('llm', 'random')):
        s = paired(arms[left], arms[right], manifest)
        gain = '—' if s['gain'] is None else f"{s['gain']:.2%}"
        ci = interval([(arms[left][i]['weighted'], arms[right][i]['weighted']) for i in s['ids']])
        ci_text = '—' if ci is None else f'{ci[0]:+.2%} to {ci[1]:+.2%}'
        lines.append(f"| {left} vs {right} | {s['n']} | {_number(s['mean_left'])} | {_number(s['mean_right'])} | "
                     f"{gain} | {s['fewer']} | {s['equal']} | {s['more']} | {s['p']:.8g} | {ci_text} |")
    lines += ['', 'Final-plan origins are read with `tables.origin` from recorded ancestry. '
              'Multiple search runs reaching the final stow are displayed as `shared`; '
              'the exact producing seed remains in `final_search_origin`. Matching objectives do not establish lineage.', '',
              '| Arm | Final origin | Audited plans | Search changed | Search unchanged |',
              '|---|---|---:|---:|---:|']
    for arm in ARMS:
        passed = [r for r in arms[arm].values() if audited_pass(r, manifest)]
        counts = Counter(origin(r) for r in passed)
        for name, count in sorted(counts.items()):
            changed = sum(origin(r) == name and r['final_search_changed'] for r in passed)
            lines.append(f'| {arm} | `{name.replace("|", "&#124;")}` | {count} | {changed} | {count-changed} |')
        if not counts:
            lines.append(f'| {arm} | — | 0 | 0 | 0 |')
    lines += ['', '| Arm | Actual producing seed | Audited plans | Shared stows |',
              '|---|---|---:|---:|']
    for arm in ARMS:
        passed = [r for r in arms[arm].values() if audited_pass(r, manifest)]
        for producer, count in sorted(Counter(r['final_search_origin'] for r in passed).items()):
            shared = sum(r['final_search_origin'] == producer and r['final_shared_stow'] for r in passed)
            lines.append(f'| {arm} | `{producer.replace("|", "&#124;")}` | {count} | {shared} |')
    lines += ['', 'Maps where an extra start supplied the final plan (including search descendants), '
              'paired against the fresh `none` arm. Savings are signed and include ties or regressions.', '',
              '| Arm | Map | Final moves | None moves | Moves saved | Search changed |',
              '|---|---|---:|---:|---:|---|']
    for arm in ('llm', 'random'):
        winners = []
        for iid in paired(arms[arm], arms['none'], manifest)['ids']:
            r, baseline = arms[arm][iid], arms['none'][iid]
            if extra_origin(r, arm):
                saved = float(baseline['weighted']) - float(r['weighted'])
                winners.append(saved)
                lines.append(f"| {arm} | {iid} | {r['weighted']:g} | {baseline['weighted']:g} | {saved:+g} | "
                             f"{'yes' if r['final_search_changed'] else 'no'} |")
        lines.append(f'| {arm} total | {len(winners)} maps | — | — | {sum(winners):+g} | — |')
    lines += ['', 'Retained presence (logic P0.1) requires an extra producing seed, ≥50% of load-list '
              'box-to-slot pairs retained, and ≥2 retained pairs absent from fresh none. The H1 threshold '
              'remains ≥20% of mutual maps. Pre-result P1.7 attribution refinement: a final stow reached '
              'by both extra and software search runs is excluded from exclusive H1/H1m and reported '
              'as shared-qualified. A stow shared only by extra runs may count using its actual producing seed. '
              'The same rule applies to random. H1 measures presence; H1m additionally requires ≥1 move saved.', '',
              '| Arm | Map | Retained / load boxes | Retained fraction | Unique vs none | Moves saved | Exclusive H1 presence | Shared-qualified | H1m |',
              '|---|---|---:|---:|---:|---:|---|---|---|']
    summaries = {}
    for arm in ('llm', 'random'):
        measures = []
        for iid in paired(arms[arm], arms['none'], manifest)['ids']:
            r = arms[arm][iid]
            m = retained(r, arms['none'][iid], arm)
            measures.append(m)
            if extra_origin(r, arm):
                lines.append(f"| {arm} | {iid} | {m['retained']}/{m['total']} | {m['fraction']:.1%} | "
                             f"{m['unique']} | {m['saved']:+g} | {'yes' if m['qualified'] else 'no'} | "
                             f"{'yes' if m['shared_qualified'] else 'no'} | {'yes' if m['H1m'] else 'no'} |")
        summaries[arm] = dict(mutual=len(measures), presence=sum(m['qualified'] for m in measures),
                              shared=sum(m['shared_qualified'] for m in measures),
                              h1m=sum(m['H1m'] for m in measures),
                              positive_saved=sum(m['saved'] for m in measures if m['H1m']),
                              signed_saved=sum(m['saved'] for m in measures if m['qualified']))
    lines += ['', '| Arm | Mutual maps | Exclusive retained presence | Shared-qualified | H1m maps saving ≥1 | Moves saved on H1m maps | Signed savings on presence maps |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for arm, m in summaries.items():
        lines.append(f"| {arm} | {m['mutual']} | {m['presence']} | {m['shared']} | {m['h1m']} | "
                     f"{m['positive_saved']:+g} | {m['signed_saved']:+g} |")
    a, b = summaries['llm'], summaries['random']
    lines.append(f"| LLM minus random | — | {a['presence']-b['presence']:+g} | {a['shared']-b['shared']:+g} | "
                 f"{a['h1m']-b['h1m']:+g} | {a['positive_saved']-b['positive_saved']:+g} | "
                 f"{a['signed_saved']-b['signed_saved']:+g} |")
    lines += diagnostics['sensitivity'] + diagnostics['strength']
    starts, ends = [_date(r) for r in rows], [_date(r, True) for r in rows]
    starts, ends = [d for d in starts if d], [d for d in ends if d]
    lines += ['', f"Elapsed trial window: {(max(ends)-min(starts)).total_seconds():.1f} s "
              f"({min(starts).isoformat()} to {max(ends).isoformat()})." if starts and ends else
              '\nElapsed trial window: unavailable until timestamped rows arrive.']
    loads = [(a, i, r.get('load_start'), r.get('load_end')) for a in ARMS for i, r in sorted(arms[a].items())]
    load_missing = sum(start is None or end is None for _, _, start, end in loads)
    audit_failures = sum('audit' in r and not r['audit'].get('passed') for r in rows)
    lines += ['', f'Fresh audits reporting failure: {audit_failures}.', '',
              f'Per-trial start, end, and load are retained in the trial JSON rows. '
              f'Rows missing a start/end load reading: {load_missing}.', '',
              'D205’s gates and the shared-stow attribution rule were fixed before accepted results. '
              'Final gate labels require every declared trial and audit to finish.', '',
              f"- H1 **{'met' if complete and stats['H1'] else 'not met'}**: exclusive retained LLM presence on "
              f"{stats['presence']}/{stats['presence_denominator']} mutual LLM/none passes; threshold ≥20%.",
              f"- H2 **{'met' if complete and stats['H2'] else 'not met'}**: ≥3% lower mutual mean than none, "
              'fewer moves with exact sign p<0.05, and no fewer passes.',
              f"- H3 **{'met' if complete and stats['H3'] else 'not met'}**: lower mutual mean than random, "
              'fewer moves with exact sign p<0.05, and at least as many passes as random.', '']
    if not complete:
        lines += ['These are provisional “not met” labels, not conclusions from the completed experiment.', '']
    if manifest.get('profile'):
        lines += ['Pre-measurement iteration-gap profile (diagnostic, excluded from cohort results): '
                  + str(manifest['profile'].get('conclusion', 'See PROFILE.md for findings.')), '',
                  '[Full profile and evidence](PROFILE.md). '
                  + f"Sealed report SHA-256: `{manifest.get('profile_report_sha256', '')}`.", '']
    return '\n'.join(lines), complete


def write_report(runs_dir, output=OUTPUT_ROOT/'HYBRID.md', *, expected=None, require_complete=True):
    """Controller API; final reports require every manifest-declared audited trial."""
    output = Path(output).resolve()
    if not output.is_relative_to(OUTPUT_ROOT):
        raise ValueError(f'report output must stay under {OUTPUT_ROOT}')
    require_active_directory(runs_dir)
    manifest = json.loads((Path(runs_dir)/'manifest.json').read_text())
    content, complete = render(load_rows(Path(runs_dir), manifest), manifest=manifest,
                               expected=expected, run_dir=str(runs_dir))
    if require_complete and not complete:
        raise ValueError('all arms and fresh audits must be complete before producing a final report')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + '.tmp')
    temporary.write_text(content)
    temporary.replace(output)
    return complete


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs_dir', type=Path)
    parser.add_argument('--output', type=Path, default=OUTPUT_ROOT/'HYBRID.md')
    parser.add_argument('--expected', type=int, default=None)
    parser.add_argument('--require-complete', action='store_true')
    args = parser.parse_args(argv)
    try:
        complete = write_report(args.runs_dir, args.output, expected=args.expected,
                                require_complete=args.require_complete)
    except ValueError as error:
        parser.error(str(error))
    print(f"{'Final' if complete else 'Provisional'} report: {args.output}")


if __name__ == '__main__':
    main()
