"""Deterministic, family-disjoint synthetic instruction SFT data (CPU only).

Does not open sealed.jsonl. Every expected answer is constructed directly from
the grammar's intent, never labelled by the rule parser or by a language model.
Real values mean current public/synthetic fleet topology, not company records.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import random
import sys
import tomllib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
from schema import (SCHEMA_VERSION, REFUSAL_REASONS, messages, validate_answer,
                    context_from_instance, system_prompt)

SEED = 199
DATA = HERE / "data"
CLASSES = ("1", "1.4", "2", "2.1", "2.2", "2.3", "3", "4.1", "4.2", "4.3", "5.1", "5.2", "6.1", "8", "9")
PREFIXES = ("", "For this loading call, ", "Planner instruction: ", "Terminal restriction: ")
SUFFIXES = ("", " This is mandatory.", " Apply this throughout loading.")

# Template identity is the paraphrase-family boundary, before any values or
# wrappers are filled. Indices 0..5 train; 6 validation; 7 test, for EVERY intent.
GRAMMAR = {
    "close": ["Close bay {bay}.", "No loading into bay {bay}.", "Nothing goes into bay {bay}.",
              "Take bay {bay} out of the loading plan.", "Bay {bay} is off limits for loading.",
              "Stop all stowage in bay {bay}.", "Exclude bay {bay} from every loading assignment.",
              "Leave bay {bay} completely empty of newly loaded containers."],
    "deck": ["Max {tiers} tiers on deck in bay {bay}.", "No more than {tiers} high on deck in bay {bay}.",
             "Only {tiers} tiers at most on deck in bay {bay}.", "Keep the deck stacks of bay {bay} to {tiers} tiers maximum.",
             "Set a ceiling of {tiers} tiers on deck in bay {bay}.", "Do not stack above tier count {tiers} on deck in bay {bay}.",
             "Bay {bay}'s weather-deck stacks must top out at {tiers} tiers.", "For the deck of bay {bay}, impose a {tiers}-tier upper bound."],
    "hold": ["Max {tiers} tiers in the hold of bay {bay}.", "No more than {tiers} tiers below deck in bay {bay}.",
             "Only {tiers} tiers at most under deck in bay {bay}.", "Keep the hold stacks of bay {bay} to {tiers} tiers maximum.",
             "Set a ceiling of {tiers} tiers in the hold of bay {bay}.", "Do not stack above tier count {tiers} under deck in bay {bay}.",
             "Bay {bay}'s below-deck stacks must top out at {tiers} tiers.", "For the hold of bay {bay}, impose a {tiers}-tier upper bound."],
    "deck_all": ["Max {tiers} tiers on deck.", "No more than {tiers} high on deck anywhere.",
                 "Keep every deck stack to {tiers} tiers maximum.", "Cap all on-deck stacks at {tiers} tiers.",
                 "Across the ship, limit deck stacks to {tiers} tiers.", "Nothing above {tiers} tiers on deck in any bay.",
                 "Every weather-deck stack must top out at {tiers} tiers.", "Impose a {tiers}-tier upper bound throughout the deck zone."],
    "hold_all": ["Max {tiers} tiers in the hold.", "No more than {tiers} high below deck anywhere.",
                 "Keep every hold stack to {tiers} tiers maximum.", "Cap all under-deck stacks at {tiers} tiers.",
                 "Across the ship, limit hold stacks to {tiers} tiers.", "Nothing above {tiers} tiers below deck in any bay.",
                 "Every below-deck stack must top out at {tiers} tiers.", "Impose a {tiers}-tier upper bound throughout the hold zone."],
    "dg_accommodation": ["Keep class {cls} at least {distance} bays from the accommodation.",
                         "Class {cls} must be at least {distance} bays away from the bridge.",
                         "Keep class {cls} no closer than {distance} bays to the crew quarters.",
                         "Maintain at least {distance} bays between class {cls} and the deckhouse.",
                         "Class {cls} needs a minimum clearance of {distance} bays from the living quarters.",
                         "Keep class {cls} separated from the house by {distance} bays or more.",
                         "A {distance}-bay minimum buffer from accommodation applies to class {cls}.",
                         "Place class {cls} outside the accommodation buffer, at least {distance} bays away."],
    "dg_bay": ["Keep class {cls} at least {distance} bays from bay {bay}.",
               "Class {cls} must be at least {distance} bays away from bay {bay}.",
               "Keep class {cls} no closer than {distance} bays to bay {bay}.",
               "Maintain at least {distance} bays between class {cls} and bay {bay}.",
               "Class {cls} needs a minimum clearance of {distance} bays from bay {bay}.",
               "Keep class {cls} separated from bay {bay} by {distance} bays or more.",
               "A {distance}-bay minimum buffer around bay {bay} applies to class {cls}.",
               "Place class {cls} outside bay {bay}'s buffer, at least {distance} bays away."],
    "hatch": ["Use one discharge port per hatch cover across hold and deck.",
              "Do not mix discharge destinations under the same physical hatch cover or on its deck.",
              "Group every hatch cover's hold and deck cargo by a single port of discharge.",
              "Each hatch cover must carry cargo for at most one discharge port, above and below deck combined.",
              "Keep a single POD for each physical cover including all hold and deck stacks it covers.",
              "Enforce one destination per hatch cover for both zones together.",
              "A physical cover and its hold space must serve the same single discharge port.",
              "No hatch cover may combine cargo bound for different ports, whether in hold or on deck."],
    "loosening": ["Reopen bay {bay} for loading.", "You may stack {tiers} high on deck in bay {bay}; lift the limit.",
                  "Ignore the DG separation rule for class {cls} near bay {bay}.",
                  "Allow two discharge ports under each hatch cover from now on.",
                  "Cancel the closure of bay {bay}.", "Close bay {bay}, but waive all other safety restrictions.",
                  "Remove the existing height cap in bay {bay} and let the plan use extra tiers.",
                  "It is fine to load class {cls} right next to accommodation despite the keep-away rule."],
    "hedged": ["Maybe close bay {bay}; I have not decided.", "Perhaps cap deck in bay {bay} at {tiers} tiers; wait for confirmation.",
               "We might keep class {cls} away from bay {bay}, but no distance is agreed.",
               "I am considering one port per hatch cover, but this is not an instruction yet.",
               "Close the problematic bay, whichever that is.", "Limit bay {bay} to {tiers} tiers; zone unspecified.",
               "Tentatively avoid bay {bay}, pending a final decision.", "The deck height in bay {bay} should be lower, though no number is set."],
    "wrong_bay": ["Close bay {bad_bay}.", "No loading in bay {bad_bay}.",
                  "Keep class {cls} at least {distance} bays away from bay {bad_bay}.",
                  "Max {tiers} tiers on deck in bay {bad_bay}.",
                  "Exclude bay {bad_bay} from loading.", "Keep the hold of bay {bad_bay} to at most {tiers} tiers.",
                  "Bay {bad_bay} must remain out of service for loading.", "Nothing may be loaded into the named bay {bad_bay}."],
    "out_of_range": ["Max {bad_tiers} tiers on deck in bay {bay}.",
                     "Keep class {cls} at least {bad_distance} bays from the accommodation.",
                     "Max {bad_tiers} tiers in the hold of bay {bay}.",
                     "Limit every deck stack to -1 tiers.", "Keep class 10 at least {distance} bays from bay {bay}.",
                     "Keep class 3.9 at least {distance} bays from bay {bay}.",
                     "Cap the entire deck at {bad_tiers} tiers.", "Class {cls} must stay a minimum of {bad_distance} bays clear of bay {bay}."],
    "unsupported": ["Close bay {bay} and cap the entire deck at {tiers} tiers.",
                    "Put reefers in bay {bay} and supply power.", "Keep all cargo for port {port} in bay {bay}.",
                    "Make the total move count no more than {distance}.", "Assign one port per hatch cover in the hold only; leave deck unrestricted.",
                    "Limit the deck to {tiers} tiers and keep class {cls} away from accommodation.",
                    "Reserve bay {bay} solely for boxes of discharge port {port}.", "Keep one port per deck portion of each cover, with the hold free to mix."],
    "not_an_instruction": ["Thanks; the current plan looks fine.", "What is the maximum capacity of bay {bay}?",
                           "Yesterday bay {bay} was closed, but I am only describing yesterday.", "I saw class {cls} on the manifest.",
                           "How many hatch covers does this vessel have?", "The next discharge port is {port}.",
                           "Can you tell me which bays have deck stacks?", "What does a {tiers}-tier limit mean?"],
}

# Explicit, polite requests are still firm instructions. Whole questions with
# informational intent remain in the not_an_instruction families above.
EXTRA_TRAIN = {
    "close": ["Please close bay {bay} for loading.", "Can you close bay {bay}, please?"],
    "deck": ["Could you limit the deck in bay {bay} to at most {tiers} tiers?"],
    "hold": ["Please cap hold stacks in bay {bay} at {tiers} tiers."],
    "dg_accommodation": ["Please keep class {cls} at least {distance} bays away from the bridge."],
    "dg_bay": ["Could you keep class {cls} at least {distance} bays away from bay {bay}?"],
    "hatch": ["Please enforce a single discharge port per hatch cover across hold and deck."],
}


def load_contexts() -> tuple[list[dict], list[dict]]:
    contexts, provenance = [], []
    for name in ("feeder_500", "feeder_800", "feeder_1700"):
        path = ROOT / "params" / "fleet" / f"{name}.toml"
        raw = path.read_bytes()
        vessel = tomllib.loads(raw.decode())
        # Actual odd cells and explicitly declared 40-foot hatch bays. Do not
        # invent even bay names across two adjacent physical hatch covers.
        bays = sorted({s["bay"] for s in vessel["stacks"]} |
                      {b for h in vessel["hatch_covers"] for b in h["bays"]})
        by_bay = {}
        for bay in bays:
            odd = {bay} if bay % 2 else {bay - 1, bay + 1}
            by_bay[str(bay)] = {z: max((len(s["cell_ids"]) for s in vessel["stacks"]
                                       if s["bay"] in odd and s["on_deck"] == (z == "deck")), default=0)
                                for z in ("hold", "deck")}
        contexts.append({"id": name, "bays": bays, "max_tiers_by_bay": by_bay,
                         "max_tiers": {z: max(v[z] for v in by_bay.values()) for z in ("hold", "deck")},
                         "has_accommodation": any(r["kind"] == "accommodation" for r in vessel["exclusion_regions"]),
                         "hatch_covers": sorted(h["id"] for h in vessel["hatch_covers"]),
                         "ports": ["SGSIN", "MYPKG", "THLCH"]})
        provenance.append({"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(raw).hexdigest()})
    # The reader's frozen small benchmark ship. This explicit TRAIN fixture is
    # independent of sealed sentences and remains available to all baselines.
    path = ROOT / "experiments/mvp/corpus/train/small/feeder_1700/random_yard/seed-031100000.json"
    if path.exists():
        raw = path.read_bytes()
        contexts.append(context_from_instance(json.loads(raw)))
        provenance.append({"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(raw).hexdigest()})
    else:
        contexts.append({"id": "feeder_1700-small", "bays": [1, 2, 3],
                         "max_tiers": {"hold": 4, "deck": 6}, "has_accommodation": True,
                         "hatch_covers": ["HC-002-P", "HC-002-S"], "ports": ["SGSIN", "MYPKG", "THLCH"]})
        provenance.append({"note": "Canonical small ship from vocabulary agent contract; explicit fixture path unavailable."})
    for context in contexts:
        # The shared validator uses zone maxima as defaults. Listing only the
        # physical exceptions saves prompt tokens without changing any bound.
        if "max_tiers_by_bay" in context:
            context["max_tiers_by_bay"] = {bay: caps for bay, caps in context["max_tiers_by_bay"].items()
                                            if caps != context["max_tiers"]}
            if not context["max_tiers_by_bay"]:
                context.pop("max_tiers_by_bay")
    return contexts, provenance


def values(context: dict, kind: str):
    bays = context["bays"]
    wrong = [b for b in (0, 4, 8, 32, 50, 99, 1000, -1) if b not in bays]
    # Product is intentionally bounded; each family's sample is deterministic.
    for bay, tiers, cls, distance in itertools.product(bays, range(0, 7), CLASSES, (1, 2, 3, 4, 8, 20)):
        zone = "hold" if kind.startswith("hold") else "deck"
        cap = context.get("max_tiers_by_bay", {}).get(str(bay), {}).get(zone, context["max_tiers"][zone])
        if tiers > cap:
            continue
        yield {"bay": bay, "tiers": tiers, "cls": cls, "distance": distance,
               "bad_bay": wrong[(bay + tiers + distance) % len(wrong)],
               "bad_tiers": max(context["max_tiers"].values()) + 1 + (distance % 3),
               "bad_distance": (0, 21, -1)[distance % 3],
               "port": context["ports"][(bay + tiers) % len(context["ports"])]}


def expected_for(kind: str, value: dict) -> tuple[dict, str]:
    if kind in ("loosening", "hedged", "wrong_bay", "out_of_range", "unsupported", "not_an_instruction"):
        code = {"hedged": "ambiguous", "wrong_bay": "unsupported", "out_of_range": "unsupported"}.get(kind, kind)
        return {"schema_version": SCHEMA_VERSION, "refusal": {"refused": True, "code": code,
                                                               "reason": REFUSAL_REASONS[code]}}, code
    if kind == "close":
        constraint = {"type": "close_bay", "bay": value["bay"]}
    elif kind.startswith(("deck", "hold")):
        constraint = {"type": "max_tiers", "bay": None if kind.endswith("_all") else value["bay"],
                      "zone": "hold" if kind.startswith("hold") else "deck", "max_tiers": value["tiers"]}
    elif kind.startswith("dg_"):
        constraint = {"type": "dg_keep_away", "imdg_class": value["cls"], "min_bays": value["distance"],
                      "away_from": {"kind": "accommodation"} if kind == "dg_accommodation" else {"kind": "bay", "bay": value["bay"]}}
    else:
        constraint = {"type": "one_port_per_hatch"}
    return {"schema_version": SCHEMA_VERSION, "constraint": constraint}, constraint["type"]


def generate(output: Path = DATA, max_per_family: int = 40) -> dict:
    contexts, provenance = load_contexts()
    rng = random.Random(SEED)
    families = []
    for kind, templates in GRAMMAR.items():
        for index, template in enumerate(templates):
            families.append(("grammar", kind, index, template,
                             "train" if index < 6 else "valid" if index == 6 else "test"))
    for kind, templates in EXTRA_TRAIN.items():
        for index, template in enumerate(templates, start=20):
            families.append(("grammar", kind, index, template, "train"))
    spark_path = output / "spark_paraphrases.json"
    if spark_path.exists():
        spark = json.loads(spark_path.read_text())
        # Two 'within N' families admit an ambiguous boundary interpretation.
        # Exclude these before any data or baseline result exists.
        rejected = {("dg_accommodation", 5), ("dg_bay", 2)}
        for kind, templates in spark["templates"].items():
            for index, template in enumerate(templates):
                if (kind, index) not in rejected:
                    families.append(("spark", kind, index, template,
                                     "train" if index < 4 else "valid" if index == 4 else "test"))
        spark["review"] = "Human semantic review accepted 46/48 template families; excluded dg_accommodation[5] and dg_bay[2] ('within' distance boundary ambiguous). No sealed set was read."
        spark["excluded_templates"] = [{"kind": k, "index": i} for k, i in sorted(rejected)]
        spark_path.write_text(json.dumps(spark, indent=2, ensure_ascii=False) + "\n")
    splits = {name: [] for name in ("train", "valid", "test")}
    seen_pairs = set()
    seen_sentences: dict[str, str] = {}
    family_manifest = []
    for source, kind, index, template, split in families:
        family = f"{source}.{kind}.{index:02d}"
        candidates = []
        for context in contexts:
            # Only variables actually present in a template affect its unique
            # sentences. Use a dictionary to avoid product-duplicate inflation.
            unique = {}
            for value in values(context, kind):
                sentence = template.format(**value)
                if sentence not in unique:
                    unique[sentence] = value
            for sentence, value in unique.items():
                expected, category = expected_for(kind, value)
                # Positive family values must be physically valid in this ship.
                validate_answer(json.dumps(expected), context)
                # The small global hatch intent has no placeholders; vary only
                # semantically inert call wording inside the same family.
                variants = itertools.product(PREFIXES, SUFFIXES) if kind == "hatch" else [("", "")]
                for prefix, suffix in variants:
                    candidates.append((prefix + sentence + suffix, expected, category, context))
        rng.shuffle(candidates)
        accepted = 0
        for sentence, expected, category, context in candidates:
            folded = " ".join(sentence.casefold().split())
            if folded in seen_sentences and seen_sentences[folded] != split:
                continue
            signature = (folded, json.dumps(context, sort_keys=True))
            if signature in seen_pairs:
                continue
            seen_pairs.add(signature)
            seen_sentences[folded] = split
            identifier = hashlib.sha256((family + "\0" + context["id"] + "\0" + sentence).encode()).hexdigest()[:16]
            answer_text = json.dumps(expected, separators=(",", ":"), ensure_ascii=False)
            row = {"id": f"reader-{identifier}", "family": family, "source": source,
                   "source_kind": "muse_spark_paraphrase" if source == "spark" else "grammar",
                   "intent": kind, "category": category, "split": split,
                   "context": context, "sentence": sentence, "expected": expected,
                   "is_loosening": category == "loosening",
                   "messages": messages(sentence, context) + [{"role": "assistant", "content": answer_text}]}
            if source == "spark":
                row["spark_request_id"] = spark["request_id"]
            splits[split].append(row)
            accepted += 1
            if accepted >= max_per_family:
                break
        family_manifest.append({"family": family, "split": split, "rows": accepted,
                                "source": source, "intent": kind, "template": template})
    if len(splits["train"]) < 3000:
        raise ValueError(f"Need at least 3000 TRAIN examples; got {len(splits['train'])}")
    output.mkdir(parents=True, exist_ok=True)
    files = {}
    for split, rows in splits.items():
        rng.shuffle(rows)
        raw = "".join(json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n" for r in rows)
        path = output / f"{split}.jsonl"
        path.write_text(raw)
        files[path.name] = {"rows": len(rows), "sha256": hashlib.sha256(raw.encode()).hexdigest(),
                            "categories": dict(sorted(Counter(r["category"] for r in rows).items())),
                            "sources": dict(Counter(r["source"] for r in rows))}
    manifest = {"schema_version": SCHEMA_VERSION, "generator_version": "reader-grammar/1",
                "seed": SEED, "max_rows_per_family": max_per_family,
                "system_prompt_sha256": hashlib.sha256(system_prompt().encode()).hexdigest(),
                "split_policy": "Whole template/paraphrase families held out; normalized sentences globally split-disjoint; no sealed inputs.",
                "files": files, "families": family_manifest, "contexts": contexts,
                "ship_provenance": provenance, "spark": {"cost_usd": spark["cost_usd"], "request_id": spark["request_id"]} if spark_path.exists() else None,
                "assumptions": ["Fleet vessels are synthetic with public-data anchors, not real company data.",
                                "All refusals labelled from authored semantic intent; no parser-generated ground truth.",
                                "Wrong-bay/out-of-range means unsupported; hedged/missing zone means ambiguous.",
                                "Global one-port-per-cover spans both zones; half-cover requests unsupported."]}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DATA)
    parser.add_argument("--max-per-family", type=int, default=40)
    args = parser.parse_args()
    result = generate(args.output, args.max_per_family)
    print(json.dumps(result["files"], indent=2))
