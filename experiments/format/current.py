"""CPU-tokenizer sizing over manifested examples and a heterogeneous layover.

Persisted shards are loaded unchanged, SHA256 verified, with historical producer
versions recorded. These are format observations, not current-generator or full
checker certifications. No hull reconstruction, weights, GPU or network is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import resource
import time

from llm.stowlm.format import ActionCodec, load_tokenizer
from llm.stowlm.format_prompt import PROMPT_VERSION, encode_prompt
from stowage.actions.space import BoxSlotSpace, Discharge, Place, Relocate
from stowage.io import load_instance
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.moves import apply_plan
from measure import prompt_cost_split, sizing_plan
from measure_extended import top_access_sizing_plan

ROOT = Path(__file__).resolve().parents[2]
SAMPLING_LIMITS = (
    "One lowest-seed canonical shard per profile and yard generator, plus one "
    "lowest-seed noise=0.8 dense shard per generator/size; one constructed tiny "
    "heterogeneous layover with explicit relocation and discharge destination. "
    "Not all seeds/ports/noise/flag combinations, arbitrary instruction lengths, "
    "background-box populations or override frequencies. Persisted campaign 2.0.0 "
    "producer versions predate the current fix wave: current tokenizer measurements "
    "are not fresh D36/current-generator qualification. No full-checker or quality claim."
)


def load_profile(profile, generator, *, noise=None):
    manifest_path = ROOT / "experiments/instances-v2/manifest.json"
    payload = manifest_path.read_bytes()
    manifest = json.loads(payload)
    group = manifest["groups"][f"{generator}/{profile}"]
    candidates = [r for r in group["shards"] if noise is None or r["spec"]["noise"] == noise]
    row = min(candidates, key=lambda r: (r["seed"], r["instance"]))
    path = manifest_path.parent / generator / profile / row["instance"]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != row["sha256"]:
        raise ValueError(f"Canonical shard SHA256 mismatch: {path}")
    instance = load_instance(path)
    if instance.family != row["family"]:
        raise ValueError(f"Canonical shard family mismatch: {path}")
    return instance, dict(
        source=str(path.relative_to(ROOT)), source_sha256=digest,
        manifest_sha256=hashlib.sha256(payload).hexdigest(),
        campaign_version=manifest["campaign_version"], profile=profile,
        generator=generator, seed=row["seed"], noise=row["spec"]["noise"],
        source_spec=row["spec"], artifact_provenance="unchanged historical campaign shard",
    )


def heterogeneous_layover():
    """A replayable tiny observation with all special-flag families and instructions.

    APPROXIMATION: fixture geometry and cargo are synthetic test placeholders;
    this sequence establishes crane replay only, never full-checker feasibility.
    The explicit relocation clears a buried load before it is requested, and the
    discharge requires temporary off/on restowing of a through box.
    """
    data = build_instance(layover=True).model_dump(mode="json")
    ids = [c["id"] for c in data["containers"]]
    data["yard"]["stacks"] = [
        {"key": "A-001-01", "length_ft": 40, "boxes": [ids[0]]},
        {"key": "B-001-01", "length_ft": 20, "boxes": [ids[1]]},
        {"key": "A-003-01", "length_ft": 20, "boxes": [ids[2], ids[3]]},
    ]
    data["containers"][1]["dg"]["segregation_codes"] = ["SG35"]
    data["containers"][3]["oog"].update(over_width_port_m=0.2, over_length_fore_m=0.3)
    data["plan_constraints"] = [
        {"kind": "reefer_bays", "params": {"bays": [1, 3]}, "hardness": "hard",
         "source_text": "Keep reefers in bays 1 and 3"}]
    data["rule_overrides"] = {"stability.gm": {"min_gm_m": 0.4}}
    instance = Instance.model_validate(data)
    actions = [Relocate(ids[3], "A-005-01"), Discharge(ids[4], "A-006-02"),
               Place(ids[5], "0010102"), Place(ids[2], "0030202"),
               Place(ids[1], "0010282"), Place(ids[3], "0030282"), Place(ids[0], "0020182")]
    return instance, actions


def measure_instance(instance, tokenizer, metadata, *, actions=None):
    started = time.perf_counter()
    heuristic = "explicit_heterogeneous_layover" if actions is not None else "short_box_first_static_assignment"
    original_error = None
    if actions is None:
        try:
            actions, plan = sizing_plan(instance)
        except (RuntimeError, ValueError) as error:
            original_error = f"{type(error).__name__}: {error}"
            heuristic = "top_access_first_all20_fallback"
            try:
                actions, plan = top_access_sizing_plan(instance)
            except (RuntimeError, ValueError) as fallback_error:
                raise RuntimeError(f"Original: {original_error}; fallback: {fallback_error}") from fallback_error
    else:
        plan = BoxSlotSpace().expand(instance, actions)
    replay = apply_plan(instance, plan)
    if not replay.legal:
        raise ValueError(f"Move-engine replay failed: {replay.violations}")
    codec = ActionCodec(tokenizer, [c.id for c in instance.containers])
    output = codec.encode_plan(actions)
    if codec.decode_plan(output) != actions:
        raise ValueError("Action roundtrip failed")
    prompt = encode_prompt(instance, tokenizer)
    regular = [a for a in actions if not isinstance(a, Relocate)]
    relocation_cost = sum(len(codec.encode_action(a)) for a in actions if isinstance(a, Relocate))
    nominal_budget = 8 * len(instance.load_list) * 1.05 + len(codec.end_plan_tokens)
    # This supplementary event accounting is not a replacement for D18's nominal
    # load-only expectation: both results are retained, including any excess.
    observed_event_budget = 8 * len(regular) + relocation_cost + len(codec.end_plan_tokens)
    row = dict(
        **metadata, **prompt_cost_split(instance, tokenizer),
        instance_id=instance.id, stage=instance.stage, load_boxes=len(instance.load_list),
        physical_cells=len(instance.vessel.cells), prompt_tokens=len(prompt), output_tokens=len(output),
        total_sft_tokens=len(prompt) + len(output), context_tokens=131072,
        context_fit=len(prompt) + len(output) <= 131072,
        output_budget_tokens=nominal_budget, output_budget_fit=len(output) <= nominal_budget,
        observed_event_budget_tokens=observed_event_budget,
        observed_event_budget_fit=len(output) <= observed_event_budget,
        explicit_relocations=sum(isinstance(a, Relocate) for a in actions),
        actions=len(actions), expanded_lifts=len(plan.moves), move_engine_legal=True,
        full_checker_certified=False, sizing_heuristic=heuristic, original_sizing_error=original_error,
        instance_provenance=instance.provenance.model_dump(mode="json"),
        measurement_seconds=time.perf_counter() - started,
    )
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--output", default="experiments/format/current_measurements.json")
    args = parser.parse_args()
    started = time.perf_counter()
    tokenizer = load_tokenizer(ROOT / "models/glimmer-30b-4bit")
    rows = []
    failed_measurements = []
    for generator in ("random_yard", "simulated_yard"):
        for profile in ("tiny", "small", "medium", "full", "realistic_500", "realistic_1000"):
            instance, metadata = load_profile(profile, generator)
            try:
                rows.append(measure_instance(instance, tokenizer, metadata))
            except (RuntimeError, ValueError) as error:
                failed_measurements.append({**metadata, "prompt_tokens": len(encode_prompt(instance, tokenizer)),
                                            "output_tokens": None, "total_sft_tokens": None,
                                            "error": f"{type(error).__name__}: {error}",
                                            "note": "Sizing heuristic failed; no feasible output fabricated",
                                            "full_checker_certified": False})
            if args.probe:
                break
        if args.probe:
            break
    if not args.probe:
        for generator in ("random_yard", "simulated_yard"):
            for profile in ("realistic_500", "realistic_1000"):
                instance, metadata = load_profile(profile, generator, noise=0.8)
                rows.append(measure_instance(instance, tokenizer, metadata))
        instance, actions = heterogeneous_layover()
        rows.append(measure_instance(instance, tokenizer, {
            "profile": "heterogeneous_layover", "generator": "model_fixture",
            "artifact_provenance": "current synthetic fixture plus explicit observation modifications",
            "source": "experiments/format/current.py", "seed": instance.seed,
        }, actions=actions))
        # Keep original ladder sources comparable to the archived sizing study.
        for rung in ("tiny", "small", "medium", "full"):
            path = ROOT / "experiments/instances" / rung / "seed-00000001.json"
            instance = load_instance(path)
            rows.append(measure_instance(instance, tokenizer, {
                "profile": rung, "generator": "historical_ladder", "seed": instance.seed,
                "source": str(path.relative_to(ROOT)),
                "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "artifact_provenance": "unchanged historical pre-KCS ladder snapshot",
            }))
    report = dict(
        rows=rows, failed_measurements=failed_measurements, wall_seconds=time.perf_counter() - started,
        peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        cpu_cores=1, gpu=False, sampling_limits=SAMPLING_LIMITS, prompt_version=PROMPT_VERSION,
        tokenizer_sha256=hashlib.sha256((ROOT / "models/glimmer-30b-4bit/tokenizer.json").read_bytes()).hexdigest(),
        prompt_source_sha256=hashlib.sha256((ROOT / "src/llm/stowlm/format_prompt.py").read_bytes()).hexdigest(),
        sizing_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )
    (ROOT / args.output).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"rows": len(rows), "wall_seconds": report["wall_seconds"],
                      "peak_rss_bytes": report["peak_rss_bytes"]}))


if __name__ == "__main__":
    main()
