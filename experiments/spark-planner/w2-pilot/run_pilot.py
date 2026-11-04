"""One paid attempt per generated instance; all shipping uses run_planner."""
from decimal import Decimal
import argparse
import json
import os
from pathlib import Path
import time
import sys

sys.dont_write_bytecode = True
from generate_pilot import runtime
runtime()

from binding import digest, instance_digest

from llm.spark.client import SparkClient
from llm.spark.planner import SparkPlanner, _decode_line
from llm.stowlm.format import ActionCodec, END_PLAN
from llm.spark.planner_budget import allocation_budget, estimate_instance, guard_run
from llm.spark.spend import SpendCapExceeded
from llm.stowlm.format import load_tokenizer
from llm.stowlm.format_prompt import active_prompt_version
from stowage.io import load_instance
from stowage.planner import run_planner
from stowage.rules.catalogue import rule_freeze_v1
from stowage.solver.scalable import ScalableSolver
from stowage.solver.twostep import TwoStepPlanner

ROOT = Path(__file__).parent
# Rung floors. A 4096 cap truncated a 4-box tiny response (results-cap4096-probe.jsonl).
# A 16384 cap truncated three 1-4k-input tiny responses too (results-cap16384-probe.jsonl).
# A 65536 cap truncated small seed-110390000 (all reasoning); small uses the API maximum.
CAPS = {'tiny': 65536, 'small': 131072, 'medium': 131072, 'full': 131072}


def output_cap(instance, tokenizer, rung):
    # Action text is ~one line per required box move; price 4x that (restows,
    # blocker lifts, any terse reasoning) and never go below the rung floor.
    line = 'P000 0010182\n'
    encoded = tokenizer.encode(line, add_special_tokens=False)
    per_line = len(encoded.ids if hasattr(encoded, 'ids') else encoded)
    needed = per_line*(len(instance.load_list)+len(instance.discharge_list))
    cap = CAPS[rung]
    while cap < 4*needed+1024:
        cap *= 2
    return min(cap, 131072), needed


def syntax_score(instance, tokenizer, text):
    # Offline grammar check of every line, independent of the feasibility engine.
    codec = ActionCodec(tokenizer, [box.id for box in instance.containers])
    has_end = text.rstrip().endswith(END_PLAN)
    lines = (text.rstrip()[:-len(END_PLAN)] if has_end else text).splitlines(keepends=True)
    ok, first_bad, kinds, placed = 0, None, {}, set()
    for index, line in enumerate(lines):
        try:
            action = _decode_line(codec, line)
        except Exception:
            first_bad = index if first_bad is None else first_bad
            continue
        ok += 1
        kinds[type(action).__name__] = kinds.get(type(action).__name__, 0)+1
        if type(action).__name__ == 'Place':
            placed.add(action.box)
    loads = {getattr(b, 'box_id', b) for b in instance.load_list}
    return dict(has_end_marker=has_end, strict_end_marker=text.endswith(END_PLAN), lines=len(lines), parsed_lines=ok,
                first_parse_error_index=first_bad, action_kinds=kinds,
                load_boxes_placed=len(placed & loads), load_boxes_required=len(loads),
                response_chars=len(text))


def baseline(planner, instance):
    started = time.monotonic()
    try:
        result, report = run_planner(planner, instance, seed=0, time_limit_s=300, allow_repair=False)
    except Exception as exc:
        return dict(planner=planner.name, error_type=type(exc).__name__)
    return dict(planner=planner.name, status=result.status, shippable=result.shippable,
                checker_passed=bool(report and report.passed),
                moves=report.count.model_dump(mode='json') if report and report.count else None,
                wall_s=time.monotonic()-started)
LABEL = 'pilot, pre-freeze epoch, not a result'


class CapturedPlanner:
    """Replay an already-paid result through the fixed checking pipeline."""
    def __init__(self, planner, result):
        self.name, self.version = planner.name, planner.version
        self.sets_ballast, self.deterministic = planner.sets_ballast, False
        self.result = result

    def plan(self, instance, **kwargs):
        return self.result.model_copy(deep=True)


def append(row):
    with (ROOT/'results.jsonl').open('a') as f:
        f.write(json.dumps(row, sort_keys=True)+'\n')
        f.flush()
        os.fsync(f.fileno())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('instance_paths', nargs='+', type=Path)
    parser.add_argument('--compact-only', action='store_true',
                        help='D76 information ablation: no rules appendix (the w2 pilot rows used this prompt)')
    parser.add_argument('--reasoning-effort', choices=['minimal', 'low', 'medium', 'high', 'xhigh'],
                        help='provider reasoning.effort for the later A/B; omitted = provider default')
    args = parser.parse_args()
    smoke = json.loads((ROOT/'test-call.json').read_text())
    # Snapshot run: spend is always recorded in the repository's durable ledger.
    repo = Path('/Users/anda/Projects/meta-hackathon-stowage')
    client = SparkClient(max_retries=0, timeout=1500, ledger_path=repo/'experiments/spark-spend/calls.jsonl',
                         compute_path=repo/'docs/compute.md')
    # Explicit resume accounting: this job's earlier measured + held spend, by
    # its own reservation IDs (canonical totals; unsettled holds stay charged).
    job = [r for r in [json.loads(line) for line in client.ledger.path.read_text().splitlines()][smoke['ledger_lines_before']:]
           if r.get('purpose') in ('harness-test', 'muse-spark-zero-shot')]
    prior_job_usd = sum(client.ledger._totals(job), Decimal(0))
    tokenizer = load_tokenizer()
    captured = {}
    original_complete = client.complete

    def complete(prompt, **kwargs):
        captured.pop('text', None)
        captured.pop('body', None)
        # Paid-call boundary: bind the exact prompt bytes before the request goes out,
        # so an incomplete (still billed) attempt keeps its prompt identity too.
        captured['prompt'] = prompt.encode('utf-8')
        captured['max_output_tokens'] = kwargs.get('max_output_tokens')
        response = original_complete(prompt, **kwargs)
        captured['text'] = response.text
        return response
    client.complete = complete
    # Tee the provider's JSON body (usage details incl. reasoning tokens) for
    # diagnosis; the request still goes through the client and its ledger.
    import llm.spark.client as client_module
    original_urlopen = client_module.urllib.request.urlopen

    class Body:
        def __init__(self, raw, headers):
            self.raw, self.headers = raw, headers
        def read(self):
            return self.raw
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False

    def urlopen(request, timeout=None):
        with original_urlopen(request, timeout=timeout) as response:
            raw, headers = response.read(), response.headers
        captured['body'] = raw
        return Body(raw, headers)
    client_module.urllib.request.urlopen = urlopen
    previous = [json.loads(line) for line in (ROOT/'results.jsonl').read_text().splitlines()] if (ROOT/'results.jsonl').exists() else []
    completed = {r.get('instance_id') for r in previous}
    for path in args.instance_paths:
        with rule_freeze_v1(enabled=True):
            instance = load_instance(path)
            if instance.id in completed:
                continue
            rung = instance.size_class
            cap, needed_tokens = output_cap(instance, tokenizer, rung)
            row = dict(label=LABEL, instance_id=instance.id, instance_path=str(path),
                       instance_sha256=instance_digest(instance),
                       rung=rung, family=instance.family, rule_freeze_v1=True,
                       prompt_version=active_prompt_version(), max_output_tokens=cap,
                       samples=1, estimated_action_tokens=needed_tokens, temperature='provider default (planner omits temperature)',
                       minimum_actions_needed=len(instance.load_list)+len(instance.discharge_list),
                       baseline_note='Baselines run here on the same snapshot inputs, no repair.')
            estimate = estimate_instance(instance, tokenizer, max_output_tokens=cap, appendix=not args.compact_only)
            row['estimated_usd'] = str(estimate.cost_usd)
            row['estimated_input_tokens'] = estimate.input_tokens
            ledger_start = len(client.ledger.path.read_text().splitlines())
            started = time.monotonic()
            try:
                with allocation_budget(client.ledger, allocation_usd=Decimal('5'), prior_job_usd=prior_job_usd):
                    guard_run([estimate], client.ledger, allocation_usd=Decimal('5'))
                    planner = SparkPlanner(client, tokenizer=tokenizer, max_output_tokens=cap,
                                           allocation_usd=Decimal('5'), appendix=not args.compact_only,
                                           reasoning_effort=args.reasoning_effort)
                    raw = planner.plan(instance, seed=0, time_limit_s=1800)
            except SpendCapExceeded:
                row.update(attempted=False, skipped_reason='allocation_preflight', raw_plan_legal=None)
                append(row)
                continue
            raw_elapsed = time.monotonic()-started
            text = captured.get('text')
            body = captured.get('body')
            prompt_bytes = captured.pop('prompt', None)
            appendix = getattr(planner, 'prompt_appendix', None)
            if appendix is not None:
                row['prompt_appendix'] = str(appendix)  # absent = compact-only prompt
            row['prompt_sha256'] = digest(prompt_bytes) if prompt_bytes is not None else None
            row['prompt_binding'] = 'recorded at paid-call boundary' if prompt_bytes is not None else 'no request sent'
            row['request_max_output_tokens'] = captured.pop('max_output_tokens', None)
            if prompt_bytes is not None:
                (ROOT/'prompts').mkdir(exist_ok=True)
                (ROOT/'prompts'/f'{instance.id}.txt').write_bytes(prompt_bytes)
            row['response_sha256'] = digest(text) if text is not None else None
            if body is not None:
                (ROOT/'raw').mkdir(exist_ok=True)
                (ROOT/'raw'/f'{instance.id}.json').write_bytes(body)
                try:
                    usage = json.loads(body).get('usage', {})
                    row['output_tokens_details'] = usage.get('output_tokens_details')
                except ValueError:
                    pass
            if text is not None:
                (ROOT/'responses').mkdir(exist_ok=True)
                (ROOT/'responses'/f'{instance.id}.txt').write_bytes(text.encode('utf-8'))
            row['response_saved'] = text is not None
            row['syntax'] = syntax_score(instance, tokenizer, text) if text is not None else None
            raw_metrics = dict(raw.metrics)
            row.update(attempted=True, raw_status=raw.status, raw_metrics=raw_metrics,
                       raw_plan_legal=bool(raw_metrics.get('raw_plan_legal', 0)),
                       first_illegal_action_index=raw_metrics.get('first_illegal_action_index'),
                       first_parse_error_index=raw_metrics.get('first_parse_error_index'),
                       actions_returned=raw_metrics.get('response_action_lines'),
                       legal_actions_retained=raw_metrics.get('legal_action_count', 0),
                       planner_latency_s=raw_elapsed)
            # Check own plan, then attempt repair on the same captured response.
            own, own_report = run_planner(CapturedPlanner(planner, raw), instance,
                                          seed=0, time_limit_s=60, allow_repair=False)
            shipped, shipped_report = (own, own_report) if own.shippable else run_planner(
                CapturedPlanner(planner, raw), instance, seed=0, time_limit_s=60, allow_repair=True)
            records = [json.loads(line) for line in client.ledger.path.read_text().splitlines()][ledger_start:]
            # This client uses one attempt; records are copied for accounting audit.
            own_records = [r for r in records if r.get('purpose') == planner.name]
            actual, held = client.ledger._totals(own_records)
            settlements = [r for r in own_records if r.get('phase') == 'settlement']
            row.update(input_tokens=sum(r.get('input_tokens') or 0 for r in settlements),
                       output_tokens=sum(r.get('output_tokens') or 0 for r in settlements),
                       cached_tokens=sum(r.get('cached_tokens') or 0 for r in settlements),
                       cost_usd=str(actual), unknown_hold_usd=str(held),
                       ledger_records=own_records,
                       own_run_planner_status=own.status,
                       own_checker_passed=bool(own_report and own_report.passed),
                       own_shippable=own.shippable,
                       own_checker_rules=[v.rule_id for v in own_report.violations] if own_report else [],
                       run_planner_status=shipped.status,
                       checker_verdict=bool(shipped_report and shipped_report.passed),
                       shipped_after_repair=shipped.shippable,
                       fallback_needed=bool(own.metrics.get('fallback_needed')),
                       fallback_attempted=shipped.repaired,
                       repair_status=shipped.metrics.get('repair_status'),
                       own_moves=own_report.count.model_dump(mode='json') if own_report and own_report.count else None,
                       final_moves=shipped_report.count.model_dump(mode='json') if shipped_report and shipped_report.count else None,
                       total_latency_s=time.monotonic()-started)
            # The engine has no legal action at all when its global screen is unavailable.
            row['environment_blocked'] = str(raw_metrics.get('mask_global_screen', '')).startswith('unavailable')
            row['baseline_comparison'] = None
            row['baseline_note'] = ('Not run: scalable/two-step dead-end at depth 0 in this epoch '
                                    '(production linear policy epoch stale; plan task 4.11).')
            artifact = ROOT/'checked'/f'{instance.id}.json'
            artifact.parent.mkdir(exist_ok=True)
            artifact.write_text(json.dumps({'label':LABEL,'raw':raw.model_dump(mode='json'),
                'own':own.model_dump(mode='json'), 'own_report':own_report.model_dump(mode='json') if own_report else None,
                'final':shipped.model_dump(mode='json'), 'final_report':shipped_report.model_dump(mode='json') if shipped_report else None}, indent=2)+'\n')
            append(row)
            print(json.dumps({k:row[k] for k in ('instance_id','rung','cost_usd','raw_plan_legal','own_checker_passed','shipped_after_repair')}), flush=True)


if __name__ == '__main__':
    main()
