"""Re-score saved pilot responses offline: no HTTP, no spend.

Use once the production linear policy epoch is requalified (plan task 4.11).
A stub client returns each saved response text, so SparkPlanner's own replay
and stowage.planner.run_planner (ballast finisher, full checker, repair) run
unchanged. Writes replay.jsonl beside results.jsonl.

Replay only scores saved responses. It never re-asks Spark and never swaps
prompts: before a saved response is used, its generation identity is checked
(binding.py): instance ID and sha256, prompt version, prompt appendix, output
cap, the exact prompt bytes (recorded hash, saved prompt file, and the prompt
SparkPlanner hands the stub client), and the response file's sha256. Any
mismatch refuses the row (replayed=false + reason); nothing is scored.

The pilot's responses were generated with the compact-only prompt
(stowlm-prompt@3.0, no rules appendix), and their prompt bytes were not saved
(prompt_binding "unrecorded (pre-fix pilot)"). They are refused unless run with
--accept-unbound-prompt, which labels rows binding="instance+response only".
The 6.7 run will use the rules-appendix prompt (decision D76), so these pilot
responses are a compact-only sample, not a 6.7 result.

The scoring epoch (time, git HEAD, dirty tree) is recorded separately from the
generation identity. Importing this module has no side effects; the cache
runtime and the stowage stack load in main().
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
import argparse
import json
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from binding import (BindingMismatch, FULL_LABEL, PRE_FIX_PROMPT_BINDING, UNBOUND_LABEL,  # noqa: E402
                     UNBOUND_PROMPT_REASON, digest, instance_digest)
import blocked_metrics  # noqa: E402

ROOT = Path(__file__).parent
GENERATION_FIELDS = ('instance_id', 'instance_sha256', 'prompt_version', 'prompt_appendix', 'max_output_tokens',
                     'prompt_sha256', 'prompt_binding', 'response_sha256', 'rule_freeze_v1')


class SavedClient:
    """Stands in for SparkClient. Returns one saved response, only for the bound prompt.

    The ledger is a throwaway file, never the project's.
    """
    def __init__(self, text: str, usage: Any, *, expected_prompt_sha256: str | None,
                 saved_prompt: bytes | None, expected_max_output_tokens: int | None,
                 ledger: Any = None, make_response: Callable[[str, Any], Any] | None = None):
        self.text, self.usage, self.ledger = text, usage, ledger
        self.expected_prompt_sha256, self.saved_prompt = expected_prompt_sha256, saved_prompt
        self.expected_max_output_tokens = expected_max_output_tokens
        self.make_response = make_response
        self.calls, self.mismatch, self.observed_prompt_sha256 = 0, None, None

    def complete(self, prompt, *, purpose, max_output_tokens=128):
        self.calls += 1
        received = prompt.encode('utf-8')
        observed = digest(received)
        self.observed_prompt_sha256 = observed
        if self.expected_prompt_sha256 is not None and observed != self.expected_prompt_sha256:
            self.mismatch = 'prompt received at replay differs from the recorded prompt_sha256'
        elif self.saved_prompt is not None and received != self.saved_prompt:
            self.mismatch = 'prompt received at replay differs from the saved prompt bytes'
        elif self.expected_max_output_tokens is not None and max_output_tokens != self.expected_max_output_tokens:
            self.mismatch = 'max_output_tokens at replay differs from the recorded cap'
        if self.mismatch:
            raise BindingMismatch(self.mismatch)
        if self.make_response is None:
            from llm.spark.client import SparkResponse
            return SparkResponse(self.text, self.usage, Decimal(0), 'replay')
        return self.make_response(self.text, self.usage)


@dataclass
class Deps:
    """The stowage stack, injected so binding checks are testable without physics."""
    load_instance: Callable[[Path], Any]
    build_prompt: Callable[[Any], str]
    prompt_version: Callable[[Any], str]
    make_planner: Callable[[Any, dict], Any]
    run_planner: Callable[..., tuple]
    make_usage: Callable[[dict], Any]
    make_ledger: Callable[[], Any] = lambda: None
    make_response: Callable[[str, Any], Any] | None = None
    freeze: Callable[[bool], Any] = lambda enabled: nullcontext()


def _refuse(row, reason, binding, epoch, **extra):
    return dict(instance_id=row.get('instance_id'), rung=row.get('rung'), replayed=False, reason=reason,
                binding=binding, generation_identity={k: row.get(k) for k in GENERATION_FIELDS},
                scoring_epoch=epoch, **extra)


def _moves(report):
    return report.count.model_dump(mode='json') if report is not None and getattr(report, 'count', None) else None


def replay_row(row: dict, deps: Deps, *, root: Path = ROOT, accept_unbound_prompt: bool = False,
               scoring_epoch: dict | None = None) -> dict:
    """Check one row's generation binding, then (only if it holds) score its saved response."""
    epoch = scoring_epoch or {}
    iid = row['instance_id']
    text_path = root/'responses'/f'{iid}.txt'
    if not text_path.exists():
        return _refuse(row, 'no saved response (incomplete or skipped)', None, epoch)
    response_bytes = text_path.read_bytes()
    if not row.get('response_sha256'):
        return _refuse(row, 'response hash not recorded', None, epoch)
    if digest(response_bytes) != row['response_sha256']:
        return _refuse(row, 'response file sha256 differs from the recorded response_sha256', None, epoch)
    prompt_path = root/'prompts'/f'{iid}.txt'
    saved_prompt = prompt_path.read_bytes() if prompt_path.exists() else None
    recorded_prompt = row.get('prompt_sha256')
    with deps.freeze(bool(row.get('rule_freeze_v1', True))):
        instance = deps.load_instance(root/row['instance_path'])
        if instance.id != iid:
            return _refuse(row, f'loaded instance id {instance.id!r} differs from the row', None, epoch)
        if instance_digest(instance) != row.get('instance_sha256'):
            return _refuse(row, 'instance sha256 differs from the recorded instance_sha256', None, epoch)
        if deps.prompt_version(instance) != row.get('prompt_version'):
            return _refuse(row, f'prompt version now {deps.prompt_version(instance)!r}, '
                                f'recorded {row.get("prompt_version")!r}', None, epoch)
        rebuilt = deps.build_prompt(instance).encode('utf-8')
        if recorded_prompt is None:
            if not accept_unbound_prompt:
                return _refuse(row, UNBOUND_PROMPT_REASON, None, epoch, reconstructed_prompt_sha256=digest(rebuilt))
            binding = UNBOUND_LABEL
        else:
            if saved_prompt is not None and digest(saved_prompt) != recorded_prompt:
                return _refuse(row, 'saved prompt file sha256 differs from the recorded prompt_sha256', None, epoch)
            if digest(rebuilt) != recorded_prompt:
                return _refuse(row, 'rebuilt prompt differs from the recorded prompt_sha256', None, epoch)
            binding = FULL_LABEL
        client = SavedClient(response_bytes.decode('utf-8'), deps.make_usage(row),
                             expected_prompt_sha256=recorded_prompt, saved_prompt=saved_prompt,
                             expected_max_output_tokens=row.get('max_output_tokens'),
                             ledger=deps.make_ledger(), make_response=deps.make_response)
        planner = deps.make_planner(client, row)
        if getattr(planner, 'prompt_appendix', None) != row.get('prompt_appendix'):
            return _refuse(row, 'prompt appendix at replay differs from the recorded generation', binding, epoch)
        own, own_report = deps.run_planner(planner, instance, seed=0, time_limit_s=360, allow_repair=False)
        if client.mismatch or client.calls == 0:
            return _refuse(row, client.mismatch or 'planner never requested the saved response', binding, epoch)
        repaired, repaired_report = (own, own_report) if own.shippable else deps.run_planner(
            planner, instance, seed=0, time_limit_s=360, allow_repair=True)
        if client.mismatch:
            return _refuse(row, client.mismatch, binding, epoch)
    metrics = own.metrics
    out = dict(instance_id=iid, rung=row.get('rung'), replayed=True, binding=binding,
               generation_identity={k: row.get(k) for k in GENERATION_FIELDS}, scoring_epoch=epoch,
               replay_prompt_sha256=client.observed_prompt_sha256,
               raw_plan_legal=bool(metrics.get('raw_plan_legal', 0)),
               first_illegal_action_index=metrics.get('first_illegal_action_index'),
               first_illegal_rule_id=metrics.get('first_illegal_rule_id'),
               own_checker_passed=bool(own_report and own_report.passed), own_shippable=own.shippable,
               own_moves=_moves(own_report),
               # Solver repair measures the solver, not Spark; kept apart from the own-plan fields.
               fallback_needed=bool(metrics.get('fallback_needed', not own.shippable)), solver_repair_shipped=repaired.shippable,
               solver_repair_attempted=bool(getattr(repaired, 'repaired', False)),
               solver_repair_moves=_moves(repaired_report))
    out['environment_blocked'] = str(metrics.get('mask_global_screen', '')).startswith('unavailable')
    return blocked_metrics.apply(out)


def scoring_epoch() -> dict:
    """Cheap scoring identity: wall time + git HEAD + dirty flag (physics identity not computed here)."""
    repo = ROOT.parents[2]
    def git(*args):
        try:
            return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True,
                                  timeout=30).stdout.strip()
        except Exception:
            return None
    status = git('status', '--porcelain', '--', 'src', 'params', 'data')
    return dict(scored_utc=datetime.now(timezone.utc).isoformat(), git_head=git('rev-parse', 'HEAD'),
                source_dirty=None if status is None else bool(status),
                note='scoring epoch only; generation identity is in generation_identity')


def real_deps() -> Deps:
    from generate_pilot import runtime
    runtime()
    from llm.spark.planner import SparkPlanner, build_prompt
    from llm.spark.spend import SpendLedger, Usage
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.format_prompt import prompt_version
    from stowage.io import load_instance
    from stowage.planner import run_planner
    from stowage.rules.catalogue import rule_freeze_v1
    tokenizer = load_tokenizer()

    def make_ledger():
        scratch = ROOT/'replay-scratch'
        scratch.mkdir(exist_ok=True)
        (scratch/'compute.md').write_text('')
        return SpendLedger(path=scratch/'calls.jsonl', compute_path=scratch/'compute.md')

    return Deps(load_instance=load_instance, build_prompt=build_prompt, prompt_version=prompt_version,
                make_planner=lambda client, row: SparkPlanner(client, tokenizer=tokenizer,
                                                              max_output_tokens=row['max_output_tokens'],
                                                              allocation_usd=Decimal('1'),
                                                              # Rows without prompt_appendix were paid with the compact-only prompt.
                                                              appendix=row.get('prompt_appendix') is not None),
                run_planner=run_planner, make_usage=lambda row: Usage(row['input_tokens'], row['output_tokens']),
                make_ledger=make_ledger, freeze=lambda enabled: rule_freeze_v1(enabled=enabled))


def main(argv=None, deps: Deps | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--results', type=Path, default=ROOT/'results.jsonl')
    parser.add_argument('--out', type=Path, default=ROOT/'replay.jsonl')
    parser.add_argument('--accept-unbound-prompt', action='store_true',
                        help='score rows whose prompt bytes were not recorded; rows are labelled '
                             f'binding="{UNBOUND_LABEL}"')
    args = parser.parse_args(argv)
    deps = deps or real_deps()
    epoch = scoring_epoch()
    rows = [json.loads(line) for line in args.results.read_text().splitlines() if line.strip()]
    with args.out.open('w') as out:
        for row in rows:
            result = replay_row(row, deps, root=args.results.parent,
                                accept_unbound_prompt=args.accept_unbound_prompt, scoring_epoch=epoch)
            out.write(json.dumps(result, sort_keys=True)+'\n')


if __name__ == '__main__':
    main()
