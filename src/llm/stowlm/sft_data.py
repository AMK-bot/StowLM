"""SFT dataset (plan 7.1): serialise gold plans into StowLM training examples.

One example = one (Instance, MovePlan) pair:

    input_ids  = encode_prompt(instance)           # system + user(prompt) + "<|start|>assistant to=user<|message|>"
               + ActionCodec.encode_plan(actions)  # one canonical token run per boxslot action, then <|eot|>
    prompt_len = len(encode_prompt(instance))      # loss mask: only tokens at positions >= prompt_len are trained

Both halves come from the shared token-ID builder (``llm.stowlm.format.build_sequence``, decision D9): text is never
re-tokenised, so the token IDs the model is trained on are exactly the ones the mask allows at decode time. The chat
framing is 4.5's: the assistant turn's recipient is fixed to ``user`` and the completion starts directly with
actions (no analysis channel). "Loss on the assistant tokens only" is standard SFT practice: the prompt is context,
not something the model should learn to write (e.g. Ouyang et al. 2022, InstructGPT §3.5; mlx-lm's ``mask_prompt``).

The 7.1 done criterion is that every example replays through the mask with no masked token: ``verify_replay`` feeds
the completion tokens to the 5.1 mask (``llm.stowlm.mask.MaskedDecoder``) and requires each to be allowed and the
plan to end ``complete``.

``build_example`` is a general serializer and explicitly labels its output as non-gold. ``build_gold_example``
alone admits published 6.1 records with pinned v2 membership, bound full D50 checker evidence, current producer
epochs and fresh default production replay. Decoder completion itself does not establish checker approval.

Gold plans must be clean (``stowage.frozen.require_clean``). ``allow_stubs=True`` exists only for the tiny chain run
(experiments/tiny-chain/), whose plans are stub-tainted; such examples carry their taint into the dataset files and
``write_dataset`` refuses to write them outside a folder named ``tiny-chain`` or ``sft-pilot`` (TAINT_OK_DIRS).
"""

from __future__ import annotations

import json
import math
import gzip
from hashlib import sha256
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from llm.stowlm.format import FORMAT_VERSION, ActionCodec, encode_example, load_tokenizer
from llm.stowlm.format_prompt import active_prompt_version, encode_prompt
from stowage.actions import ACTION_SPACE_VERSION, get_action_space
from stowage.frozen import current_provenance, require_clean
from stowage.model import Instance, MovePlan
from stowage.model.provenance import Provenance

DATASET_VERSION = "stowlm-sft@2.0"
# Folders where stub-tainted examples/adapters may live: the tiny chain and the 8.1 sizing pilot (timing only).
TAINT_OK_DIRS = {"tiny-chain", "sft-pilot"}


@dataclass
class SFTExample:
    instance_id: str
    split: str
    input_ids: list[int]
    prompt_len: int
    n_actions: int
    n_boxes: int
    plan_id: str
    stubs: dict[str, str] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def completion_len(self) -> int:
        return len(self.input_ids) - self.prompt_len

    @property
    def provenance(self) -> Provenance:
        return Provenance(stubs=dict(self.stubs))


def plan_actions(instance: Instance, plan: MovePlan) -> list[Any]:
    """The plan's structured actions (stored, never re-derived from tokens). Falls back to ``from_plan``."""
    if plan.instance_id != instance.id:
        raise ValueError("plan instance_id does not match the supplied instance")
    if plan.call_index != instance.call_index:
        raise ValueError("plan call_index does not match the supplied instance")
    space = get_action_space()
    if plan.actions is not None:
        if plan.action_space not in (None, f"{space.name}@{space.version}"):
            raise ValueError(f"plan {plan.id} speaks {plan.action_space}, not {space.name}@{space.version}")
        return [space.decode(a) for a in plan.actions]
    return space.from_plan(instance, plan)


def codec_for(instance: Instance, tokenizer: Any) -> ActionCodec:
    return ActionCodec(tokenizer, [box.id for box in instance.containers])


def example_sha256(example: SFTExample) -> str:
    """Bind replay evidence to the complete serialized row, excluding evidence metadata."""
    values = {key: value for key, value in asdict(example).items() if key != "meta"}
    return sha256(json.dumps(values, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def build_example(instance: Instance, plan: MovePlan, *, split: str, tokenizer: Any | None = None,
                  allow_stubs: bool = False) -> SFTExample:
    """Serialize a pair without gold approval; use build_gold_example for production gold."""
    provenance = current_provenance().merge_nested(instance, plan)
    if not allow_stubs:
        require_clean(instance, plan, provenance, what="SFT dataset builder (7.1)")
    tokenizer = tokenizer or load_tokenizer()
    actions = plan_actions(instance, plan)
    encoded = encode_example(instance, actions, tokenizer)
    return SFTExample(
        instance_id=instance.id, split=split, input_ids=list(encoded.input_ids), prompt_len=encoded.prompt_len,
        n_actions=encoded.n_actions, n_boxes=len(instance.load_list), plan_id=plan.id,
        stubs=provenance.stubs,
        meta={**encoded.meta, "dataset": DATASET_VERSION,
              "gold": {"published": False}},
    )


@dataclass
class ReplayReport:
    ok: bool
    n_actions: int
    n_tokens: int
    first_failure: str | None = None
    stubs: dict[str, str] = field(default_factory=dict)
    global_screen: str = "unavailable: engine not constructed"
    production: bool = False
    mask_version: str = ""
    mask_config: dict[str, Any] = field(default_factory=dict)
    d79: bool = False  # replayed on the MVP engine (C2 off by config, D79); never sets ``production``
    securing: str | None = None  # D95: name of the deck-securing mask layer the replay went through, if any

    @property
    def provenance(self) -> Provenance:
        return Provenance(stubs=dict(self.stubs))


def verify_replay(instance: Instance, example: SFTExample, *, tokenizer: Any | None = None,
                  make_engine: Callable[..., Any] | None = None, d79: bool = False,
                  securing: bool = False) -> ReplayReport:
    """Replay an example's completion tokens through the 5.1 mask (``llm.stowlm.mask.MaskedDecoder``) from the
    instance's root engine state: every token must be in the allowed set before it, and the plan must end with the
    decoder ``complete``. Because the mask's allowed sets are read off the engine's ``legal_actions()``, this also
    proves every action was legal at its state.

    ``d79=True`` replays on the MVP engine (D79: ``d79_engine_config()``, C2 off by configuration, no stub). Such a
    report is never ``production`` (that means C2 on); MVP gold admission (``mvp_row_failure``) checks ``d79``.
    ``securing=True`` also composes the D95 necessary deck-securing layer (``llm.securing``) over the engine, as
    StowLM decodes in the MVP; MVP gold requires it."""
    from llm.stowlm.mask import MASK_VERSION, MaskConfig, MaskedDecoder
    from stowage.feasibility import make_feasibility_engine

    provenance = current_provenance().merge_nested(instance, example.provenance)
    if make_engine is not None:
        provenance = provenance.merge(Provenance(stubs={
            "sft.replay.make_engine": "injected engine factory; diagnostic replay only"}))
    global_screen = "unavailable: engine not constructed"
    config = MaskConfig()

    def finish(ok: bool, n_actions: int, n_tokens: int, failure: str | None = None) -> ReplayReport:
        # A replay is another derivation boundary: preserve its taint even after
        # an override exits, including when a caller writes this example later.
        example.stubs.update(provenance.stubs)
        report = ReplayReport(ok, n_actions, n_tokens, failure, dict(provenance.stubs), global_screen,
                              ok and not d79 and not provenance.stubs and global_screen == "on" and make_engine is None,
                              MASK_VERSION, asdict(config),
                              ok and d79 and not provenance.stubs and global_screen == D79_SCREEN and make_engine is None,
                              SECURING_LAYER if ok and securing else None)
        example.meta["replay"] = asdict(report)
        example.meta["replay"]["example_sha256"] = example_sha256(example)
        return report

    if example.instance_id != instance.id:
        return finish(False, 0, 0, "example instance_id does not match the supplied instance")
    if not 0 <= example.prompt_len < len(example.input_ids):
        return finish(False, 0, 0, "invalid prompt_len or empty completion")
    tokenizer = tokenizer or load_tokenizer()
    if tuple(example.input_ids[:example.prompt_len]) != encode_prompt(instance, tokenizer):
        return finish(False, 0, 0, "example prompt does not match supplied instance/prompt_len")
    try:
        if d79 and make_engine is None:
            engine = make_feasibility_engine(instance, get_action_space(), config=d79_engine_config())
        else:
            engine = (make_engine or make_feasibility_engine)(instance, get_action_space())
        provenance = provenance.merge_nested(getattr(engine, "provenance", None))
        global_screen = str(getattr(engine, "diagnostics", {}).get("global_screen", "unavailable: not reported"))
        if d79 and make_engine is None and global_screen != D79_SCREEN:
            return finish(False, 0, 0, f"D79 replay expected global_screen {D79_SCREEN!r}; got {global_screen}")
        if securing:
            from llm.securing.mask_filter import wrap_engine

            engine = wrap_engine(engine)
        if make_engine is None and not d79 and global_screen != "on":
            return finish(False, 0, 0, f"production replay requires actual C2 on; {global_screen}")
        decoder = MaskedDecoder(engine, codec_for(instance, tokenizer), config)
    except Exception as error:
        return finish(False, 0, 0, f"replay engine error: {type(error).__name__}: {error}")
    tokens = example.input_ids[example.prompt_len:]
    for pos, token in enumerate(tokens):
        try:
            allowed = decoder.allowed_token_ids()
        except Exception as error:
            return finish(False, len(decoder.actions), pos, f"replay mask error: {type(error).__name__}: {error}")
        if token not in allowed:
            why = decoder.dead_end.reason if decoder.dead_end is not None else f"{len(allowed)} allowed"
            return finish(False, len(decoder.actions), pos,
                                f"token {pos} ({token}) masked after {len(decoder.actions)} actions: {why}")
        try:
            decoder.advance(token)
        except Exception as error:
            return finish(False, len(decoder.actions), pos, f"replay advance error: {type(error).__name__}: {error}")
    if decoder.status != "complete":
        return finish(False, len(decoder.actions), len(tokens), f"decoder status {decoder.status} at end")
    if len(decoder.actions) != example.n_actions:
        return finish(False, len(decoder.actions), len(tokens), "decoded n_actions does not match example")
    return finish(True, len(decoder.actions), len(tokens))


# --------------------------------------------------------------------------------------------- MVP gold (plan v4)
# D79 (leader, 29 Oct): the MVP runs the mask and solver with C2 off by configuration; the full checker stays the gate.
# MVP gold = a plan that passed the full checker (report bound to these exact inputs) and replays token by token
# through the mask on that D79 engine. APPROXIMATION: no v3 split-v2 release pins or published-shard lineage; the
# MVP corpus (experiments/mvp/corpus, seed-disjoint splits) is the split authority, and test inputs are refused.
MVP_GOLD = "mvp_gold"
SECURING_LAYER = "irs-securing-necessary-envelope"  # llm.securing.securing_decoder_factory().name (D95)
D79_SCREEN = "off: config extra['global_screen']=0 (diagnostic)"  # what the engine reports for D79's config


def d79_engine_config():
    from stowage.feasibility import FeasibilityConfig

    return FeasibilityConfig(extra={"global_screen": 0})


def build_mvp_example(instance: Instance, plan: MovePlan, report: Any, *, split: str, source: str,
                      tokenizer: Any | None = None) -> SFTExample:
    """Admit one checker-passed plan as MVP gold: clean inputs, a passing report bound to (instance, plan), and a
    D79 mask replay with no masked token. Raises ValueError with the reason on any failure."""
    from stowage.checker import report_matches_inputs
    from stowage.model import CheckReport

    if split not in ("train", "val"):
        raise ValueError(f"MVP gold trains on train/val only, not {split!r}")
    report = report if isinstance(report, CheckReport) else CheckReport.model_validate(report)
    if report.passed is not True:
        raise ValueError("checker report did not pass")
    if not report_matches_inputs(report, instance, plan):
        raise ValueError("checker report is not bound to this instance/plan")
    if report.provenance.stubs:
        raise ValueError(f"checker report is stub-tainted ({sorted(report.provenance.stubs)})")
    example = build_example(instance, plan, split=split, tokenizer=tokenizer)  # require_clean inside
    replay = verify_replay(instance, example, tokenizer=tokenizer, d79=True, securing=True)
    if not (replay.ok and replay.d79 and replay.securing == SECURING_LAYER):
        raise ValueError(f"D79 mask replay failed: {replay.first_failure}")
    report_sha = sha256(report.model_dump_json().encode()).hexdigest()
    example.meta["action_space"] = ACTION_SPACE_VERSION
    example.meta["gold"] = {"published": True, "kind": MVP_GOLD, "checker_passed": True,
                            "report_sha256": report_sha, "source": source,
                            "weighted_nonproductive": getattr(report.count, "weighted_nonproductive", None),
                            "example_sha256": example_sha256(example)}
    return example


def mvp_row_failure(e: SFTExample) -> str | None:
    """Re-check one MVP gold row (writer and trainer both call it; neither trusts a stats label)."""
    from llm.stowlm.mask import MASK_VERSION

    gold, replay = e.meta.get("gold", {}), e.meta.get("replay", {})
    digest = example_sha256(e)
    if gold.get("published") is not True or gold.get("kind") != MVP_GOLD or gold.get("checker_passed") is not True:
        return "lacks MVP gold admission"
    if e.split not in ("train", "val"):
        return f"split {e.split!r} is not train/val"
    if gold.get("example_sha256") != digest or replay.get("example_sha256") != digest:
        return "content binding changed after gold admission/replay"
    if not (replay.get("ok") is True and replay.get("d79") is True and replay.get("mask_version") == MASK_VERSION
            and not replay.get("stubs") and not e.stubs):
        return "lacks a current, untainted D79 mask replay"
    if replay.get("securing") != SECURING_LAYER:
        return "mask replay did not include the D95 securing layer"
    if any(e.meta.get(k) != v for k, v in
           (("format", FORMAT_VERSION), ("prompt", active_prompt_version()),
            ("action_space", ACTION_SPACE_VERSION), ("dataset", DATASET_VERSION))):
        return "format/prompt/action-space/dataset version mismatch"
    return None


def _gold_producer_contract() -> dict[str, Any]:
    """Use 6.1's source epoch owner rather than maintain a second source allowlist."""
    import importlib.util
    import sys

    path = Path(__file__).resolve().parents[3] / "experiments/gold-plans/tiny/driver.py"
    name = "_stowlm_sft_gold_epoch"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("gold source epoch owner is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        return {"source_sha256": module.source_fingerprint()["sha256"],
                "component_versions": module.component_versions()}
    finally:
        sys.modules.pop(name, None)


def current_gold_source_sha256() -> str:
    return _gold_producer_contract()["source_sha256"]


def current_gold_component_versions() -> dict[str, str]:
    return _gold_producer_contract()["component_versions"]


def gold_rule_policy():
    """Use the leader's shared freeze/admission policy, without a second rule list."""
    import importlib.util
    path = Path(__file__).resolve().parents[3] / 'experiments/campaign-g1/rule_policy.py'
    spec = importlib.util.spec_from_file_location('_sft_rule_policy', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_production_lineage(gold: Mapping[str, Any]) -> dict[str, Any]:
    """Pipeline tests need same-epoch leader approval before production use."""
    if gold.get('data_mode') != 'production':
        raise ValueError('pipeline_test gold requires same-epoch G1 approval for production')
    epoch, rung, pin = gold.get('epoch_id'), gold.get('rung'), gold.get('required_rules')
    if not isinstance(epoch, str) or not epoch or rung not in ('tiny', 'small', 'medium', 'full'):
        raise ValueError('production gold requires its frozen epoch and rung')
    if not isinstance(pin, dict):
        raise ValueError('production gold lacks frozen required_rules policy')
    policy = gold_rule_policy()
    policy.enforce_rule_policy(Path(__file__).resolve().parents[3], pin)
    policy._module('migration_evidence').assert_component_epoch({'component_epoch':gold.get('component_epoch')})
    return policy.validate_g1_approval(gold.get('g1_approval') or {}, epoch, [rung])


def _require_current_instance_generators(instance: Instance) -> None:
    from stowage.versions import CARGO_GENERATOR_VERSION, RANDOM_YARD_VERSION, SIMULATED_INSTANCES_VERSION
    from stowage.generate.instances_v2_random import RANDOM_V2_VERSION
    from stowage.vessel.fleet_conditions import CONDITION_VERSION
    from stowage.generate.background_acceptance import requires_background_gate
    versions = instance.provenance.versions
    generator = instance.family.get('generator')
    if generator in ('simulated', 'simulated_yard') or instance.provenance.created_by == 'generate.instances':
        expected = {'instances': SIMULATED_INSTANCES_VERSION, 'cargo_generator': CARGO_GENERATOR_VERSION}
    elif generator == 'random_yard' or instance.provenance.created_by == 'generate.random_yard':
        expected = {'random_yard': RANDOM_YARD_VERSION, 'random_yard_v2': RANDOM_V2_VERSION}
    else:
        raise ValueError('instance generator identity is missing or unknown')
    if any(versions.get(key) != value for key, value in expected.items()):
        raise ValueError('instance own generator versions are stale or missing')
    if any(instance.provenance.input_versions.get(key) for key in expected):
        raise ValueError('instance generator provenance mixes older versions')
    if (requires_background_gate(instance.vessel, size=instance.size_class) or 'slice_condition' in versions):
        if versions.get('slice_condition') != CONDITION_VERSION:
            raise ValueError('instance own condition version is stale or missing')
        if instance.provenance.input_versions.get('slice_condition'):
            raise ValueError('instance condition provenance mixes older versions')


def build_gold_example(instance: Instance, plan: MovePlan, *, split: str,
                       gold_record: Mapping[str, Any], publication: Mapping[str, Any],
                       tokenizer: Any | None = None, split_manifest: str | Path | None = None) -> SFTExample:
    """Admit a published 6.1 record, then independently replay its canonical tokens.

    Publication includes the manifest plus ``entry`` and original ``record_bytes`` /
    ``input_bytes``. Original bytes are required because JSON reformatting changes
    a publication checksum. A hash asserted by the caller alone is not linkage.
    This validates published evidence; it does not turn synthetic evidence into a
    physical gold witness, nor does decoder completion replace the full checker.
    """
    from eval.splits import canonical_spec, load_inventory, require_membership, spec_from_instance, spec_key
    from stowage.checker import report_matches_inputs
    from stowage.model import CheckReport, PlannerResult
    from stowage.versions import CHECKER_VERSION, component_versions

    require_clean(instance, plan, current_provenance(), what="SFT gold admission")
    record = dict(gold_record)
    entry = publication.get("entry")
    if not isinstance(entry, Mapping) or entry not in publication.get("shards", []):
        raise ValueError("gold record is not a published manifest shard")
    raw = publication.get("record_bytes")
    if not isinstance(raw, bytes) or sha256(raw).hexdigest() != entry.get("sha256"):
        raise ValueError("published gold record bytes/checksum mismatch")
    if json.loads(raw) != record:
        raise ValueError("supplied gold record differs from published bytes")
    raw_input = publication.get("input_bytes")
    if (not isinstance(raw_input, bytes) or sha256(raw_input).hexdigest() != entry.get("input_sha256")
            or entry.get("input_sha256") != record.get("input_sha256")):
        raise ValueError("published input bytes/checksum mismatch")
    input_payload = gzip.decompress(raw_input) if raw_input[:2] == b"\x1f\x8b" else raw_input
    if Instance.model_validate_json(input_payload) != instance:
        raise ValueError("supplied instance differs from published input bytes")
    _require_current_instance_generators(instance)
    inventory = load_inventory(split_manifest)
    pins = {"split_version": inventory.version, "split_entries_sha256": inventory.entries_sha256}
    if inventory.version != "v2":
        raise ValueError("production SFT gold requires splits v2")
    if any(obj.get(key) != value for obj in (record, entry, publication) for key, value in pins.items()):
        raise ValueError("gold split release pin mismatch")
    spec = record.get("split_spec")
    require_membership(spec, inventory=inventory, split=split)
    if canonical_spec(spec_from_instance(instance)) != canonical_spec(spec):
        raise ValueError("instance differs from gold split_spec")
    if any(obj.get("split") != split or obj.get("instance_id") != instance.id
           or obj.get("spec_key") != spec_key(spec) for obj in (record, entry)):
        raise ValueError("published split/instance/spec identity mismatch")
    if record.get("format") != "tiny-gold@1.0" or record.get("accepted") is not True:
        raise ValueError("record is not accepted production gold")
    if publication.get("format") != "tiny-gold-manifest@1.0":
        raise ValueError("unsupported gold publication format")
    versions = current_gold_component_versions()
    if (any(versions.get(key) != value for key, value in component_versions().items())
            or any(obj.get("component_versions") != versions for obj in (record, publication))):
        raise ValueError("gold checker/component epoch mismatch")
    epoch = record.get("relevant_source_sha256")
    if (not isinstance(epoch, str) or len(epoch) != 64 or publication.get("relevant_source_sha256") != epoch
            or current_gold_source_sha256() != epoch):
        raise ValueError("gold source epoch mismatch")
    source = record.get("gold_source")
    if source not in ("exact", "scalable"):
        raise ValueError("unsupported gold source")
    candidate = record.get(source, {})
    if (record.get("plan") != plan.model_dump(mode="json") or candidate.get("plan") != record.get("plan")
            or candidate.get("report") != record.get("check_report")):
        raise ValueError("published candidate plan/report mismatch")
    for obj in (record, candidate):
        if any(obj.get(flag) is not True for flag in ("accepted", "shippable", "mask_verified")):
            raise ValueError("published gold lacks accepted checker/mask/shipping evidence")
    if any(entry.get(flag) is not True for flag in ("checker_passed", "shippable", "mask_verified")):
        raise ValueError("published shard lacks checker/mask/shipping evidence")
    if record.get("c2") != "on/default" or candidate.get("global_screen") != "on":
        raise ValueError("published gold lacks actual C2 evidence")
    if instance.vessel.hull is None or plan.ballast is None:
        raise ValueError("gold requires declared full hull and completed ballast")
    actions = plan_actions(instance, plan)
    space = get_action_space()
    if (plan.actions is None or plan.action_space != f"{space.name}@{space.version}"
            or record.get("actions_schema") != plan.action_space or actions != space.from_plan(instance, plan)):
        raise ValueError("gold canonical actions do not match plan moves/schema")
    report = CheckReport.model_validate(record.get("check_report"))
    require_clean(report, what="SFT gold checker evidence")
    if report.provenance.versions.get("checker") != CHECKER_VERSION:
        raise ValueError("gold checker report epoch mismatch")
    if not report_matches_inputs(report, instance, plan):
        raise ValueError("gold checker report is not bound to supplied inputs")
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    programme = validate_in_port_programme(report, activated=rule_freeze_v1_enabled())
    result = PlannerResult(status="ok", plan=plan, planner=str(source), planner_version="published-gold")
    result._record_full_check(instance, report)
    if not result.shippable:
        raise ValueError("gold full checker evidence is incomplete or failed")
    if instance.voyage is None:
        raise ValueError("gold lacks D50 voyage metadata")
    # D50 evidence contract: IACS S11A §2.2.2 (data/raw/iacs/ur-s11a.txt,
    # PDF p. 8) requires departure/arrival strength. IS Code Part B 3.4.1.2–.3
    # (data/raw/is-code/msc267-85.pdf, PDF p. 41) pairs departure with arrival.
    # The full checker owns the physics and consumable draw-down calculation.
    condition_parts = [f"condition:{identity.id}:{part}"
                       for identity in (instance.voyage.departure, instance.voyage.arrival)
                       for part in ("stability", "strength")]
    if any(name not in report.parts for name in ["voyage", *condition_parts]):
        raise ValueError("gold lacks D50 departure/arrival checker parts")
    example = build_example(instance, plan, split=split, tokenizer=tokenizer)
    replay = verify_replay(instance, example, tokenizer=tokenizer)
    if not replay.ok or not replay.production or replay.stubs:
        raise ValueError(f"gold production replay failed: {replay.first_failure or 'tainted/nonproduction'}")
    example.meta["replay"] = {**asdict(replay), "example_sha256": example_sha256(example)}
    example.meta["gold"] = {"published": True, **pins, "record_sha256": entry["sha256"],
                            "input_sha256": entry["input_sha256"], "spec_key": record["spec_key"],
                            "source_epoch_sha256": epoch, "checker_version": CHECKER_VERSION,
                            "ballast_programme": programme,
                            "voyage": instance.voyage.model_dump(mode="json"),
                            "condition_parts": condition_parts, "example_sha256": example_sha256(example)}
    lineage = dict(data_mode='pipeline_test', epoch_id=record.get('epoch_id', publication.get('epoch_id')),
        rung=spec['rung'], tiny_g1_exception=bool(record.get('tiny_g1_exception') or publication.get('tiny_g1_exception')),
        required_rules=publication.get('required_rules', record.get('required_rules')),
        component_epoch=record.get('component_epoch'),
        g1_approval=publication.get('g1_approval', record.get('g1_approval')) or
            (publication.get('g1_summary') or record.get('g1_summary') or {}).get('approval'))
    if lineage['g1_approval'] is not None and publication.get('data_mode') != 'pipeline_test':
        if record.get('epoch_id') != lineage['epoch_id'] or publication.get('epoch_id') != lineage['epoch_id']:
            raise ValueError('gold publication/record frozen epochs differ')
        if publication.get('component_epoch') != lineage['component_epoch']:
            raise ValueError('M5_GOLD_COMPONENT_MISMATCH: gold publication/record component epochs differ')
        lineage['data_mode'] = 'production'
        validate_production_lineage(lineage)
    else:
        lineage['g1_approval'] = None
    example.meta['gold'].update(lineage)
    return example


PROGRAMME_RATE_RULE = "port.ballast.transfer_rate"
_PROGRAMME_COUNTS = ("changeover_k", "lifts_after_k", "pump_window_lifts", "cranes")


def validate_in_port_programme(report: Any, *, activated: bool) -> str | None:
    """D74 in-port ballast programme on an activated gold report (DESIGN.md Addendum B §B.3, incl. ballast_delay_h).

    The typed ``PartReport.ballast_programme`` already rejects missing or ill-typed fields. Admission also refuses
    what the type admits but evidence can't be: non-finite values (JSON ``NaN`` parses as a float), negative masses,
    times or counts, no crane, or another rate rule. Under activation an in-port part must carry the block explicitly
    (``null`` = no programme needed); a legacy-shaped in-port part is not activated evidence. The checker, not this
    function, decides whether the programme is physically feasible. Switch off: unchanged (returns None).
    Returns ``"present"``, ``"null"`` or None (no in-port part, or switch off)."""
    if not activated:
        return None
    part = getattr(report, "parts", {}).get("in_port")
    if part is None:
        return None
    if "ballast_programme" not in part.model_fields_set:
        raise ValueError("activated D74 in-port report lacks its explicit ballast_programme block")
    programme = part.ballast_programme
    if programme is None:
        return "null"
    values = programme.model_dump()
    for name, value in values.items():
        if isinstance(value, bool) or value is None or isinstance(value, str):
            continue
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"malformed ballast_programme: {name}={value!r}")
    if any(type(values[name]) is not int for name in _PROGRAMME_COUNTS) or values["cranes"] < 1:
        raise ValueError("malformed ballast_programme: counts must be integers and cranes >= 1")
    if values["rate_check_rule"] != PROGRAMME_RATE_RULE:
        raise ValueError(f"malformed ballast_programme: rate_check_rule {values['rate_check_rule']!r}")
    return "present"


def _summary(values: Sequence[int]) -> dict[str, float]:
    v = sorted(values)
    if not v:
        return {}
    q = lambda p: v[min(len(v) - 1, max(0, math.ceil(p * len(v)) - 1))]  # noqa: E731 nearest-rank percentile
    return {"n": len(v), "min": v[0], "p50": q(0.5), "mean": round(sum(v) / len(v), 1), "p95": q(0.95),
            "max": v[-1], "sum": sum(v)}


def length_stats(examples: Iterable[SFTExample]) -> dict[str, Any]:
    ex = list(examples)
    acts = sum(e.n_actions for e in ex)
    return {
        "examples": len(ex),
        "prompt_tokens": _summary([e.prompt_len for e in ex]),
        "completion_tokens": _summary([e.completion_len for e in ex]),
        "total_tokens": _summary([len(e.input_ids) for e in ex]),
        "n_actions": _summary([e.n_actions for e in ex]),
        "n_boxes": _summary([e.n_boxes for e in ex]),
        "completion_tokens_per_action": round(sum(e.completion_len for e in ex) / acts, 2) if acts else None,
    }


def order_by_size(examples: Iterable[SFTExample]) -> list[SFTExample]:
    """Curriculum order: fewer boxes first, then shorter sequences, then instance id (deterministic)."""
    return sorted(examples, key=lambda e: (e.n_boxes, len(e.input_ids), e.instance_id))


def write_dataset(examples: Sequence[SFTExample], out_dir: str | Path, *,
                  production: bool = False, required_splits: Sequence[str] | None = None,
                  mvp: bool = False) -> dict[str, Any]:
    """Write curriculum-ordered split files and statistics, replacing all managed splits.

    The backward-compatible default is an explicitly unverified serialization.
    Production publication requires independently admitted gold and current replay
    evidence. All validation and serialization finishes before existing files change;
    individual replacements are atomic and stats is the final publication marker.
    """
    import os
    import tempfile
    from dataclasses import replace

    from stowage.frozen import current_provenance

    supported = {"train", "val", "test"}
    ex = list(examples)
    splits = {e.split for e in ex}
    required = set(required_splits or ())
    if not splits <= supported or not required <= supported:
        raise ValueError("dataset split must be one of train, val, test")
    if required - splits:
        raise ValueError(f"missing required dataset splits: {sorted(required - splits)}")

    # An override active during publication is part of the output's lineage too.
    active = current_provenance().stubs
    ex = [replace(e, stubs={**e.stubs, **e.meta.get("replay", {}).get("stubs", {}), **active}) for e in ex]
    out = Path(out_dir).resolve()
    tainted = [e.instance_id for e in ex if e.stubs]
    if tainted and not TAINT_OK_DIRS & set(out.parts):
        raise RuntimeError(f"{len(tainted)} stub-tainted examples (e.g. {tainted[0]}) can only go to a tiny-chain/sft-pilot folder")
    for name in ("train.jsonl", "val.jsonl", "test.jsonl", "stats.json"):
        target = out / name
        if target.is_symlink() or target.resolve().parent != out:
            raise ValueError(f"dataset target symlink or directory escape: {target}")

    pins: dict[str, Any] = {}
    if mvp and production:
        raise ValueError("a dataset is either v3 production gold or MVP gold, not both")
    if mvp:
        if not ex:
            raise ValueError("MVP gold dataset cannot be empty")
        if tainted:
            raise ValueError("MVP gold dataset refuses stub taint")
        for e in ex:
            if (why := mvp_row_failure(e)) is not None:
                raise ValueError(f"example {e.instance_id}: {why}")
    if production:
        from eval.splits import load_inventory
        from llm.stowlm.mask import MASK_VERSION

        if not ex:
            raise ValueError("production gold dataset cannot be empty")
        if tainted:
            raise ValueError("production gold dataset refuses stub taint")
        inventory = load_inventory()
        pins = {"split_version": inventory.version, "split_entries_sha256": inventory.entries_sha256}
        for e in ex:
            gold, replay = e.meta.get("gold", {}), e.meta.get("replay", {})
            if gold.get("published") is not True:
                raise ValueError(f"example {e.instance_id} lacks published gold admission")
            digest = example_sha256(e)
            if gold.get("example_sha256") != digest or replay.get("example_sha256") != digest:
                raise ValueError(f"example {e.instance_id} content binding changed after gold admission/replay")
            if any(gold.get(k) != v for k, v in pins.items()) or pins["split_version"] != "v2":
                raise ValueError("production gold split release pin mismatch")
            if not (replay.get("ok") is True and replay.get("production") is True
                    and replay.get("mask_version") == MASK_VERSION and not replay.get("stubs")):
                raise ValueError(f"example {e.instance_id} lacks current production mask replay")
            if any(e.meta.get(k) != v for k, v in
                   (("format", FORMAT_VERSION), ("prompt", active_prompt_version()),
                    ("action_space", ACTION_SPACE_VERSION), ("dataset", DATASET_VERSION))):
                raise ValueError("production example format version mismatch")
            validate_production_lineage(gold)

    # Diagnostic rows may have been encoded before the activation switch changed.
    # Report their actual versions; a mixed dataset has no single prompt version.
    prompt_versions = sorted({e.meta["prompt"] for e in ex if "prompt" in e.meta})
    stats: dict[str, Any] = {"dataset": DATASET_VERSION, "format": FORMAT_VERSION,
        "prompt": prompt_versions[0] if len(prompt_versions) == 1 else None if prompt_versions else active_prompt_version(),
        "prompt_versions": prompt_versions, "action_space": ACTION_SPACE_VERSION,
        "dataset_kind": "production_gold" if production else MVP_GOLD if mvp else "diagnostic" if tainted else
            "pipeline_test" if any(e.meta.get('gold', {}).get('data_mode') == 'pipeline_test' for e in ex) else "unverified",
        "tainted_examples": len(tainted), "curriculum": ["n_boxes", "total_tokens", "instance_id"],
        "required_splits": sorted(required), "splits": {}, **pins}
    payloads: dict[str, str] = {}
    for split in sorted(splits):
        rows = order_by_size(e for e in ex if e.split == split)
        payloads[f"{split}.jsonl"] = "".join(json.dumps(asdict(e), separators=(",", ":"), allow_nan=False) + "\n" for e in rows)
        stats["splits"][split] = length_stats(rows)
    reports = [e.meta.get("replay", {}) for e in ex]
    epochs = sorted({e.meta.get('gold', {}).get('epoch_id') for e in ex if e.meta.get('gold', {}).get('epoch_id')})
    catalogues = sorted({(e.meta.get('gold', {}).get('required_rules') or {}).get('catalogue_sha256') for e in ex
                         if (e.meta.get('gold', {}).get('required_rules') or {}).get('catalogue_sha256')})
    stats['training_epochs'] = epochs
    stats['training_catalogue_sha256'] = catalogues
    stats["replay"] = {"examples": len(ex), "verified": sum(r.get("ok") is True for r in reports),
        "production_verified": sum(r.get("ok") is True and r.get("production") is True for r in reports),
        "d79_verified": sum(r.get("ok") is True and r.get("d79") is True for r in reports),
        "securing_verified": sum(r.get("ok") is True and bool(r.get("securing")) for r in reports)}
    payloads["stats.json"] = json.dumps(stats, indent=1, allow_nan=False) + "\n"
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".sft-stage-", dir=out) as staging:
        stage = Path(staging)
        for name, contents in payloads.items():
            (stage / name).write_text(contents)
        for name in payloads:
            if name != "stats.json":
                os.replace(stage / name, out / name)
        for split in supported - splits:
            (out / f"{split}.jsonl").unlink(missing_ok=True)
        os.replace(stage / "stats.json", out / "stats.json")
    return stats


def read_dataset(path: str | Path) -> list[SFTExample]:
    return [SFTExample(**json.loads(line)) for line in Path(path).read_text().splitlines() if line.strip()]
