"""Aggregate pilot rows, probes and the project ledger into summary.json (no HTTP).

Spark rows report Spark's own plans only. Legality/checker counts are over measurable
(not environment-blocked) rows, and null when no attempted row is measurable. Solver
repair is not a Spark metric: only the plain fallback-needed count is kept.
Job cost uses the canonical ledger totals over this job's reservations and their
settlements, so a reservation that never settled stays charged as a hold.
"""
from decimal import Decimal
from pathlib import Path
import json
import statistics

from llm.spark.spend import SpendLedger

ROOT = Path(__file__).parent
LEDGER = Path('/Users/anda/Projects/meta-hackathon-stowage/experiments/spark-spend/calls.jsonl')
TEST_SIZES = {'tiny': 75, 'small': 75, 'medium': 75, 'full': 75}  # experiments/splits/v2/manifest.json test counts


JOB_PURPOSES = ('harness-test', 'muse-spark-zero-shot')
FALLBACK_LABEL = 'fallback (solver repair) needed — measures the solver, not Spark'
PITCH_NOW = ("In a 6-instance pilot, Muse Spark wrote complete, well-formed plans for 5; "
             "whether they're legal is pending.")


def job_records(ledger, lines_before):
    """This job's reservations (by purpose, after the pre-test ledger lines) and their settlements."""
    job = ledger[lines_before:]
    ids = {r['reservation_id'] for r in job if r.get('phase') == 'reservation' and r.get('purpose') in JOB_PURPOSES}
    return [r for r in job if r.get('phase') in ('reservation', 'settlement') and r.get('reservation_id') in ids]


def job_totals(records):
    """(measured, held): unsettled reservations stay held at their full reserved cost."""
    actual, held = SpendLedger._totals(records)
    reservations = {r['reservation_id'] for r in records if r['phase'] == 'reservation'}
    settled = {r['reservation_id'] for r in records if r['phase'] == 'settlement'}
    return actual, held, sorted(reservations - settled)


def measurable_count(rows, field):
    measurable = [r for r in rows if not r.get('environment_blocked')]
    return (sum(bool(r.get(field)) for r in measurable) if measurable else None), len(measurable)


def rows(name):
    path = ROOT/name
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def main():
    smoke = json.loads((ROOT/'test-call.json').read_text())
    ledger = [json.loads(line) for line in LEDGER.read_text().splitlines()]
    spent, held, unsettled = job_totals(job_records(ledger, smoke['ledger_lines_before']))
    main_rows = rows('results.jsonl')
    probes = rows('results-cap4096-probe.jsonl') + rows('results-cap16384-probe.jsonl')
    per_rung = {}
    for rung in ('tiny', 'small', 'medium', 'full'):
        attempted = [r for r in main_rows if r['rung'] == rung and r.get('attempted')]
        done = [r for r in attempted if r['raw_metrics'].get('incomplete_reason') is None and r.get('syntax')]
        costs = [Decimal(r['cost_usd']) for r in attempted]
        syntax_ok = [r for r in done if r['syntax']['has_end_marker'] and r['syntax']['first_parse_error_index'] is None
                     and r['syntax']['load_boxes_placed'] == r['syntax']['load_boxes_required']]
        per_rung[rung] = dict(
            attempted=len(attempted), completed_responses=len(done),
            truncated=sum(r['raw_metrics'].get('incomplete_reason') == 'max_output_tokens' for r in attempted),
            syntax_clean_all_loads_placed=len(syntax_ok),
            raw_plan_legal=measurable_count(attempted, 'raw_plan_legal')[0],
            own_checker_passed=measurable_count(attempted, 'own_checker_passed')[0],
            measurable=measurable_count(attempted, 'raw_plan_legal')[1],
            environment_blocked=sum(bool(r.get('environment_blocked')) for r in attempted),
            fallback_needed=sum(bool(r.get('fallback_needed')) for r in attempted),
            fallback_needed_label=FALLBACK_LABEL,
            mean_usd=str(sum(costs, Decimal(0))/len(costs)) if costs else None,
            output_tokens=[r['output_tokens'] for r in attempted],
            reasoning_tokens=[(r.get('output_tokens_details') or {}).get('reasoning_tokens') for r in attempted],
            input_tokens=[r['input_tokens'] for r in attempted],
            actions_needed=[r['minimum_actions_needed'] for r in attempted],
            caps=sorted({r['max_output_tokens'] for r in attempted}),
            latency_s_median=statistics.median([r['planner_latency_s'] for r in attempted]) if attempted else None)
    projection = {}
    for rung, data in per_rung.items():
        if data['mean_usd'] is not None:
            one = Decimal(data['mean_usd'])*TEST_SIZES[rung]
            projection[rung] = dict(one_sample_usd=str(one.quantize(Decimal('0.01'))),
                                    four_samples_usd=str((4*one).quantize(Decimal('0.01'))))
    bindings = sorted({r.get('prompt_binding') or 'missing' for r in main_rows})
    summary = dict(label='pilot, pre-freeze epoch, not a result', job_measured_spend_usd=str(spent),
                   job_unknown_hold_usd=str(held), job_committed_usd=str(spent+held),
                   unsettled_reservations=unsettled, reporting='Spark own plans only; prompt compact-only '
                   '(no D76 rules appendix)', response_bindings=bindings, pitch_wording_now=PITCH_NOW,
                   probe_spend_usd=str(sum((Decimal(r['cost_usd']) for r in probes), Decimal(0))),
                   per_rung=per_rung, projection_6_7=projection, test_sizes=TEST_SIZES)
    (ROOT/'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True)+'\n')
    print(json.dumps(summary, indent=1, sort_keys=True))


if __name__ == '__main__':
    main()
