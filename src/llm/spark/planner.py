"""Unmasked Muse Spark baseline: propose actions, then stop at the first rejection.

Zero-shot means no stowage training examples are fitted to the model; a few
syntax examples only teach the wire grammar. The v2 observation is deliberately
compact; the original Instance remains authoritative. Unlike local constrained
decoding, an HTTP completion cannot mask illegal tokens. Replay therefore
measures legality rather than guaranteeing it. A legal action prefix is never a
shipping approval: callers must use stowage.planner.run_planner for ballast,
full checking and any repair. Sources: docs/architecture.md (fixed pipeline),
llm.stowlm.format (4.5 codec), stowage.actions.space (3.8 action semantics).

Metrics: raw_plan_legal is 1 only for a syntactically valid, complete response
whose every action replayed; average it across *all* attempts for the zero-shot
legal-plan rate. first_illegal_action_index and first_parse_error_index are
zero-based (None if absent). complete describes the retained prefix, not the
raw response. Costs and tokens are measured by the shared 1.8 client.
"""
from __future__ import annotations

import math
import time
from decimal import Decimal
from typing import Any

from llm.spark.client import MODEL, REASONING_EFFORTS, SparkClient, SparkIncompleteError
from llm.spark.rules_appendix import APPENDIX_VERSION, rules_appendix
from llm.stowlm.format import ActionCodec, END_PLAN, load_tokenizer
from llm.stowlm.format_prompt import (
    SYSTEM_PROMPT as STOWLM_SYSTEM_PROMPT,
    active_prompt_version, instance_to_prompt, prompt_version, system_prompt,
)
from stowage.actions import get_action_space
from stowage.feasibility import IllegalActionError
from stowage.frozen import current_provenance
from stowage.model import Instance, PlannerResult

# The stable implementation identity carries no switch-dependent prompt claim.
# Use planner_version()/SparkPlanner.version for a prompt-bound run identity.
PLANNER_VERSION = f"1.3/{MODEL}"
# Measured need (w2 pilot, experiments/spark-planner/w2-pilot/report.md): Spark's
# reasoning counts against max_output_tokens; tiny used 24-28k, small 42-91k (one
# small truncated at 65,536). Medium/full are unmeasured, so they get the API max.
DEFAULT_MAX_OUTPUT_TOKENS = {"tiny": 65_536, "small": 131_072, "medium": 131_072, "full": 131_072}
# Provider reasoning control (dev.meta.ai/docs/protocols/responses, checked
# 29 Oct): reasoning.effort minimal/low/medium/high/xhigh; omitted = the model's
# default. SparkClient does not send it yet, so the run identity records that.
REASONING_SETTING = "provider-default"  # when no reasoning_effort is passed


def output_cap(instance: Instance, max_output_tokens: int | None = None) -> int:
    """An explicit cap, else the measured need for the instance's rung."""
    if max_output_tokens is not None:
        return max_output_tokens
    if instance.size_class not in DEFAULT_MAX_OUTPUT_TOKENS:
        raise ValueError("no measured Spark output allowance for this rung; pass max_output_tokens")
    return DEFAULT_MAX_OUTPUT_TOKENS[instance.size_class]


def planner_version(instance: Instance | None = None, *, appendix: bool = True,
                    reasoning_effort: str | None = None) -> str:
    """The selected prompt, appendix condition and reasoning setting belong in run identity."""
    selected = prompt_version(instance) if instance is not None else active_prompt_version()
    condition = f"rules-appendix@{APPENDIX_VERSION}" if appendix else "compact-only"
    return f"{PLANNER_VERSION}/{selected}/{condition}/reasoning={reasoning_effort or REASONING_SETTING}"


class _PlannerVersion:
    """Keep both class-level checkpoint readers and instances switch-aware."""

    def __get__(self, instance, owner) -> str:
        if instance is None:
            return planner_version()
        return planner_version(appendix=instance.appendix, reasoning_effort=instance.reasoning_effort)


# Preserve the legacy header bytes; active prompts use the selected shared rule
# header and legend below. Spark HTTP generation has no feasibility mask.
SYSTEM_PROMPT = (
    "You are StowLM. Write only boxslot actions using supplied box indices; no analysis. "
    "Load all targets, satisfy all hard rules and minimise crane lifts. "
    "No feasibility mask is available during generation. "
    "Actions are replayed and the full checker decides whether a plan passes.\n"
    "Codes:" + STOWLM_SYSTEM_PROMPT.split("Codes:", 1)[1]
)

GRAMMAR = """Task: choose the ordered boxslot actions for this port call. Load all targets,
finish discharges and return restowed boxes; satisfy hard rules, then minimise
lifts. No feasibility mask is available here: do not assume invalid choices will
be corrected. Do not write analysis, JSON, prose or Markdown fences.
Grammar (each action ends with a newline; indices are zero-based, at least 3 digits):
P<index> <BBBRRTT> = place box in ship slot; blockers/covers move automatically.
D<index>[ <BLOCK-BBB-RR>] = discharge to the default or explicit yard stack.
R<index> <BLOCK-BBB-RR> = explicit yard relocation.
O<index> = restow off ship onto quay; place it back later with P.
End the entire list with the literal <|eot|>.
Syntax examples only (independent; these boxes/slots are not this instance):
P000 0010182
<|eot|>
D002
P004 0030102
<|eot|>
R001 A-003-01
P000 0010102
<|eot|>
Instance (v2 compact observation):
"""


def _without_reasoning_line(text: str) -> str:
    # StowLM's Glimmer chat-template cue; Spark's reasoning is a provider setting
    # (REASONING_SETTING), never an inherited prompt instruction (D76).
    for cue, keep in (("; Reasoning strength: low.", "."), (" Reasoning strength: low.", ""),
                      ("Reasoning strength: low.", "")):
        text = text.replace(cue, keep)
    return text.rstrip()


def build_prompt(instance: Instance, *, appendix: bool = True) -> str:
    """The exact prompt used for both estimation and the paid request.

    appendix=True (D76, the 6.7 condition) appends the plain-language rules and
    limits the checker applies; appendix=False is the labelled compact-only
    information ablation (StowLM's observation alone).
    """
    selected = system_prompt(instance)
    header = _without_reasoning_line(SYSTEM_PROMPT if selected == STOWLM_SYSTEM_PROMPT else (
        _without_reasoning_line(selected) + "\nNo feasibility mask is available during generation. "
        "Actions are replayed and the full checker decides whether a plan passes."
    ))
    grammar = GRAMMAR if selected == STOWLM_SYSTEM_PROMPT else GRAMMAR.replace(
        "Instance (v2 compact observation):", f"Instance ({prompt_version(instance)} compact observation):")
    prompt = header + "\n\n" + grammar + instance_to_prompt(instance)
    if appendix:
        prompt = prompt.rstrip("\n") + "\n\n" + rules_appendix(instance)
    return prompt


def _decode_line(codec: ActionCodec, line: str):
    # 4.5 currently exposes token decoding, but no public text parser. Its own
    # grammar parser avoids a competing interpretation of Spark's text output;
    # public encode/decode then validates field shapes and canonical spelling.
    action = codec._parse_action_text(line)
    tokens = codec.encode_action(action)
    if "".join(codec.action_fragments(action)) != line:
        raise ValueError("noncanonical action spelling")
    return codec.decode_action(tokens)


class SparkPlanner:
    """Planner protocol implementation; constructing it never makes an HTTP call.

    The baseline allocation guard runs before every completion in addition to
    the client's durable project-wide spend cap. Run-level preflight must also
    guard the whole test split before starting its first request. time_limit_s
    is a cooperative deadline checked before/after HTTP and each replay step;
    the injected client's HTTP timeout and bounded retries control blocking IO.
    """

    name = "muse-spark-zero-shot"
    version = _PlannerVersion()
    sets_ballast = False
    deterministic = False

    def __init__(self, client: SparkClient | None = None, *, tokenizer: Any = None,
                 max_output_tokens: int | None = None,
                 allocation_usd: Decimal = Decimal("60"), appendix: bool = True,
                 reasoning_effort: str | None = None):
        # None selects the measured per-rung need (DEFAULT_MAX_OUTPUT_TOKENS).
        if max_output_tokens is not None and (
                type(max_output_tokens) is not int or not 16 <= max_output_tokens <= 131072):
            raise ValueError("max_output_tokens must be from 16 to 131072")
        if type(appendix) is not bool:
            raise ValueError("appendix must be a boolean")
        if reasoning_effort is not None and reasoning_effort not in REASONING_EFFORTS:
            raise ValueError(f"reasoning_effort must be one of {', '.join(REASONING_EFFORTS)} or None")
        self.reasoning_effort = reasoning_effort
        self.appendix = appendix
        # Binding label for saved responses; None means the compact-only prompt.
        self.prompt_appendix = f"rules-appendix@{APPENDIX_VERSION}" if appendix else None
        if not allocation_usd.is_finite() or allocation_usd < 0:
            raise ValueError("allocation_usd must be nonnegative and finite")
        self.client, self.tokenizer = client, tokenizer
        self.max_output_tokens, self.allocation_usd = max_output_tokens, allocation_usd

    def plan(self, instance: Instance, *, seed: int = 0,
             time_limit_s: float | None = None) -> PlannerResult:
        start = time.monotonic()
        run_version = planner_version(instance, appendix=self.appendix,
                                      reasoning_effort=self.reasoning_effort)
        provenance = current_provenance(created_by=self.name, seed=seed).merge(
            instance.provenance, parent_ids=[instance.id])
        metrics = dict(deterministic=0, raw_plan_legal=0, complete=0,
                       output_parse_ok=0, legal_action_count=0,
                       first_illegal_action_index=None, first_parse_error_index=None)
        plan = None
        engine = None

        def result(status, message=None):
            if engine is not None:
                from eval.mask_observation import mask_diagnostic_snapshot
                diagnostic = mask_diagnostic_snapshot(engine, instance.id)
                if diagnostic is not None:
                    metrics["mask_diagnostics"] = [diagnostic]
                    metrics["mask_global_screen"] = diagnostic.get("global_screen")
            prov = provenance.merge(plan.provenance) if plan is not None else provenance
            return PlannerResult(status=status, plan=plan, planner=self.name,
                                 planner_version=run_version, seed=seed,
                                 wall_time_s=time.monotonic() - start, metrics=metrics,
                                 message=message, provenance=prov)

        def expired():
            return time_limit_s is not None and time.monotonic() - start >= time_limit_s

        try:
            if time_limit_s is not None and (not math.isfinite(time_limit_s) or time_limit_s < 0):
                return result("error", "time_limit_s must be nonnegative and finite")
            if expired():
                return result("timeout", "Planning deadline reached before request")
            tokenizer = self.tokenizer if self.tokenizer is not None else load_tokenizer()
            # The allocation is a job-local allowance, never a durable reduction
            # of the shared project cap. Reuse the client to retain the job's
            # starting spend across plan calls.
            if self.client is None:
                self.client = SparkClient()
            client = self.client
            from llm.spark.planner_budget import allocation_budget, estimate_instance, guard_run

            cap = output_cap(instance, self.max_output_tokens)
            metrics.update(max_output_tokens=cap, prompt_condition=(
                f"rules-appendix@{APPENDIX_VERSION}" if self.appendix else "compact-only"),
                reasoning_setting=self.reasoning_effort or REASONING_SETTING)
            estimate = estimate_instance(instance, tokenizer=tokenizer, max_output_tokens=cap,
                                         appendix=self.appendix)
            with allocation_budget(client.ledger, allocation_usd=self.allocation_usd):
                guard_run([estimate], client.ledger, allocation_usd=self.allocation_usd)
                if expired():
                    return result("timeout", "Planning deadline reached before request")
                # Only an explicit effort is sent, so injected stub clients
                # (replay) keep the original call signature.
                effort = {} if self.reasoning_effort is None else {"reasoning_effort": self.reasoning_effort}
                response = client.complete(build_prompt(instance, appendix=self.appendix),
                                           purpose=self.name, max_output_tokens=cap, **effort)
            metrics.update(tokens_in=response.usage.input_tokens,
                           tokens_out=response.usage.output_tokens,
                           cached_tokens=response.usage.cached_tokens,
                           cost_usd=str(response.cost_usd))
            if expired():
                return result("timeout", "Planning deadline reached during HTTP completion")
            space = get_action_space("boxslot")
            # Resolve the public factory during planning so harness observers see
            # engines even when this module was imported before observation.
            from stowage.feasibility import make_feasibility_engine
            engine = make_feasibility_engine(instance, space)
            codec = ActionCodec(tokenizer, [box.id for box in instance.containers])
            text = response.text
            # Whitespace after the end marker carries no action (live Spark
            # ends with "<|eot|>\n"); the marker itself is still required.
            has_end = text.rstrip().endswith(END_PLAN)
            body = text.rstrip()[:-len(END_PLAN)] if has_end else text
            lines = body.splitlines(keepends=True)
            metrics["response_action_lines"] = len(lines)
            failure = None
            for index, line in enumerate(lines):
                if expired():
                    failure = ("timeout", "Planning deadline reached during replay")
                    break
                try:
                    action = _decode_line(codec, line)
                except ValueError:
                    metrics["first_parse_error_index"] = index
                    failure = ("error", f"Malformed action at index {index}")
                    break
                try:
                    engine.push(action)
                except IllegalActionError as exc:
                    violation = getattr(exc, "violation", None)
                    metrics["first_illegal_rule_id"] = getattr(violation, "rule_id", None)
                    metrics["first_illegal_action_index"] = index
                    metrics["first_illegal_action"] = space.encode(action)
                    failure = ("dead_end", f"Illegal action at index {index}; replay stopped")
                    break
                metrics["legal_action_count"] += 1
            complete = engine.is_complete()
            metrics["complete"] = int(complete)
            plan = engine.to_plan() if complete else space.expand(instance, engine.actions())
            if failure is not None:
                return result(*failure)
            if not has_end:
                metrics["first_parse_error_index"] = len(lines)
                return result("error", "Missing end-of-plan marker")
            metrics["output_parse_ok"] = 1
            metrics["raw_plan_legal"] = int(complete)
            if not complete:
                return result("dead_end", "Response ended before required work was complete")
            return result("ok")
        except SparkIncompleteError as exc:
            # The client has settled measured usage, but partial output is never
            # a plan. Preserve only typed accounting for failed-attempt metrics.
            metrics.update(error_type=type(exc).__name__, tokens_in=exc.usage.input_tokens,
                           tokens_out=exc.usage.output_tokens,
                           cached_tokens=exc.usage.cached_tokens, cost_usd=str(exc.cost_usd),
                           response_incomplete=1, incomplete_reason=exc.reason)
            return result("error", "Spark response incomplete; measured usage logged")
        except Exception as exc:
            # Avoid echoing response bodies, credentials or client exception text.
            metrics["error_type"] = type(exc).__name__
            return result("error", "Spark planning failed; inspect typed error and spend ledger")


# Convenient constructor matching the task's Planner terminology.
Planner = SparkPlanner
