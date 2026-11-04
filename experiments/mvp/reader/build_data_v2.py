"""D219 deterministic CPU-only data refresh; never opens a sealed artifact.

Targets are authored from intent, never inferred/repaired by a reader. The only
error inputs are checks/*/valid-sample.json and an optional family-test result.
One family owns every surface variant of an authored construction.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from schema import REFUSAL_REASONS, SCHEMA_VERSION, messages, validate_answer

SEED = 219
# Each tuple is one construction family; all its variants remain train-only.
# old/new values are explicit, making direction independent of unseen state.
FAMILIES = {
    "loosen.cap_increase": ("loosening", 160, [
        "Tighten the deck cap in bay {bay} to {new} tiers, replacing the existing {old}-tier ceiling.",
        "Impose a strict maximum of {new} tiers on deck in bay {bay}; its current cap is {old}."]),
    "loosen.hold_increase": ("loosening", 160, [
        "Restrict bay {bay}'s hold to {new} tiers instead of the currently allowed {old} tiers.",
        "Set a firm hold height limit of {new} tiers in bay {bay}, up from the current {old}."]),
    "loosen.buffer_reduce": ("loosening", 160, [
        "Enforce a minimum {old}-bay gap between class {cls} and bay {bay}, replacing the existing {new}-bay minimum.",
        "Keep class {cls} at least {old} bays from bay {bay}; reduce its current {new}-bay exclusion distance to that."]),
    "loosen.house_reduce": ("loosening", 160, [
        "For safety, require class {cls} to stay {old} bays from accommodation rather than the current {new}-bay minimum.",
        "Apply a strict {old}-bay accommodation buffer for class {cls}, cutting the existing {new}-bay buffer."]),
    "loosen.exception": ("loosening", 160, [
        "Close bay {bay} to all cargo except class {cls}, which may load despite the existing closure.",
        "Maintain bay {bay}'s closure, but permit one container to load there as an exception."]),
    "loosen.partial_hatch": ("loosening", 160, [
        "Keep one discharge port under every hatch cover, but remove the existing one-port restriction from its deck.",
        "Require one discharge port on each cover's deck, while waiving the existing single-port rule below it."]),
    "loosen.mix_tighten": ("loosening", 160, [
        "Limit deck stacks in bay {bay} to {old} tiers and suspend the DG keep-away restrictions for class {cls}.",
        "Close bay {bay} and authorize other bays to ignore their current tier limits."]),
    "loosen.restore_permission": ("loosening", 160, [
        "Restrict loading to the usual vessel limits; cancel the extra closure on bay {bay}.",
        "Retain all standard rules and withdraw the special class {cls} exclusion around bay {bay}."]),
    "hedge.unapproved": ("hedged", 120, [
        "Draft proposal, not approved: stop loading bay {bay}; await approval before applying it.",
        "The suggested closure of bay {bay} is still awaiting authorization; no instruction has been issued."]),
    "hedge.conditional_unknown": ("hedged", 120, [
        "If the inspection later calls for it, close bay {bay}; the inspection outcome is not known.",
        "A future survey might require a {old}-tier deck limit in bay {bay}; this is undecided."]),
    "hedge.question_choice": ("hedged", 120, [
        "Should we close bay {bay} or leave it available? No choice has been made.",
        "We have not chosen whether to limit bay {bay}'s deck to {old} tiers or a different number."]),
    "hedge.zone_absent": ("hedged", 120, [
        "Put a {old}-tier maximum in bay {bay}; I cannot say whether this means deck or hold.",
        "Bay {bay} needs a {old}-tier cap in one zone, but the zone has not been selected."]),
    "hedge.distance_absent": ("hedged", 120, [
        "Keep class {cls} some distance from bay {bay}; the minimum distance is not specified.",
        "Require an accommodation clearance for class {cls}, with its distance still to be decided."]),
    "hedge.scope_absent": ("hedged", 120, [
        "Shut the bay the surveyor meant; its number has not been supplied.",
        "Use one discharge destination for the hatch cover we discussed; I cannot identify which cover."]),
    "positive.exposed_deck": ("deck", 90, [
        "For exposed deck cargo in bay {bay}, the permitted stack height is {old} tiers maximum.",
        "Weather deck stowage in bay {bay}: enforce a stack ceiling of {old} occupied tiers."]),
    "positive.hold_distinction": ("hold", 90, [
        "For cargo inside the hold of bay {bay}, the permitted stack height is {old} tiers maximum.",
        "Enclosed hold stowage in bay {bay}: enforce a stack ceiling of {old} occupied tiers."]),
    "positive.zero_deck": ("deck_zero", 90, [
        "Apply a zero-tier maximum exclusively to deck stacks in bay {bay}.",
        "For bay {bay}, set deck occupied-tier capacity to zero; this instruction concerns only deck stacks."]),
    "positive.global_zone": ("deck_all", 90, [
        "Set a vessel-wide occupied stack-height ceiling of {old} tiers for exposed deck cargo.",
        "Shipwide weather deck stowage shall have a maximum of {old} occupied tiers per stack."]),
    "unsupported.destination_assignment": ("unsupported", 80, [
        "Assign all cargo destined for {port} to bay {bay}.",
        "Stow only {port}-bound containers in bay {bay} and send other destinations elsewhere."]),
    "unsupported.bad_reference": ("unsupported", 80, [
        "Apply an immediate loading prohibition to bay 999, which is absent from this vessel.",
        "Deck cargo in nonexistent bay 999 is limited to {old} tiers."]),
    "unsupported.bad_range": ("unsupported", 80, [
        "For deck stacks in bay {bay}, impose a negative height maximum of -2 tiers.",
        "Put a strict {bad}-tier ceiling on bay {bay}'s deck, above its physical tier capacity."]),
    "comment.information": ("not_an_instruction", 60, [
        "Please explain how a deck maximum of {old} tiers would affect bay {bay}; I am requesting information only.",
        "The archived loading report mentions bay {bay}; this sentence describes the report only."]),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def fold(sentence):
    return " ".join(sentence.casefold().split())


def target(kind, v):
    if kind in ("loosening", "hedged", "unsupported", "not_an_instruction"):
        code = "ambiguous" if kind == "hedged" else kind
        return {"schema_version": SCHEMA_VERSION, "refusal": {
            "refused": True, "code": code, "reason": REFUSAL_REASONS[code]}}
    return {"schema_version": SCHEMA_VERSION, "constraint": {
        "type": "max_tiers", "bay": None if kind == "deck_all" else v["bay"],
        "zone": "hold" if kind == "hold" else "deck",
        "max_tiers": 0 if kind == "deck_zero" else v["old"]}}


def error_evidence():
    evidence = []
    for path in sorted((HERE / "checks").glob("*/valid-sample.json")):
        doc = json.loads(path.read_text())
        evidence.append({"path": str(path.relative_to(HERE)), "sha256": sha(path),
                         "count": doc["count"], "exact_match_count": doc["exact_match_count"],
                         "failed_families": {f: d for f, d in doc["by_family"].items()
                                             if d["exact_match_count"] < d["count"]}})
    path = HERE / "results/family-test-tuned.json"
    if path.exists():
        doc = json.loads(path.read_text())
        evidence.append({"path": str(path.relative_to(HERE)), "sha256": sha(path),
                         "failed_families": {f: d for f, d in doc["by_family"].items()
                                             if d["exact_match_count"] < d["count"]}})
    return evidence


def build(output: Path, base_per_family: int = 8, augmentation_scale: float = .55):
    if base_per_family < 1 or not 0 < augmentation_scale <= 1:
        raise ValueError("positive family caps and 0 < augmentation_scale <= 1 required")
    rng = random.Random(SEED)
    base = {split: rows(HERE / "data" / f"{split}.jsonl") for split in ("train", "valid", "test")}
    families = defaultdict(list)
    for row in base["train"]:
        families[row["family"]].append(row)
    train, family_manifest = [], []
    for name, candidates in sorted(families.items()):
        candidates.sort(key=lambda r: hashlib.sha256((str(SEED) + r["id"]).encode()).hexdigest())
        selected = deepcopy(candidates[:base_per_family])
        train.extend(selected)
        family_manifest.append({"family": name, "split": "train", "rows": len(selected),
                                "source": "retained_v1_train", "original_rows": len(candidates)})
    contexts = {json.dumps(r["context"], sort_keys=True): r["context"] for r in base["train"]}
    seen = {fold(r["sentence"]) for split in ("train", "valid", "test") for r in base[split]}
    for family, (kind, limit, templates) in FAMILIES.items():
        name = "d219." + family
        candidates = {}
        for context_key, context in sorted(contexts.items()):
            for bay in context["bays"]:
                cap = min(context["max_tiers"].values())
                cap = min(cap, *context.get("max_tiers_by_bay", {}).get(str(bay), context["max_tiers"]).values())
                for old in range(cap):
                    for cls in ("1.4", "2.1", "3", "4.3", "5.1", "6.1", "8", "9"):
                        # old/new compare explicit existing restrictions; DG old
                        # must be >=1, tier old can be zero and new stays physical.
                        if family.startswith("loosen.") and "reduce" in family and old == 0:
                            continue
                        v = {"bay": bay, "old": old, "new": old + 1, "cls": cls,
                             "bad": context["max_tiers"]["deck"] + 2,
                             "port": context["ports"][(bay + old) % len(context["ports"])]}
                        answer = target(kind, v)
                        validate_answer(json.dumps(answer), context)
                        for template in templates:
                            sentence = template.format(**v)
                            if fold(sentence) in seen:
                                continue
                            key = sentence + "\0" + context_key
                            candidates[key] = (sentence, context, answer)
        ordered = sorted(candidates)
        rng.shuffle(ordered)
        selected = []
        for key in ordered[:max(1, int(limit * augmentation_scale))]:
            sentence, context, answer = candidates[key]
            identifier = hashlib.sha256((name + "\0" + key).encode()).hexdigest()[:16]
            category = answer.get("refusal", {}).get("code", "max_tiers")
            row = {"id": "reader-v2-" + identifier, "family": name, "source": "authored_d219",
                   "source_kind": "deterministic_intent_authored", "intent": "deck" if kind == "deck_zero" else kind,
                   "category": category, "split": "train", "context": context, "sentence": sentence,
                   "expected": answer, "is_loosening": kind == "loosening",
                   "messages": messages(sentence, context) + [{"role": "assistant", "content": json.dumps(answer, separators=(",", ":"))}]}
            selected.append(row)
        train.extend(selected)
        seen.update(fold(r["sentence"]) for r in selected)
        family_manifest.append({"family": name, "split": "train", "rows": len(selected),
                                "source": "authored_d219", "intent": kind, "templates": templates})
    rng.shuffle(train)
    output.mkdir(parents=True, exist_ok=True)
    (output / "train.jsonl").write_text("".join(json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n" for r in train))
    for split in ("valid", "test"):
        (output / f"{split}.jsonl").write_bytes((HERE / "data" / f"{split}.jsonl").read_bytes())
    splits = {"train": train, "valid": base["valid"], "test": base["test"]}
    for left, right in (("train", "valid"), ("train", "test"), ("valid", "test")):
        assert not ({r["family"] for r in splits[left]} & {r["family"] for r in splits[right]})
        assert not ({fold(r["sentence"]) for r in splits[left]} & {fold(r["sentence"]) for r in splits[right]})
    manifest = {
        "schema_version": SCHEMA_VERSION, "generator_version": "reader-data/2-d219", "seed": SEED,
        "generator_sha256": sha(Path(__file__)), "base_rows_per_family": base_per_family,
        "augmentation_scale": augmentation_scale,
        "files": {f"{split}.jsonl": {"rows": len(data), "sha256": sha(output / f"{split}.jsonl"),
                    "intents": dict(sorted(Counter(r["intent"] for r in data).items())),
                    "categories": dict(sorted(Counter(r["category"] for r in data).items()))} for split, data in splits.items()},
        "input_files": {str((HERE / "data" / f"{split}.jsonl").relative_to(HERE)): sha(HERE / "data" / f"{split}.jsonl") for split in base},
        "families": family_manifest, "error_evidence": error_evidence(),
        "targeting": {
            "latest_valid_failures": ["grammar.deck.06: weather-deck misread as hold", "grammar.unsupported.06: destination reservation misread as closure/hatch"],
            "earlier_valid_failures": ["hedged", "not_an_instruction", "out_of_range", "wrong_bay", "loosening"],
            "family_test": "Included if a completed family-test-tuned artifact exists at build time; never uses sealed results.",
            "coverage": "New families cover all observed failing intents; safety mix raised independently by the D219 instruction."},
        "spark_v2_spend_usd": 0,
        "policy": ["No sealed artifact is opened, hashed, compared or used by this builder.",
                   "All authored variants of a construction share one family and are train-only; no validation sentence or template is moved into train.",
                   "Valid and family-test sets preserved byte-for-byte; original training families uniformly capped to retain coverage within the three-hour epoch budget.",
                   "Original synthetic training rows retained as rehearsal; additions target only valid/family errors plus the explicitly requested safety categories.",
                   "Labels are authored from intended semantics and checked for schema validity; no parser labels, model corrections or output filters.",
                   "Explicit old/new restriction values establish loosening without guessing current operational state.",
                   "All data synthetic; no external API requests or new Spark paraphrase charges."],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=HERE / "data-v2")
    parser.add_argument("--base-per-family", type=int, default=8)
    parser.add_argument("--augmentation-scale", type=float, default=.55)
    args = parser.parse_args()
    manifest = build(args.output, args.base_per_family, args.augmentation_scale)
    print(json.dumps(manifest["files"], indent=2))
