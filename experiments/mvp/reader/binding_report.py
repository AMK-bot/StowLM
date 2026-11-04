"""Produce the measured D199 binding report, preserving every failed case."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUN = HERE/'runs/binding'


def main():
    cohort = json.loads((HERE/'binding-cases.json').read_text())
    pairs = []
    for case in cohort['cases']:
        plan = json.loads((RUN/'plans'/f"{case['case_id']}.json").read_text())
        audit = json.loads((RUN/'audits'/f"{case['case_id']}.json").read_text())
        if hashlib.sha256((RUN/'plans'/f"{case['case_id']}.json").read_bytes()).hexdigest() != audit['plan_record_sha256']:
            raise ValueError('Plan record changed after fresh audit')
        pairs.append((plan, audit))
    latest = {}
    for line in (ROOT/'experiments/spark-spend/calls.jsonl').read_text().splitlines():
        row = json.loads(line)
        if row.get('purpose') == 'reader-d199-binding-parser':
            latest[row['reservation_id']] = row
    by_request = {row.get('request_id'): row for row in latest.values()}
    for plan, _ in pairs:
        row = by_request[plan['parser']['request_id']]
        if row.get('status') != 'completed' or Decimal(row['cost_usd']) != Decimal(plan['parser']['cost_usd']):
            raise ValueError('Parser response does not reconcile to shared ledger')
    spent = sum((Decimal(r['cost_usd']) for r in latest.values()), Decimal(0))
    if spent > 1:
        raise ValueError('Binding parser allocation exceeded')
    passed = sum(a.get('passed', False) for _, a in pairs)
    binding = sum(a.get('binding', False) for _, a in pairs)
    grouped = defaultdict(list)
    for pair in pairs:
        grouped[pair[0]['type']].append(pair)
    lines = ['# Frontier 0.9 under a sentence — D199 route 3', '',
        f'Completed {len(pairs)} preregistered constrained runs on validation maps only, five per constraint type. '
        f'**{passed}/20 freshly audited passes; {binding}/20 real binding replans.** '
        'This cohort was deliberately selected by inspecting stored baseline violations and necessary capacity screens. '
        'It is a binding demonstration, not a blind success-rate estimate.', '',
        f'Muse Spark parsed all {sum(p.get("parse_exact", False) for p, _ in pairs)}/20 literal sealed sentences exactly '
        f'in their actual ship contexts before planning began. **Spend: ${spent} / $1**, {len(latest)} calls, '
        'all reconciled to the authoritative shared ledger and its managed `docs/compute.md` row. '
        'No training paraphrases, reader tuning, real company data, `src/` edits, git commands, or repair were used.', '',
        'The baseline is the stored best passing Frontier 0.9 plan, freshly checked twice here; no new unconstrained '
        'planner runs were spent. That comparator already includes a port-blocked start; the paired rows '
        'show observed costs with the hard instruction added. Each case makes at most one constrained portfolio call. Search uses the baseline’s '
        'stored search allocation, a 120-second constructor cap, the same V4 finisher with 300 seconds per candidate, '
        'and the full checker. There are two map workers, one native CP/finisher worker each; load threshold 128 (≥36). '
        'Per-case claims prevent automatic retries of failed or interrupted planner attempts.', '',
        'The reader driver binds hybrid’s existing `plan_and_check` function code to private dependency globals; '
        'it imports and reuses Frontier’s constructors, master, ALNS, sequencing, finisher, and checker. '
        'Every admitted start and search candidate is checked against the instruction. Constructor masks and the '
        'copied master static table intersect the original legal domains; every finished result is re-audited. '
        'A separate fresh process then repeats the full checker, physical move replay, completion and instruction-filtered mask replay.', '',
        '**Shared-source change:** another executive updated hybrid’s provenance telemetry during this run. '
        'The observed boundary is old telemetry through `max_tiers-01`, new telemetry from `max_tiers-02`. '
        'Independent read-only review found the constructor/master/start/search/ranking/finishing policy unchanged '
        'for our empty-extra-start calls. New score-witness bookkeeping and final contribution replay preserve '
        'the existing constrained score/gates but can change wall-clock-bounded iteration counts. This is not a '
        'single byte-frozen implementation cohort. The new contribution dependency was not in the original '
        'preregistration; old/new hashes, the review, and observed source snapshots are archived in '
        '`runs/binding/upstream-change-review.json` and `runs/binding/source-observed/`. '
        'No parser, compiler, constrained portfolio, or Frontier solver changes were made in response to outcomes; '
        'binding claims rest on the fresh audits.', '',
        '**Cohort-screen limitation:** the original global capacity screen missed per-box DG exclusions. '
        'Post-run geometry diagnostics prove `dg_keep_away-02`, `-03`, and `-05` leave a required 40-foot '
        'class-8 box with zero allowed physical paired footprints. These are infeasible requests on those cargo '
        'slices, not evidence of planner quality, and cannot count as binding successes. '
        '`runs/binding/cohort-diagnostics.json` records every examined footprint and blocker. '
        'The future selector now checks each actual box and refuses overwriting the frozen cohort. '
        'The original 20 cases were neither replaced nor rerun; four regression tests cover this correction.', '',
        'The parser interface is `parse(sentence, context) -> (validated_answer, evidence)`. '
        'The public entry point is `instruction_planner.plan_sentence(instance, sentence, parser=...)`. '
        'Muse Spark supplies that answer today; a Glimmer reader can replace it without changing the compiler or planner. '
        'The compiler sees only the parsed structure, never an expected label. Refusals do not execute.', '',
        'OnePortPerHatch derives a strict cover→POD mapping from the instance using physical-cover selectors on '
        'Frontier’s unchanged static master. Every footprint cell, hold and deck, obeys the mapping; retained cargo '
        'fixes its covers. A static packing witness is not a checker certificate. A mapping mismatch alone does not '
        'make a baseline violate the sentence: binding is judged by actual mixed discharge ports on a physical cover. '
        'Each case uses one instance-derived allocation; no mapping sweep or global-optimality claim is made. '
        'Any observed cost increase is not a proven minimum penalty of the sentence.', '',
        'A **binding** result requires a freshly passing baseline that intrinsically violates the sentence, a changed '
        'container-move sequence, and a newly passing constrained plan with an independent successful audit (D166/D170). '
        'Ballast metadata changes alone do not count. Failures and unchanged/nonbinding cases remain below.', '',
        '| Case / validation map | Sentence | Spark constraint | Before moves | After moves | Pass | Fresh audit | Binding | Wall s |',
        '|---|---|---|---:|---:|---|---|---|---:|']
    for plan, audit in pairs:
        sentence = plan['sentence'].replace('|', '\\|').replace('\n', ' ')
        constraint = json.dumps(plan['parsed'].get('constraint', plan['parsed'].get('refusal')), separators=(',', ':'))
        before = audit.get('unconstrained_moves', plan.get('unconstrained_moves', plan.get('baseline_weighted')))
        after = audit.get('constrained_moves', plan.get('constrained_moves'))
        wall = plan.get('parse_wall_s', 0)+plan.get('wall_s', 0)+audit.get('wall_s', 0)
        lines.append(f"| {plan['case_id']} / `{plan['instance_id']}` | {sentence} | `{constraint}` | {before} | {after if after is not None else '—'} | {'yes' if plan.get('passed') else 'no'} | {'PASS' if audit.get('passed') else 'FAIL'} | {'yes' if audit.get('binding') else 'no'} | {wall:.1f} |")
    lines += ['', 'Wall time includes parsing, software allocation, planning and the fresh audit; runs share a busy '
              'machine, so these are observations rather than matched speed comparisons.', '', '## OnePortPerHatch move effect', '']
    hatch = grouped['one_port_per_hatch']
    mutual = [(p,a) for p,a in hatch if a.get('passed') and a.get('unconstrained_passed')]
    lines.append(f'{len(mutual)}/5 hatch cases passed the independent audit; {sum(a.get("binding", False) for _,a in hatch)}/5 meet the binding criterion.')
    lines.append('')
    for plan, audit in hatch:
        if audit.get('passed'):
            before, after = audit['unconstrained_moves'], audit['constrained_moves']
            lines.append(f"- `{plan['instance_id']}`: {before:g} → {after:g} weighted moves ({after-before:+g}); "
                         f"{audit['changed_final_placements']} final container placements changed; "
                         f"later restows {audit['restows_later']}, later hatch lifts {audit['hatches_later']}.")
        else:
            lines.append(f"- `{plan['instance_id']}`: no passing constrained plan; {audit.get('failure', audit.get('error', 'audit failed'))}.")
    if mutual:
        before = sum(a['unconstrained_moves'] for _,a in mutual)
        after = sum(a['constrained_moves'] for _,a in mutual)
        lines.append(f'\nOn these {len(mutual)} mutually passing cases, total moves are {before:g} → {after:g} '
                     f'({100*(after/before-1):+.2f}%; means {before/len(mutual):.2f} → {after/len(mutual):.2f}). '
                     'The strict port rule reaches the checked frontier plan through a sentence Spark read. '
                     'This establishes the language-to-constraint contribution on passing instructed plans; '
                     'it does not attribute software’s allocation or search skill to the LLM, or establish a population saving.')
    else:
        lines.append('\nNo checked OnePortPerHatch contribution is established by this cohort.')
    lines.append('\nThe failed hatch case `one_port_per_hatch-04` obtained an OPTIMAL static allocation, '
                 'but both constructors reached dead ends and the deck-4 master hit its time limit after cuts; '
                 'lower tier attempts were infeasible. No admissible start emerged. This is a portfolio failure '
                 'under the one chosen allocation, not a proof that the sentence is globally infeasible. '
                 'No alternative allocation or extra replan was tried.')
    lines += ['', '## Case audit details', '']
    for plan, audit in pairs:
        errors = audit.get('failure', audit.get('error', plan.get('error')))
        lines.append(f"- **{plan['case_id']}**: baseline violations {len(audit.get('unconstrained_violations', []))}; "
                     f"changed final placements {audit.get('changed_final_placements', '—')}; "
                     f"mask {audit.get('mask_legal', 'not run')}, complete {audit.get('mask_complete', 'not run')}, "
                     f"checker {audit.get('checker_passed', 'not run')}, all loaded {audit.get('all_loaded', 'not run')}. "
                     +(f'Failure: {errors}' if errors else 'No instruction/allocation violations.'))
    lines += ['', 'Original execution commands, from repository root (do not repeat the 20-case campaign). '
              'The source guard intentionally refuses a new run after upstream source drift; the final command '
              'regenerates this report entirely offline from completed artifacts:', '', '```sh',
              'TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python -m experiments.mvp.reader.run_binding prepare',
              'TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 STOWAGE_IN_PORT_WORKERS=1 UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python -m experiments.mvp.reader.run_binding run --load-limit 128',
              'UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python -m experiments.mvp.reader.binding_report', '```', '',
              'Evidence: `binding-cases.json`, `runs/binding/preregister.json`, per-case parser/plan/audit JSON, '
              '`runs/binding/spend.json`, `runs/binding/verification.json`, `binding-tests.log`, '
              '`binding-pipeline-tests.log`, `binding-interface-tests.log`, `binding-cohort-tests.log`, '
              '`binding-final-tests.log`, and `binding-test-collection.log` '
              '(59 tests pass in the final complete reader suite, including concurrent GPU-harness unit tests). '
              'Literal sealed sentences were used for this instructed '
              'planning demonstration only; no reader training or hyperparameter/prompt selection used them.']
    (HERE/'BINDING.md').write_text('\n'.join(lines)+'\n')
    spend = {'time_utc': datetime.now(timezone.utc).isoformat(), 'spent_usd': str(spent),
             'cap_usd': '1', 'records': list(latest.values()), 'reconciled_response_count': len(pairs)}
    (RUN/'spend.json').write_text(json.dumps(spend, indent=2)+'\n')
    print(json.dumps({'runs':len(pairs), 'passed':passed, 'binding':binding, 'spend_usd':str(spent)}, indent=2))


if __name__ == '__main__':
    main()
