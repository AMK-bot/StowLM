"""Bounded DAgger-style recovery: learn continuations of model-visited prefixes.

DAgger asks an expert how to continue states visited by the learner, reducing
errors that accumulate away from the expert's usual trajectory (Ross et al.,
2011, https://proceedings.mlr.press/v15/ross11a.html). Here the existing solver
is that expert. A prefix is never relabelled as preserved if the solver drops it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import multiprocessing as mp
import os
import threading
from time import monotonic, process_time
from typing import Any

from llm.exit.selection import Candidate, Verification, verify_candidate


@dataclass
class RecoveryBudget:
    """Conservative one-core wall reservations against measured generation CPU.

    Supply *non-recovery* CPU seconds already measured in this generation, summed
    across CPU workers. Never use a projected generation duration as credit.
    APPROXIMATION: charge worker wall time as CPU, including startup and waiting;
    the 14%-of-base allowance stays below 15% of total generation CPU. A hard
    worker timeout bounds solver/checker calls that ignore cooperative deadlines.
    """
    base_cpu_s: float
    fraction: float = .14
    max_cases: int = 4
    spent_s: float = 0.
    cases: int = 0

    def __post_init__(self):
        if not math.isfinite(self.base_cpu_s) or self.base_cpu_s < 0:
            raise ValueError('base_cpu_s must be measured finite nonnegative CPU')
        if not 0 < self.fraction < .15 or self.max_cases < 0:
            raise ValueError('recovery fraction must be below .15; max_cases nonnegative')
        if not math.isfinite(self.spent_s) or self.spent_s < 0 or self.cases < 0:
            raise ValueError('recovery counters must be finite and nonnegative')

    def allowance(self, requested_s):
        if not math.isfinite(requested_s) or requested_s <= 0:
            raise ValueError('recovery time must be finite and positive')
        if self.cases >= self.max_cases:
            return 0.
        # Reserve 250 ms for worker termination and accounting. No credit means
        # no worker, so recovery can never bootstrap itself with guessed CPU.
        return max(0., min(requested_s, self.base_cpu_s*self.fraction-self.spent_s-.25))


@dataclass
class RecoveryResult:
    passed: bool = False
    plan: Any = None
    prefix_actions: tuple = ()
    details: dict[str, Any] = field(default_factory=dict)


def _actions(instance, plan):
    from stowage.actions import get_action_space
    return list(get_action_space().from_plan(instance, plan))


def _normalize_prefix(prefix):
    from stowage.actions import get_action_space
    space = get_action_space()
    return tuple(space.decode(a) if isinstance(a, dict) else a for a in prefix)


def _complete(instance, prefix, *, time_limit_s):
    from stowage.feasibility import FeasibilityConfig
    from stowage.solver.scalable import complete, ScalableConfig
    # APPROXIMATION: one bounded forward completion, no suffix relaxation or
    # expensive LNS. Failure simply contributes no training target.
    return complete(instance, prefix, seed=instance.seed, time_limit_s=time_limit_s,
                    config=ScalableConfig(max_neighborhoods=0, backtrack_limit=0,
                        feasibility_config=FeasibilityConfig(extra={'global_screen': 0})))


def last_good_prefix(instance, prefix_actions):
    """Back off to the latest completed action boundary with legal successors.

    This is a local mask notion of good, not a promise that an expert can finish.
    The final dead-end action and any partial action tokens are never targets.
    """
    from llm.exit.selection import decoder_factory
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.stowlm.mask import MaskConfig
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    prefix = _normalize_prefix(prefix_actions)
    engine = make_feasibility_engine(instance, get_action_space(),
        config=FeasibilityConfig(extra={'global_screen': 0}))
    codec = ActionCodec(load_tokenizer(), [box.id for box in instance.containers])
    decoder = decoder_factory()(instance, engine, codec, MaskConfig(), 1)[0]
    good = 0
    for index, action in enumerate(prefix):
        try:
            for token in codec.encode_action(action):
                if token not in decoder.allowed_token_ids():
                    return prefix[:good]
                decoder.advance(token)
            if decoder.status == 'running' and decoder.allowed_token_ids():
                good = index+1
            else:
                break
        except (ValueError, KeyError, RuntimeError):
            break
    return prefix[:good]


def _recover(instance, prefix_actions, *, time_limit_s, completer=None, verifier=None):
    """Synchronous core; production calls it only inside the bounded worker."""
    prefix = _normalize_prefix(prefix_actions)
    result = RecoveryResult(prefix_actions=prefix)
    started = monotonic()
    try:
        original_length = len(prefix)
        prefix = last_good_prefix(instance, prefix)
        result.prefix_actions = prefix
        result.details['prefix_backoff_actions'] = original_length-len(prefix)
        if not prefix:
            raise ValueError('empty prefix is ordinary gold, not recovery')
        candidate = (completer or _complete)(instance, prefix, time_limit_s=time_limit_s*.4)
        if candidate.status != 'ok' or candidate.plan is None:
            raise ValueError('expert completion failed')
        if _actions(instance, candidate.plan)[:len(prefix)] != list(prefix):
            raise ValueError('expert discarded requested prefix')
        remaining = time_limit_s-(monotonic()-started)
        if remaining <= 0:
            raise TimeoutError('recovery deadline exhausted before gates')
        checked = (verifier or (lambda i, c: verify_candidate(i, c, ballast_s=remaining)))(
            instance, Candidate(candidate.plan, 'recovery'))
        if not checked.passed or checked.plan is None:
            raise ValueError('recovery failed full checker/ballast/layered replay gates')
        actual = _actions(instance, checked.plan)
        if actual[:len(prefix)] != list(prefix) or len(actual) <= len(prefix):
            raise ValueError('finalized recovery must preserve prefix and add continuation')
        if monotonic()-started >= time_limit_s:
            raise TimeoutError('recovery gates exceeded deadline')
        result.passed, result.plan = True, checked.plan
        result.details.update(verification=checked.details, weighted_moves=checked.weighted_moves)
    except Exception as error:
        result.details['error'] = f'{type(error).__name__}: {error}'
    result.details['wall_s'] = monotonic()-started
    return result


def _worker(send, instance, prefix, time_limit_s):
    os.environ['STOWAGE_IN_PORT_WORKERS']='1'
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
                'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[key] = '1'
    # The outer watchdog owns the entire calculation. Match exact_deadline's
    # existing isolation pattern: inline the inner ballast watchdog in this
    # process only, preventing a warm grandchild surviving forced termination.
    from stowage.solver import ballast_deadline, exact_deadline
    ballast_deadline.run_with_deadline = exact_deadline._inline
    started = process_time()
    try:
        result = _recover(instance, prefix, time_limit_s=time_limit_s)
        if result.passed:
            # Include all replay/row preparation in the same hard CPU credit.
            # Parent admission verifies these bytes instead of replaying again.
            from dataclasses import asdict
            result.details['admitted_example']=asdict(build_recovery_example(instance,result))
        result.details['worker_cpu_s'] = process_time()-started
        send.send(result)
    except BaseException as error:
        send.send(RecoveryResult(details={'error': f'{type(error).__name__}: {error}'}))
    finally:
        send.close()


def recover_dead_end(instance, prefix_actions, *, budget: RecoveryBudget,
                     time_limit_s=15., input_path=None, guard=None) -> RecoveryResult:
    """Attempt an exact-prefix continuation in an owned, timed CPU subprocess.

    The caller supplies completed canonical ``decoder.actions`` only; a partial
    final action token is discarded. Call only for a dead-end/truncated sample.
    """
    from llm.exit.identity import identity_guard
    (guard or identity_guard()).require_allowed(instance, role='train', input_path=input_path)
    allowed = budget.allowance(time_limit_s)
    if allowed < .1:
        return RecoveryResult(details={'skipped': 'measured CPU budget or case cap exhausted'})
    prefix = _normalize_prefix(prefix_actions)
    if not prefix:
        return RecoveryResult(details={'skipped': 'no completed model prefix actions'})
    budget.cases += 1
    started = monotonic()
    context = mp.get_context('spawn')
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(send, instance, prefix, allowed), daemon=False)
    result = RecoveryResult(prefix_actions=prefix)
    try:
        process.start()  # Only this PID may be terminated below.
        send.close()
        # A readable pipe can contain only the beginning of a large report.
        # Bound the entire receive, not just waiting for its first bytes.
        delivered = threading.Event()
        delivery = {}
        def read_result():
            try:
                delivery['result'] = receive.recv()
            except (EOFError, OSError) as error:
                delivery['error'] = str(error)
            finally:
                delivered.set()
        threading.Thread(target=read_result, daemon=True).start()
        if delivered.wait(max(0., allowed-(monotonic()-started))) and 'result' in delivery:
            result = delivery['result']
        else:
            result.details['error'] = 'recovery worker timed out; no target admitted'
        process.join(timeout=max(0., allowed-(monotonic()-started)))
    except Exception as error:
        result = RecoveryResult(prefix_actions=prefix, details={'error': f'{type(error).__name__}: {error}'})
    finally:
        if process.pid is not None and process.is_alive():
            process.terminate()
            process.join(timeout=.1)
            if process.is_alive():
                process.kill()
                process.join()
        receive.close()
        send.close()
        elapsed = monotonic()-started
        budget.spent_s += elapsed
    if elapsed > allowed or process.exitcode != 0:
        result.passed, result.plan = False, None
        result.details['error'] = 'recovery exceeded admission deadline or worker failed'
    result.details.update(charged_cpu_upper_s=elapsed, budget_spent_s=budget.spent_s,
                          measured_base_cpu_s=budget.base_cpu_s, cases=budget.cases)
    return result


def build_recovery_example(instance, result: RecoveryResult, *, tokenizer=None,
                           source='exit.recovery', input_path=None, guard=None):
    """Admit a complete checked trajectory; mask prefix loss in runtime only.

    Preserve the original prompt_len and all replay/content hashes. Runtime uses
    exit_recovery.loss_start after stock admission, so every action (including
    prefix context) remains bound to a fresh root-state replay.
    """
    from llm.exit.identity import identity_guard
    from llm.exit.data import layered_replay
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.stowlm.sft_data import build_mvp_example, mvp_row_failure
    (guard or identity_guard()).require_allowed(instance, role='train', input_path=input_path)
    if not result.passed or result.plan is None:
        raise ValueError('unchecked recovery cannot become an SFT target')
    prefix = _normalize_prefix(result.prefix_actions)
    if not prefix or _actions(instance, result.plan)[:len(prefix)] != list(prefix):
        raise ValueError('recovery target lost model prefix')
    report = result.details.get('verification', {}).get('report')
    if report is None:
        raise ValueError('recovery lacks a bound full checker report')
    tokenizer = tokenizer or load_tokenizer()
    example = build_mvp_example(instance, result.plan, report, split='train', source=source, tokenizer=tokenizer)
    example.meta['exit_layered_replay'] = layered_replay(instance, example, tokenizer)
    from llm.exit.preferences import layer_identity
    example.meta['exit_layered_replay']['source_identity']=layer_identity()
    codec = ActionCodec(tokenizer, [box.id for box in instance.containers])
    tokens = [token for action in prefix for token in codec.encode_action(action)]
    start = example.prompt_len
    if example.input_ids[start:start+len(tokens)] != tokens:
        raise ValueError('recovery prefix token encoding mismatch')
    loss_start = start+len(tokens)
    if loss_start >= len(example.input_ids):
        raise ValueError('recovery has no expert continuation')
    example.meta['exit_recovery'] = dict(loss_start=loss_start, prefix_tokens=tokens,
        prefix_actions=len(prefix), full_prompt_len=start, source=source)
    if reason := mvp_row_failure(example):
        raise ValueError('recovery row admission: '+reason)
    return example


def admit_cached_example(instance,plan,report,payload,*,tokenizer):
    """Bind an in-budget fully replayed row to its checked plan/current layers."""
    from llm.stowlm.sft_data import SFTExample,build_example,example_sha256,mvp_row_failure
    from stowage.model import CheckReport
    from stowage.checker import report_matches_inputs,CHECKER_VERSION
    from llm.exit.preferences import layer_identity
    from llm.exit.runtime import recovery_loss_offset
    from stowage.frozen import require_clean
    import hashlib
    row=SFTExample(**payload)
    report=report if isinstance(report,CheckReport) else CheckReport.model_validate(report)
    require_clean(instance,plan,report,what='cached recovery row')
    if (not report.passed or not report_matches_inputs(report,instance,plan)
            or report.provenance.versions.get('checker')!=CHECKER_VERSION):
        raise ValueError('cached recovery report is not current and bound')
    expected=build_example(instance,plan,split='train',tokenizer=tokenizer)
    if example_sha256(row)!=example_sha256(expected):
        raise ValueError('cached recovery tokens differ from checked plan')
    if row.meta.get('gold',{}).get('report_sha256')!=hashlib.sha256(report.model_dump_json().encode()).hexdigest():
        raise ValueError('cached recovery report hash mismatch')
    replay=row.meta.get('exit_layered_replay',{})
    if replay.get('ok') is not True or replay.get('source_identity')!=layer_identity():
        raise ValueError('cached recovery layered replay is stale or missing')
    if reason:=mvp_row_failure(row):
        raise ValueError('cached recovery admission: '+reason)
    recovery_loss_offset(row)
    return row
