#!/usr/bin/env python3
"""Render recorded medium showcase evidence; no solving, imports, or physics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT = ROOT / 'experiments/mvp/runs/showcase-medium'
PHASES = ('generation', 'plan', 'finalize', 'check', 'mask', 'package')


def read(path):
    return json.loads(path.read_text()) if path.exists() else {}


def cell(value):
    return str(value).replace('|', r'\|').replace('\n', ' ')


def number(value, decimals=2):
    return f'{value:.{decimals}f}' if isinstance(value, (int, float)) else 'unavailable'


def gate(stage):
    if not stage:
        return 'not run'
    if stage.get('timed_out'):
        return 'TIMEOUT'
    if stage.get('passed'):
        return 'PASS'
    if not stage.get('finished_at') and not stage.get('error'):
        return 'unfinished'
    return 'FAIL'


def first_failure(stages):
    for name in PHASES:
        stage = stages.get(name, {})
        if stage and not stage.get('passed'):
            hard = stage.get('first_hard_violation') or {}
            if hard:
                detail = f"{hard.get('rule_id', 'unknown rule')}: {hard.get('message', '')}"
            else:
                detail = stage.get('error') or stage.get('message') or gate(stage)
            return f'{name}: {detail}'
    return 'none recorded' if all(stages.get(p, {}).get('passed') for p in ('generation', 'plan', 'finalize', 'check', 'mask')) else 'not reached; no measured failure'


def render(summary, folder):
    rows = summary.get('rows', [])
    lines = ['# Medium solver showcase', '',
        'Medium is a labelled SOLVER SHOWCASE ONLY; excluded from training and evaluation.', '',
        f"Summary checkpoint: {summary.get('generated_at', 'unavailable')}. "
        f"Recorded invocation wall time: {number(summary.get('wall_time_s'))} s. "
        'Historical phase attempts remain separate below.', '',
        'Only a complete full-checker PASS and complete production token-mask PASS with C2 off qualify. '
        'An unfinished prefix has no departure approval. Missing measurements are marked unavailable.', '',
        '| Case | Generator | Pipeline | Packaged | Full checker | Complete mask | Peak worker RSS MiB | First failure |',
        '|---|---|---|---|---|---|---:|---|']
    for row in rows:
        stages = row.get('stages', {})
        generation = stages.get('generation', {})
        generator = row.get('generator') or generation.get('generator') or 'random_yard (legacy invocation)'
        rss = [s.get('resources', {}).get('max_rss_bytes') for s in stages.values()]
        rss = [v for v in rss if isinstance(v, (int, float))]
        accepted = bool(row.get('passed') and stages.get('check', {}).get('passed') and stages.get('mask', {}).get('passed'))
        values = [row.get('case'), generator, 'PASS' if accepted else 'not accepted',
                  'yes' if row.get('packaged') else 'no', gate(stages.get('check', {})),
                  gate(stages.get('mask', {})), number(max(rss)/1024**2) if rss else 'unavailable', first_failure(stages)]
        lines.append('| ' + ' | '.join(map(cell, values)) + ' |')
    lines += ['', '## Recorded stage timings', '',
              'Stage-body times exclude initialization before the stage recorder starts. '
              'Process times include imports, cache setup, and source snapshots. RSS is the worker high-water mark; '
              'it does not sum descendant memory.', '',
              '| Case | Phase | Verdict | Body s | Process s | RSS MiB |', '|---|---|---|---:|---:|---:|']
    processes = {}
    attempts = []
    for path in sorted(folder.glob('invocation-*.json')):
        invocation = read(path)
        for phase in invocation.get('phases', []):
            processes[(phase.get('case'), phase.get('name'))] = phase
            attempts.append((path.name, phase))
    for row in rows:
        for phase in PHASES:
            stage = row.get('stages', {}).get(phase, {})
            process = processes.get((row.get('case'), phase), {})
            rss = stage.get('resources', {}).get('max_rss_bytes')
            lines.append('| ' + ' | '.join(map(cell, [row.get('case'), phase, gate(stage),
                number(stage.get('wall_time_s')), number(process.get('wall_time_s')),
                number(rss/1024**2) if isinstance(rss, (int, float)) else 'unavailable'])) + ' |')
    for row in rows:
        name = row.get('case', 'unknown')
        stages = row.get('stages', {})
        generation = stages.get('generation', {})
        audit = read(folder / 'evidence' / name / 'generation.audit.json')
        lines += ['', f'## {name}', '', f"Ship: {row.get('ship')}; seed: {row.get('seed')}. "
                  f"Targets: {generation.get('loads', 'unavailable')}; DG: {generation.get('dg', 'unavailable')}; "
                  f"reefers: {generation.get('reefers', 'unavailable')}.", '']
        if audit:
            lines.append(f"Generation: {audit.get('attempts', 'unavailable')} measured draws; "
                         f"rejections `{json.dumps(audit.get('rejections', {}), sort_keys=True)}`; "
                         f"background checks {audit.get('background_checks', 'unavailable')}; "
                         f"C2 checks {audit.get('c2_checks', 'unavailable')}.")
        if generation.get('blocking_ratio') is not None:
            lines.append(f"Measured blocking ratio: {number(generation['blocking_ratio'], 4)}.")
        if generation.get('simulation_statistics'):
            lines.append(f"Measured simulator statistics: `{json.dumps(generation['simulation_statistics'], sort_keys=True)}`.")
        count = stages.get('check', {}).get('count')
        if count:
            total = sum(count.get(k, 0) for k in ('loads', 'discharges', 'relocations', 'restow_lifts', 'hatch_cover_lifts'))
            lines.append(f"Checker move counts: {total} current lifts; {count.get('relocations', 'unavailable')} rehandles; "
                         f"{count.get('restow_lifts', 'unavailable')} restow lifts; {count.get('hatch_cover_lifts', 'unavailable')} hatch-cover lifts; "
                         f"weighted nonproductive objective {count.get('weighted_nonproductive', 'unavailable')}.")
        else:
            lines.append('Checker move counts: unavailable.')
        progress = row.get('progress') or {}
        if progress:
            lines.append(f"Last solver checkpoint: `{json.dumps(progress, sort_keys=True)}`.")
        for filename, prefix in (row.get('prefixes') or {}).items():
            metrics = prefix.get('metrics') or {}
            lines.append(f"Prefix `{filename}`: status {prefix.get('status', 'unavailable')}; "
                         f"{prefix.get('lifts', 'unavailable')} recorded lifts; "
                         f"{metrics.get('partial_loaded', 'unavailable')}/{metrics.get('required_loads', 'unavailable')} required boxes aboard; "
                         f"{metrics.get('oracle_approved_actions', 'unavailable')} oracle-approved actions. "
                         f"Artifact: `{prefix.get('path', 'unavailable')}`. Departure approval is not implied.")
        mask = stages.get('mask', {})
        if mask:
            lines.append(f"Token replay: {mask.get('actions_completed', 'unavailable')}/{mask.get('actions_expected', 'unavailable')} actions; "
                         f"decoder status {mask.get('status', 'unavailable')}; masked action `{json.dumps(mask.get('masked_action'))}`.")
        casepath = folder / 'evidence' / name
        diagnostics = {phase: read(casepath / f'prefix-{phase}.json') for phase in ('check', 'mask')}
        if any(diagnostics.values()):
            lines += ['', 'Partial-prefix diagnostics (departure remains **UNAPPROVED**):', '',
                '| Diagnostic | Verdict | Body s | Worker RSS MiB |', '|---|---|---:|---:|']
            for phase, diagnostic in diagnostics.items():
                rss = diagnostic.get('resources', {}).get('max_rss_bytes')
                verdict = ('PASS for prefix only' if diagnostic.get('prefix_mask_passed') else
                           'PASS on tested prefix state' if diagnostic.get('prefix_checker_passed') is True else
                           'FAIL' if diagnostic.get('prefix_checker_passed') is False else
                           diagnostic.get('status', 'not run'))
                lines.append('| ' + ' | '.join(map(cell, [phase, verdict,
                    number(diagnostic.get('wall_time_s')), number(rss/1024**2) if isinstance(rss, (int, float)) else 'unavailable'])) + ' |')
            changed = sorted({path for d in diagnostics.values() for path in d.get('changed_sources', [])})
            if changed:
                lines += ['', 'Diagnostic source changes: ' + ', '.join(f'`{path}`' for path in changed) + '.']
            checkdiag = diagnostics['check']
            hard = checkdiag.get('first_hard_violation') or {}
            if hard:
                lines += ['', f"Prefix checker first hard failure: `{hard.get('rule_id')}` — {hard.get('message', '')}. "
                          f"Hard violations: {checkdiag.get('hard_violations', 'unavailable')}."]
            prefixcount = (checkdiag.get('checker_report') or {}).get('count')
            if prefixcount:
                total = sum(prefixcount.get(k, 0) for k in ('loads', 'discharges', 'relocations', 'restow_lifts', 'hatch_cover_lifts'))
                lines.append(f"Prefix checker counts: {total} current lifts; {prefixcount.get('relocations', 'unavailable')} rehandles; "
                    f"{prefixcount.get('restow_lifts', 'unavailable')} restow lifts; "
                    f"{prefixcount.get('hatch_cover_lifts', 'unavailable')} hatch-cover lifts. These describe the prefix, not a completed plan.")
            else:
                lines.append('Prefix checker move counts: unavailable.')
            maskdiag = diagnostics['mask']
            details = maskdiag.get('mask') or {}
            if details:
                lines.append(f"Prefix mask replay: {details.get('actions_completed', 'unavailable')}/{details.get('actions_expected', 'unavailable')} "
                    f"actions, {details.get('tokens', 'unavailable')} tokens; decoded actions equal saved actions: {details.get('actions_equal', 'unavailable')}; "
                    f"plan complete: {details.get('plan_complete', 'unavailable')}; masked action: `{json.dumps(details.get('masked_action'))}`.")
            for phase, diagnostic in diagnostics.items():
                if diagnostic.get('error'):
                    lines.append(f"Prefix {phase} diagnostic error: {diagnostic['error']}.")
            lines.append('Prefix checks do not change the candidate status or approve departure; no completion tokens are emitted for prefix replay.')
        if stages.get('generation', {}).get('passed') and not stages.get('plan', {}).get('passed'):
            lines += ['', 'No complete solver candidate was produced. Ballast finishing and full-complete-plan checker/mask validation were not run; '
                      'any diagnostic above concerns the saved incomplete prefix only.']
        changed = sorted({path for stage in stages.values() for path in stage.get('changed_sources', [])})
        lines.append('Source changes observed during stages: ' + (', '.join(f'`{p}`' for p in changed) if changed else 'none recorded') + '.')
        lines.append('Per-stage SHA256 identities and source-byte snapshots remain in `evidence/' + name + '/`.')
    lines += ['', '## Assumptions and approximations', '',
        '- ASSUMPTION: synthetic bookings use TEU slack .25 and cargo-mass cap .55; these condition demand without changing checker thresholds.',
        '- APPROXIMATION: random-yard messiness uses requested blocking ratio .7; the third case uses simulated-yard noise .7 as an independent generation remedy. Realized features are reported only when measured.',
        '- APPROXIMATION: solver budgets are divided among remaining cases, reserving time for finishing, checking, and token replay within the absolute deadline.',
        '- APPROXIMATION: numerical caches are mirrored once per owned run; later misses rebuild privately under ordinary cache validation.',
        '- Ballast V4 is enabled only for finalization. Generation, construction, full checker, and replay keep it off. C2 stays off; rule-freeze stays off.',
        '- Solver is the unchanged default scalable configuration except C2 off, with solver seed 0. No StowLM result is claimed.', '']
    if attempts:
        lines += ['## Process-attempt audit', '', '| Invocation | Case / phase | Process s | Timeout | Return code |', '|---|---|---:|---|---:|']
        for invocation, phase in attempts:
            lines.append('| ' + ' | '.join(map(cell, [invocation, f"{phase.get('case')} / {phase.get('name')}",
                number(phase.get('wall_time_s')), phase.get('timed_out', 'unfinished'), phase.get('returncode', 'unavailable')])) + ' |')
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=DEFAULT / 'summary.json')
    parser.add_argument('--output', type=Path, default=DEFAULT / 'summary.md')
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(DEFAULT):
        parser.error('Output must stay inside experiments/mvp/runs/showcase-medium')
    content = render(read(args.input), args.input.parent)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content)
    print(args.output)


if __name__ == '__main__':
    main()
