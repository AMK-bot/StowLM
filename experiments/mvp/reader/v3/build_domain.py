"""D224 common planner words, added to immutable v3 data without sealed inputs.

Grammar and independently authored paraphrase construction families have fixed
train/valid ownership. Paraphrases are author-authored, not Spark output ($0).
The original v3 train and valid byte streams are retained as prefixes, with every
original row/context unchanged. data-domain freezes after its first build.
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
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
from experiments.mvp.reader.v3 import domain_words as words
from experiments.mvp.reader.v3 import build_data as base

PRODUCTION_DATA = HERE / "data-domain"
SEED = 22403
DEFAULT_PER_FAMILY = 32
HAZARDS = (
    "corrosive", "corrosives", "toxic substances", "toxic liquids", "toxic solids",
    "flammable liquids", "flammable gas", "flammable gases", "gas", "gases",
    "explosives", "explosive substances", "oxidiser", "oxidisers", "oxidizer", "oxidizers",
    "oxidising substances", "oxidizing substances", "organic peroxides", "infectious substances",
    "radioactive material", "flammable solids", "toxic gas", "toxic gases",
)
# Every tuple is a separate construction family. Nothing from a construction
# (including any parameter/spelling variant) crosses its declared split.
# The paraphrase families were authored independently for this task, without an
# API or model-generated wording; manifests preserve that distinction.
CONSTRUCTIONS = {
    "close": (
        ("train", "grammar", "Terminal order {seqw}: prohibit all loading in bay {bayw}."),
        ("train", "authored_paraphrase", "Radio from planning, message {seqw}: take bay {bayw} out of service for loading."),
        ("valid", "grammar", "Vessel order {seqw}: loading is forbidden in bay {bayw}, effective immediately."),
        ("valid", "authored_paraphrase", "Shore message {seqw} to vessel: mark bay {bayw} unavailable to load cargo.")),
    "tiers": (
        ("train", "grammar", "Terminal order: maximum {tiersw} occupied tiers per {zone} stack in bay {bayw}."),
        ("train", "authored_paraphrase", "Radio from planning: bay {bayw}, {zone} cargo, do not stack higher than {tiersw} tiers."),
        ("valid", "grammar", "For bay {bayw}, the {zone} occupied-tier ceiling is {tiersw}."),
        ("valid", "authored_paraphrase", "Shore desk to vessel: stop each {zone} stack in bay {bayw} at {tiersw} occupied tiers.")),
    "dg_bay": (
        ("train", "grammar", "Terminal order: {hazard} require a minimum separation of {distw} bays from bay {bayw}."),
        ("train", "authored_paraphrase", "Radio from planning: do not put {hazard} within {distw} bays of bay {bayw}."),
        ("valid", "grammar", "The exclusion distance for {hazard} around bay {bayw} is at least {distw} bays."),
        ("valid", "authored_paraphrase", "Shore desk to vessel: leave a buffer of no fewer than {distw} bays between {hazard} and bay {bayw}.")),
    "dg_accommodation": (
        ("train", "grammar", "Terminal message {seqw}: {hazard} must remain at least {distw} bays away from accommodation."),
        ("train", "authored_paraphrase", "Radio message {seqw}: give the accommodation a minimum {distw}-bay clearance from {hazard}."),
        ("valid", "grammar", "Vessel order {seqw}: maintain no less than {distw} bays of separation between {hazard} and the accommodation."),
        ("valid", "authored_paraphrase", "Shore message {seqw}: leave at least {distw} bays between the living quarters and {hazard}.")),
    "hatch": (
        ("train", "grammar", "Terminal message {seqw}: require one discharge port per physical hatch cover across hold and deck, counting retained cargo."),
        ("train", "authored_paraphrase", "Radio message {seqw}: cargo already aboard and cargo to load must share one discharge destination under and over each physical cover."),
        ("valid", "grammar", "Vessel order {seqw}: at most one discharge port is permitted jointly in the hold and on deck for each physical hatch cover, including retained boxes."),
        ("valid", "authored_paraphrase", "Shore message {seqw}: do not mix discharge destinations on either side of a physical hatch cover; count both existing cargo and new cargo together.")),
    "loosening": (
        ("train", "grammar", "Terminal order {seqw}: remove the current loading ban from bay {bayw}."),
        ("train", "authored_paraphrase", "Radio from planning: bay {bayw} is already closed, but let one {hazard} container load there as an exception."),
        ("valid", "grammar", "Raise the existing {tiersw}-tier {zone} limit in bay {bayw} to {newtiersw} tiers."),
        ("valid", "authored_paraphrase", "Shore message {seqw}: disregard the active closure of bay {bayw} for this sailing and permit loading.")),
    "hedged": (
        ("train", "grammar", "Terminal draft {seqw} only: perhaps close bay {bayw}; approval is still pending."),
        ("train", "authored_paraphrase", "Radio from planning: we might need a {tiersw}-tier {zone} cap in bay {bayw}, but have not decided."),
        ("valid", "grammar", "Vessel draft {seqw}, no order authorised: the possible closure of bay {bayw} remains under discussion."),
        ("valid", "authored_paraphrase", "Shore desk to vessel: a future inspection may call for a {tiersw}-tier {zone} limit in bay {bayw}; its result is still unknown.")),
    "ambiguous_hazard": (
        ("train", "grammar", "Terminal order: keep {unclear} cargo {distw} bays from bay {bayw}; the hazard class has not been specified."),
        ("train", "authored_paraphrase", "Radio from planning: leave {distw} bays around bay {bayw} for the {unclear} goods, with the actual class still unknown."),
        ("valid", "grammar", "Require a {distw}-bay buffer from bay {bayw} for {unclear} cargo without a defined hazard class."),
        ("valid", "authored_paraphrase", "Shore desk to vessel: {unclear} is the only description supplied; give that cargo {distw} bays from bay {bayw}, but no precise class is known.")),
    "unsupported": (
        ("train", "grammar", "Terminal order: assign every {port}-bound container exclusively to bay {bayw}."),
        ("train", "authored_paraphrase", "Radio from planning: use bay {bayw} only for boxes going to {port}, and send all other destinations elsewhere."),
        ("valid", "grammar", "Allocate all cargo with discharge destination {port} to the stowage position in bay {bayw}."),
        ("valid", "authored_paraphrase", "Shore desk to vessel: make bay {bayw} the compulsory location for every box destined for {port}.")),
    "unsupported_class": (
        ("train", "grammar", "Terminal order: keep {hazard} at least {distw} bays away from bay {bayw}."),
        ("train", "authored_paraphrase", "Radio from planning: put a minimum {distw}-bay buffer between bay {bayw} and {hazard}."),
        ("valid", "grammar", "The required clearance of {hazard} from bay {bayw} is {distw} bays or more."),
        ("valid", "authored_paraphrase", "Shore desk to vessel: {hazard} must never approach bay {bayw} closer than {distw} bays.")),
    "not_an_instruction": (
        ("train", "grammar", "Terminal question for information only: what does a {tiersw}-tier {zone} cap in bay {bayw} mean? Apply no order."),
        ("train", "authored_paraphrase", "Radio from planning: I am only describing yesterday's {tiersw}-tier {zone} limit in bay {bayw}, not asking for a new restriction."),
        ("valid", "grammar", "Explain, without changing the plan, the effect of a {tiersw}-tier ceiling on bay {bayw}'s {zone}."),
        ("valid", "authored_paraphrase", "Shore desk to vessel: this is a historical note about bay {bayw} having {tiersw} {zone} tiers, with no instruction to apply.")),
}


def _frozen(output: Path) -> dict | None:
    paths = [output / name for name in ("train.jsonl", "valid.jsonl", "manifest.json")]
    if not any(path.exists() for path in paths):
        return None
    if not all(path.is_file() for path in paths):
        raise ValueError("Incomplete frozen data-domain; refusing to overwrite")
    manifest = json.loads((output / "manifest.json").read_text())
    if set(manifest.get("files", {})) != {"train.jsonl", "valid.jsonl"}:
        raise ValueError("Invalid frozen data-domain manifest")
    for name, metadata in manifest["files"].items():
        if base.sha(output / name) != metadata.get("sha256"):
            raise ValueError(f"Frozen data-domain integrity mismatch: {name}")
    return manifest


def _answer(intent: str, values: dict) -> dict:
    refusal = {"loosening": "loosening", "hedged": "ambiguous", "ambiguous_hazard": "ambiguous",
               "unsupported": "unsupported", "unsupported_class": "unsupported", "not_an_instruction": "not_an_instruction"}
    if intent in refusal:
        code = refusal[intent]
        return {"schema_version": base.SCHEMA_VERSION, "refusal": {
            "refused": True, "code": code, "reason": base.REFUSAL_REASONS[code]}}
    if intent == "close":
        constraint = {"type": "close_bay", "bay": values["bay"]}
    elif intent == "tiers":
        constraint = {"type": "max_tiers", "bay": values["bay"], "zone": values["zone"], "max_tiers": values["tiers"]}
    elif intent == "hatch":
        constraint = {"type": "one_port_per_hatch"}
    else:
        away = {"kind": "bay", "bay": values["bay"]} if intent == "dg_bay" else {"kind": "accommodation"}
        constraint = {"type": "dg_keep_away", "imdg_class": values["cls"], "min_bays": values["distance"], "away_from": away}
    return {"schema_version": base.SCHEMA_VERSION, "constraint": constraint}


def build(output: Path = PRODUCTION_DATA, per_family: int = DEFAULT_PER_FAMILY) -> dict:
    output = output.resolve()
    if not output.is_relative_to(HERE) or output.is_relative_to(HERE / "data"):
        raise ValueError("Domain output must stay inside v3 and outside immutable original data/")
    if not 1 <= per_family <= 64:
        raise ValueError("Domain family quota must be 1..64")
    if output == PRODUCTION_DATA.resolve():
        if per_family != DEFAULT_PER_FAMILY:
            raise ValueError("Fixture quotas cannot write production data-domain")
        frozen = _frozen(output)
        if frozen is not None:
            return frozen
    inputs = {split: HERE / "data" / f"{split}.jsonl" for split in ("train", "valid")}
    original_bytes = {split: path.read_bytes() for split, path in inputs.items()}
    if any(not content.endswith(b"\n") for content in original_bytes.values()):
        raise ValueError("Original v3 data must end with newline for exact prefix preservation")
    original = {split: base.rows(path) for split, path in inputs.items()}
    contexts = list({json.dumps(row["context"], sort_keys=True): row["context"] for row in original["train"]}.values())
    seen = {base.fold(row["sentence"]) for group in original.values() for row in group}
    additions = {"train": [], "valid": []}
    families = []
    rng = random.Random(SEED)
    for intent, constructions in CONSTRUCTIONS.items():
        for split, style, template in constructions:
            family = f"v3.domain.d224.{intent}.{split}.{style}"
            selected = []
            for attempt in range(4000):
                context = deepcopy(rng.choice(contexts))
                bay = min(context["bays"]) if attempt == 0 else rng.choice(context["bays"])
                zone = ("deck", "hold")[attempt % 2]
                cap = context.get("max_tiers_by_bay", {}).get(str(bay), {}).get(zone, context["max_tiers"][zone])
                tiers = rng.randrange(cap)  # room for explicit +1 loosening cases
                hazard = HAZARDS[attempt % len(HAZARDS)]
                cls = words.ALIAS_CLASSES[hazard]
                allowed = sorted(set(words.ALIAS_CLASSES.values()))
                if intent == "unsupported_class":
                    allowed.remove(cls)
                context["allowed_imdg_classes"] = allowed
                distance = 1 + attempt % 6
                values = {"bay": bay, "bayw": words.number_words(bay), "zone": zone,
                          "tiers": tiers, "tiersw": words.number_words(tiers), "newtiersw": words.number_words(tiers + 1),
                          "distance": distance, "distw": words.number_words(distance), "hazard": hazard, "cls": cls,
                          "unclear": ("toxic", "flammable")[attempt % 2],
                          "port": rng.choice(context["ports"]), "seqw": words.number_words(1 + attempt % 99)}
                sentence = template.format(**values)
                if base.fold(sentence) in seen:
                    continue
                answer = _answer(intent, values)
                base.validate_answer(json.dumps(answer), context)
                if answer.get("constraint", {}).get("type") == "dg_keep_away" and not words.class_is_allowed(cls, context):
                    raise ValueError("Authored positive class is outside its explicit context")
                identifier = hashlib.sha256((family + "\0" + sentence + "\0" + json.dumps(context, sort_keys=True)).encode()).hexdigest()[:16]
                row = {"id": "reader-v3-domain-" + identifier, "family": family, "source": "authored_d224_domain",
                       "source_kind": "deterministic_intent_authored" if style == "grammar" else "independently_authored_paraphrase",
                       "intent": "hedged" if intent == "ambiguous_hazard" else "unsupported" if intent == "unsupported_class" else intent,
                       "domain_intent": intent, "category": answer.get("refusal", {}).get("code", answer.get("constraint", {}).get("type")),
                       "split": split, "context": context, "sentence": sentence, "expected": answer,
                       "is_loosening": intent == "loosening", "provenance": {"decision": "D224", "construction": family,
                           "wording_author": "Codex task author", "paid_model": None, "spark_spend_usd": 0},
                       "messages": base.messages(sentence, context) + [{"role": "assistant", "content": json.dumps(answer, separators=(",", ":"))}]}
                selected.append(row)
                seen.add(base.fold(sentence))
                if len(selected) == per_family:
                    break
            if len(selected) != per_family:
                raise ValueError(f"Insufficient domain surfaces: {family}: {len(selected)}/{per_family}")
            additions[split].extend(selected)
            families.append({"family": family, "split": split, "style": style, "intent": intent,
                             "rows": len(selected), "template": template,
                             "source": "D224 task-author grammar" if style == "grammar" else "D224 task-author paraphrase; no Spark call"})
    combined = {split: original[split] + additions[split] for split in original}
    assert not ({r["family"] for r in combined["train"]} & {r["family"] for r in combined["valid"]})
    assert not ({base.fold(r["sentence"]) for r in combined["train"]} & {base.fold(r["sentence"]) for r in combined["valid"]})
    output.mkdir(parents=True, exist_ok=True)
    for split in original:
        extra = "".join(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n" for row in additions[split]).encode()
        (output / f"{split}.jsonl").write_bytes(original_bytes[split] + extra)
    manifest = {"schema_version": base.SCHEMA_VERSION, "generator_version": "reader-data/3-domain-d224", "seed": SEED,
                "generator_sha256": base.sha(Path(__file__)), "domain_words_sha256": base.sha(Path(words.__file__)),
                "input_files": {str(path.relative_to(HERE)): base.sha(path) for path in inputs.values()},
                "decision_evidence": ["docs/decisions.md#D224", "experiments/review/v5/logic-story.md#P1-2"],
                "public_vocabulary_sources": list(words.SOURCES), "families": families,
                "new_rows": {split: len(rows) for split, rows in additions.items()},
                "files": {f"{split}.jsonl": {"rows": len(rows), "sha256": base.sha(output / f"{split}.jsonl"),
                          "intents": dict(sorted(Counter(row["intent"] for row in rows).items()))}
                          for split, rows in combined.items()}, "spark_spend_usd": 0,
                "policy": ["Original v3 data/ is immutable and is the only sentence input; no sealed/test/family-test inputs.",
                           "Original training and validation byte streams preserved as exact prefixes; original contexts unchanged.",
                           "D224 broadens augmentation beyond historical validation errors to public domain words and spoken numbers.",
                           "Grammar and task-author paraphrase construction families have fixed disjoint train/valid ownership.",
                           "No Spark paraphrases were acquired; all new wording is task-author-authored, charged $0.",
                           "The review's exact hero sentence is excluded; no template reproduces that construction.",
                           "Bare toxic/flammable remain ambiguous; exact aliases never expand class/division scope.",
                           "New contexts declare allowed_imdg_classes; unsupported_class examples intentionally name an excluded class.",
                           "Production data-domain is immutable after first successful build; no upstream rescan on rerun."]}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PRODUCTION_DATA)
    parser.add_argument("--per-family", type=int, default=DEFAULT_PER_FAMILY)
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.per_family)["files"], indent=2))
