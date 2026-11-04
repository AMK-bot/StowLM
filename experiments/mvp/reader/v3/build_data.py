"""Validation-error-only reader v3 augmentation; never opens any test/sealed set.

Retain v2 training as rehearsal and copy v2 validation byte-for-byte. New labels
are intent-authored; each new construction cites failed validation IDs, never a
prediction-derived label or a held-out sentence copied into training.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import sys

HERE = Path(__file__).resolve().parent
READER = HERE.parent
PRODUCTION_DATA = HERE / "data"
sys.path.insert(0, str(READER))
from schema import REFUSAL_REASONS, SCHEMA_VERSION, messages, validate_answer

SEED = 22003
# name: (target intent, validation error intent, maximum new rows, surfaces)
# All variants of every construction stay train-only. No original grammar is
# imported, no held-out template is paraphrased, and no model supplies targets.
FAMILIES = {
    "loosen.exception": ("loosening", "loosening", 64, (
        "Bay {bay} remains closed, but grant a loading exception for class {cls}.",
        "Despite the closure already imposed on bay {bay}, authorize one extra box there.")),
    "loosen.cap_increase": ("loosening", "loosening", 64, (
        "Use a strict deck maximum of {tier_new} tiers in bay {bay}, replacing its existing {tier_old}-tier maximum.",
        "The current hold limit in bay {bay} is {tier_old} tiers; raise that limit to {tier_new}.")),
    "loosen.distance_reduce": ("loosening", "loosening", 64, (
        "Class {cls} currently needs {dist_old} bays of clearance from bay {bay}; reduce that minimum to {dist_new}.",
        "Enforce {dist_new} bays between class {cls} and accommodation instead of the existing {dist_old}-bay exclusion.")),
    "loosen.reopen": ("loosening", "loosening", 64, (
        "Cancel the extra prohibition on loading bay {bay} and reopen it for cargo.",
        "Withdraw the current closure of bay {bay}; loading there is permitted again.",
        "Remove bay {bay}'s existing no-loading restriction and restore normal loading permission.",
        "Bay {bay} is closed under the current plan; rescind that closure for this loading operation.")),
    "loosen.mixed": ("loosening", "loosening", 64, (
        "Close bay {bay}, and waive the existing accommodation exclusion for class {cls}.",
        "Cap bay {bay}'s deck at {tier_old} tiers, but allow class {cls} to ignore its current DG separation rule.")),
    "loosen.temporary": ("loosening", "loosening", 64, (
        "For this sailing only, suspend the existing ban on cargo in bay {bay}.",
        "Make a temporary exception to the current {tier_old}-tier deck limit in bay {bay} and permit {tier_new} tiers.")),
    "loosen.partial_hatch": ("loosening", "loosening", 64, (
        "In bay {bay}, retain the current single-port rule below hatch covers but lift that rule on deck.",
        "For bay {bay}, keep one destination on deck but waive the existing single-destination restriction below the covers.",
        "Exempt deck cargo in bay {bay} from the current rule requiring one discharge port jointly above and below each cover.",
        "The single-port-per-cover rule currently applies in bay {bay}; suspend it for hold cargo while retaining it on deck.")),
    "loosen.unconditional_permission": ("loosening", "loosening", 64, (
        "Authorize class {cls} to load in bay {bay} even when its existing keep-away restriction forbids it.",
        "Permit an override of the existing loading closure in bay {bay}; all other limits remain.")),
    "hedge.pending": ("hedged", "hedged", 64, (
        "Bay {bay} may need to be closed, but the decision is pending and this is not an approved order.",
        "A {tier_old}-tier deck limit in bay {bay} is only a proposal awaiting authorization.")),
    "hedge.conditional": ("hedged", "hedged", 64, (
        "If a future inspection finds damage, close bay {bay}; the inspection has not happened.",
        "Depending on an undecided survey result, we might cap bay {bay}'s hold at {tier_old} tiers.")),
    "hedge.alternative": ("hedged", "hedged", 64, (
        "We are still deciding between closing bay {bay} and leaving it open; neither option is selected.",
        "Choose later whether bay {bay}'s deck limit should be {tier_old} or {tier_new} tiers; no limit is ordered yet.")),
    "hedge.zone_missing": ("hedged", "hedged", 64, (
        "Limit bay {bay} to {tier_old} tiers, but the affected zone has not been identified.",
        "The proposed {tier_old}-tier cap for bay {bay} does not specify deck versus hold.")),
    "hedge.distance_missing": ("hedged", "hedged", 64, (
        "Keep class {cls} clear of bay {bay}, with the required number of bays still undecided.",
        "Class {cls} needs an accommodation buffer, but nobody has specified its minimum distance.")),
    "hedge.tentative": ("hedged", "hedged", 64, (
        "Perhaps leave bay {bay} unavailable for loading; this suggestion is not final.",
        "I am considering a {tier_old}-tier deck ceiling in bay {bay}, without issuing an instruction.")),
    "deck.exposed": ("deck", "deck", 64, (
        "For bay {bay}, impose a maximum of {tier_old} occupied tiers per exposed weather-deck stack.",
        "Bay {bay}: each above-hatch deck stack may occupy at most {tier_old} tiers.")),
    "unsupported.destination": ("unsupported", "unsupported", 64, (
        "Reserve bay {bay} exclusively for containers with discharge destination {port}.",
        "Allocate every {port}-bound container to bay {bay}; make that its mandatory stowage location.")),
    "unsupported.range": ("unsupported", "out_of_range", 64, (
        "Set bay {bay}'s deck height ceiling to {bad} tiers, exceeding the vessel's physical maximum.",
        "Limit bay {bay}'s hold to minus {tier_new} tiers.")),
    "unsupported.reference": ("unsupported", "wrong_bay", 64, (
        "Close bay {absent}; that exact bay number is absent from the vessel list.",
        "Set a {tier_old}-tier deck cap for nonexistent bay {absent}, without substituting another bay.")),
    "comment.question": ("not_an_instruction", "not_an_instruction", 32, (
        "For information only, what would a {tier_old}-tier deck limit mean in bay {bay}? Do not apply a limit.",
        "The old report describes bay {bay} carrying {tier_old} tiers; I am only recounting that report.")),
    "hatch.cover_scope": ("hatch", "hatch", 3, (
        "For each physical hatch cover, allow at most one discharge port across cargo above and below that cover, counting retained cargo.",
        "Enforce a single discharge destination per physical cover jointly over its hold and deck cargo, including containers already aboard.",
        "Across both sides of every hatch cover, all retained and newly loaded cargo must share one discharge destination.")),
    "hold.global": ("hold_all", "hold_all", 6, (
        "Throughout the vessel, occupied tiers per hold stack must not exceed {tier_old}.",
        "Apply a shipwide {tier_old}-tier maximum to each below-deck hold stack.")),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def fold(sentence: str) -> str:
    return " ".join(sentence.casefold().split())


def evidence_for(valid: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """Allowlist only validation summaries and require all detail IDs in valid."""
    by_id = {row["id"]: row for row in valid}
    evidence, by_intent = [], {}
    for folder in ("checks", "checks-v2"):
        for path in sorted((READER / folder).glob("*/valid-sample.json")):
            report = json.loads(path.read_text())
            details = report.get("details", [])
            if any(detail["id"] not in by_id for detail in details):
                raise ValueError(f"Validation report contains a non-validation ID: {path}")
            failures = [detail for detail in details if not detail.get("exact", False)]
            evidence.append({"path": str(path.relative_to(READER)), "sha256": sha(path),
                             "count": report["count"], "failed_ids": [d["id"] for d in failures]})
            for detail in failures:
                original = by_id[detail["id"]]
                anchor = {"path": str(path.relative_to(READER)), "sha256": sha(path),
                          "id": original["id"], "family": original["family"],
                          "intent": original["intent"], "malformed": detail.get("malformed", False)}
                by_intent.setdefault(original["intent"], []).append(anchor)
    return evidence, by_intent


def answer_for(intent: str, values: dict) -> dict:
    if intent in {"loosening", "hedged", "unsupported", "not_an_instruction"}:
        code = "ambiguous" if intent == "hedged" else intent
        return {"schema_version": SCHEMA_VERSION, "refusal": {
            "refused": True, "code": code, "reason": REFUSAL_REASONS[code]}}
    if intent == "hatch":
        constraint = {"type": "one_port_per_hatch"}
    else:
        constraint = {"type": "max_tiers", "bay": None if intent == "hold_all" else values["bay"],
                      "zone": "hold" if intent == "hold_all" else "deck", "max_tiers": values["tier_old"]}
    return {"schema_version": SCHEMA_VERSION, "constraint": constraint}


def build(output: Path = HERE / "data", per_family: int | None = None) -> dict:
    output = output.resolve()
    if not output.is_relative_to(HERE):
        raise ValueError("v3 data output must stay under the v3 scope fence")
    if per_family is not None and per_family < 1:
        raise ValueError("per_family must be positive")
    if output == PRODUCTION_DATA.resolve():
        if per_family is not None:
            raise ValueError("Fixture quotas cannot write the production v3/data directory")
        # V2 can keep producing validation checks after this contingency is
        # prepared. An ordinary repeat invocation must reuse the frozen corpus,
        # not absorb those newer errors or rewrite training under the same name.
        paths = [output / name for name in ("train.jsonl", "valid.jsonl", "manifest.json")]
        if any(path.exists() for path in paths):
            if not all(path.is_file() for path in paths):
                raise ValueError("Incomplete frozen v3/data; refusing to overwrite production artifacts")
            frozen = json.loads((output / "manifest.json").read_text())
            if set(frozen.get("files", {})) != {"train.jsonl", "valid.jsonl"}:
                raise ValueError("Invalid frozen v3/data manifest; refusing to overwrite")
            for name, metadata in frozen["files"].items():
                if sha(output / name) != metadata.get("sha256"):
                    raise ValueError(f"Frozen v3/data integrity mismatch: {name}; refusing to overwrite")
            return frozen
    # Deliberately no test input, glob of data files, v2 builder import, or sealed
    # deduplication: this code cannot consume those protected artifacts.
    train_path, valid_path = READER / "data-v2/train.jsonl", READER / "data-v2/valid.jsonl"
    base, valid = rows(train_path), rows(valid_path)
    evidence, by_intent = evidence_for(valid)
    contexts = list({json.dumps(row["context"], sort_keys=True): row["context"] for row in base}.values())
    seen = {fold(row["sentence"]) for row in base + valid}
    train = deepcopy(base)
    rng = random.Random(SEED)
    families = []
    for name, (intent, error_intent, quota, templates) in FAMILIES.items():
        anchors = by_intent.get(error_intent, [])
        if not anchors:
            raise ValueError(f"No failed validation evidence for {name}: {error_intent}")
        count = min(quota, per_family) if per_family else quota
        selected = []
        for _ in range(20000):
            context = rng.choice(contexts)
            bay = rng.choice(context["bays"])
            caps = context.get("max_tiers_by_bay", {}).get(str(bay), context["max_tiers"])
            physical = min(caps.values())
            old = rng.randrange(physical)
            old_distance = rng.randint(2, 12)
            values = {"bay": bay, "tier_old": old, "tier_new": old + 1,
                      "dist_old": old_distance, "dist_new": old_distance - 1,
                      "cls": rng.choice(("1.4", "2.1", "3", "4.3", "5.1", "6.1", "8", "9")),
                      "port": rng.choice(context["ports"]), "bad": context["max_tiers"]["deck"] + 2,
                      "absent": max(context["bays"]) + rng.randint(100, 200)}
            sentence = rng.choice(templates).format(**values)
            if fold(sentence) in seen:
                continue
            answer = answer_for(intent, values)
            validate_answer(json.dumps(answer), context)
            identifier = hashlib.sha256((name + "\0" + sentence + "\0" + json.dumps(context, sort_keys=True)).encode()).hexdigest()[:16]
            row = {"id": "reader-v3-" + identifier, "family": "v3.validation." + name,
                   "source": "authored_v3_validation_errors", "source_kind": "deterministic_intent_authored",
                   "intent": intent, "category": answer.get("refusal", {}).get("code", answer.get("constraint", {}).get("type")),
                   "split": "train", "context": context, "sentence": sentence, "expected": answer,
                   "is_loosening": intent == "loosening", "validation_error_refs": anchors,
                   "messages": messages(sentence, context) + [{"role": "assistant", "content": json.dumps(answer, separators=(",", ":"))}]}
            selected.append(row)
            seen.add(fold(sentence))
            if len(selected) == count:
                break
        if len(selected) != count:
            raise ValueError(f"Insufficient unique surfaces for {name}: {len(selected)}/{count}")
        train.extend(selected)
        families.append({"family": "v3.validation." + name, "rows": len(selected),
                         "intent": intent, "error_intent": error_intent, "validation_error_refs": anchors})
    rng.shuffle(train)
    assert not ({row["family"] for row in train} & {row["family"] for row in valid})
    assert not ({fold(row["sentence"]) for row in train} & {fold(row["sentence"]) for row in valid})
    output.mkdir(parents=True, exist_ok=True)
    (output / "train.jsonl").write_text("".join(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n" for row in train))
    (output / "valid.jsonl").write_bytes(valid_path.read_bytes())
    manifest = {"schema_version": SCHEMA_VERSION, "generator_version": "reader-data/3-validation-only",
                "seed": SEED, "generator_sha256": sha(Path(__file__)), "new_rows": len(train) - len(base),
                "input_files": {str(path.relative_to(READER)): sha(path) for path in (train_path, valid_path)},
                "files": {f"{split}.jsonl": {"rows": len(data), "sha256": sha(output / f"{split}.jsonl"),
                          "intents": dict(sorted(Counter(row["intent"] for row in data).items()))}
                          for split, data in (("train", train), ("valid", valid))},
                "families": families, "error_evidence": evidence, "spark_spend_usd": 0,
                "policy": ["Only data-v2 train/valid and checks or checks-v2 validation summaries are inputs.",
                           "V2 training retained as rehearsal; every new construction is anchored to actual validation failures.",
                           "Validation preserved byte-for-byte; no validation sentence is copied into new training.",
                           "Labels are intent-authored and schema-validated; predictions do not supply targets.",
                           "No sealed, test, family-test or test-v3 input is opened, hashed, or compared.",
                           "All variants of a new construction are train-only; no API charges."]}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "data")
    parser.add_argument("--per-family", type=int, help="Optional small fixture quota; omit for production data.")
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.per_family)["files"], indent=2))
