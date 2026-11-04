"""Frozen validation-only, sequential voice measurement; never invokes a planner.

prepare makes no Meta calls. run is paid, checkpointed, and never automatically
retries interrupted/failed requests. Latency is observed wall time from before
ASR upload through validated parser output, excluding TTS and human confirmation.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import statistics
import time

from experiments.mvp.voice.audio import radio, synthesize, wav_info

HERE = Path(__file__).resolve().parent
VALID = HERE.parent / "reader/data/valid.jsonl"
CATEGORIES = ("close_bay", "max_tiers", "dg_keep_away", "one_port_per_hatch")
DEFAULT_OUTPUT = HERE / "runs/v5-1.3"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    temporary.replace(path)


def freeze_cohort(output):
    """Read exactly one allowlisted validation file, never any test/sealed data."""
    rows = [json.loads(line) for line in VALID.read_text().splitlines() if line.strip()]
    chosen = []
    seen = set()
    for category in CATEGORIES:
        count = 0
        for row in sorted(rows, key=lambda item: item["id"]):
            if row.get("split") != "valid" or row.get("category") != category or row["sentence"] in seen:
                continue
            if "constraint" not in row["expected"]:
                continue
            chosen.append({key: row[key] for key in ("id", "sentence", "context", "expected", "category", "split")})
            seen.add(row["sentence"])
            count += 1
            if count == 10:
                break
        if count != 10:
            raise ValueError("Insufficient unique valid constraint sentences")
    thai = json.loads((HERE / "thai.json").read_text())
    by_id = {row["id"]: row for row in chosen}
    if len(thai["sentences"]) != 20 or len({r["source_id"] for r in thai["sentences"]}) != 20:
        raise ValueError("Thai stretch must contain 20 unique mapped validation sentences")
    for item in thai["sentences"]:
        if item["source_id"] not in by_id or item["english"] != by_id[item["source_id"]]["sentence"]:
            raise ValueError("Thai mapping does not match English frozen source")
    body = {"version": "voice-v5-1.3/1", "source": "experiments/mvp/reader/data/valid.jsonl",
            "source_sha256": hashlib.sha256(VALID.read_bytes()).hexdigest(),
            "selection": "First 10 unique sentence strings by ascending id within each of 4 supported constraint types",
            "selection_scope": "Development validation only; no refusal or sealed evaluation claim",
            "english": chosen, "thai": thai}
    body["cohort_sha256"] = digest(body)
    path = Path(output) / "cohort.json"
    if path.exists() and json.loads(path.read_text()) != body:
        raise ValueError("Frozen cohort differs; use a new output directory rather than overwrite")
    if not path.exists():
        save(path, body)
    return body


def load_cohort(output):
    cohort = json.loads((Path(output) / "cohort.json").read_text())
    identity = cohort.pop("cohort_sha256")
    if digest(cohort) != identity:
        raise ValueError("Frozen cohort hash mismatch")
    cohort["cohort_sha256"] = identity
    return cohort


def prepare(output=DEFAULT_OUTPUT):
    output = Path(output)
    cohort = freeze_cohort(output)
    manifest = {"cohort_sha256": cohort["cohort_sha256"], "clips": {}}
    for row in cohort["english"]:
        clean = output / "audio" / f"en-{row['id']}-clean.wav"
        manifest["clips"][f"en/{row['id']}/clean"] = synthesize(row["sentence"], "en", clean)
        noisy = clean.with_name(clean.name.replace("clean", "radio"))
        seed = int(hashlib.sha256(("voice-v5-1.3-radio/" + row["id"]).encode()).hexdigest()[:16], 16)
        manifest["clips"][f"en/{row['id']}/radio"] = radio(clean, noisy, seed=seed)
        save(output / "audio-manifest.json", manifest)
        print(json.dumps({"prepared": row["id"], "language": "en"}), flush=True)
    for row in cohort["thai"]["sentences"]:
        clean = output / "audio" / f"th-{row['source_id']}-clean.wav"
        manifest["clips"][f"th/{row['source_id']}/clean"] = synthesize(row["thai"], "th", clean)
        save(output / "audio-manifest.json", manifest)
        print(json.dumps({"prepared": row["source_id"], "language": "th"}), flush=True)
    return manifest


def normalized(text):
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


def edit_distance(left, right):
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(previous[j] + 1, current[-1] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def safe_evidence(evidence):
    # Excludes raw completions, error diagnostics, headers, prompts, credentials.
    allowed = ("request_id", "cost_usd", "parser", "model", "purpose", "adapter_sha256", "prompt_sha256",
               "audio_duration_ms", "reservation_id", "session_id", "cost_basis", "audio_seconds")
    return {key: evidence[key] for key in allowed if key in evidence}


def source_hashes():
    paths = list(HERE.glob("*.py")) + [HERE / "thai.json", HERE.parent / "reader/pipeline.py", HERE.parent / "reader/schema.py"]
    return {str(path.relative_to(HERE.parent.parent.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths) if path.is_file()}


def _parse(parser, sentence, context):
    from experiments.mvp.reader.schema import answer_envelope, canonicalize
    start = time.perf_counter()
    answer, evidence = parser.parse(sentence, context)
    return {"answer": answer_envelope(answer), "canonical": canonicalize(answer, context),
            "parser_latency_s": time.perf_counter() - start, "parser_evidence": safe_evidence(evidence)}


def run_measurement(output=DEFAULT_OUTPUT, *, parser=None, transcriber=None):
    """Sequential injectable harness. Construction and every paid call run here only."""
    output = Path(output)
    cohort = load_cohort(output)
    manifest = json.loads((output / "audio-manifest.json").read_text())
    if manifest["cohort_sha256"] != cohort["cohort_sha256"]:
        raise ValueError("Audio manifest belongs to a different cohort")
    provenance_path = output / "run-provenance.json"
    provenance = json.loads(provenance_path.read_text()) if provenance_path.exists() else {"invocations": []}
    invocation = {"started_utc": datetime.now(timezone.utc).isoformat(),
                  "cohort_sha256": cohort["cohort_sha256"], "start_source_sha256": source_hashes(),
                  "execution": "One sequential Python worker; no GPU; no planner"}
    provenance["invocations"].append(invocation)
    save(provenance_path, provenance)
    if parser is None:
        from experiments.mvp.reader.pipeline import SparkParser
        parser = SparkParser(purpose="voice-v5-1.3-parser", allocation_usd=Decimal("2.5"))
    if transcriber is None:
        from experiments.mvp.voice.asr import MuseTranscriber
        transcriber = MuseTranscriber()
    sources = {row["id"]: row for row in cohort["english"]}
    jobs = [("en", row["id"], "typed", row["sentence"]) for row in cohort["english"]]
    jobs += [("en", row["id"], condition, row["sentence"]) for condition in ("clean", "radio") for row in cohort["english"]]
    jobs += [("th", row["source_id"], "typed", row["thai"]) for row in cohort["thai"]["sentences"]]
    jobs += [("th", row["source_id"], "clean", row["thai"]) for row in cohort["thai"]["sentences"]]
    for language, source_id, condition, script in jobs:
        path = output / "checkpoints" / f"{language}-{source_id}-{condition}.json"
        if path.exists():
            prior = json.loads(path.read_text())
            if prior.get("cohort_sha256") != cohort["cohort_sha256"]:
                raise ValueError("Checkpoint cohort mismatch")
            # Including pending/error: unknown remote outcomes must not be retried.
            continue
        row = {"language": language, "source_id": source_id, "condition": condition,
               "script": script, "cohort_sha256": cohort["cohort_sha256"],
               "started_utc": datetime.now(timezone.utc).isoformat(), "status": "pending"}
        save(path, row)
        start = time.perf_counter()
        phase = "audio_validation" if condition != "typed" else "parser"
        try:
            sentence = script
            if condition != "typed":
                audio_path = output / "audio" / f"{language}-{source_id}-{condition}.wav"
                expected_audio = manifest["clips"][f"{language}/{source_id}/{condition}"]
                if wav_info(audio_path)["sha256"] != expected_audio["sha256"]:
                    raise ValueError("Prepared audio hash mismatch")
                # The measured span starts immediately before calling ASR/upload.
                start = time.perf_counter()
                phase = "transcriber"
                transcription = transcriber.transcribe(audio_path)
                sentence = transcription["transcript"]
                if not isinstance(sentence, str) or not sentence.strip():
                    raise ValueError("Empty transcript")
                row.update({"transcript": sentence,
                            "asr_latency_s": transcription.get("latency_s", time.perf_counter() - start),
                            "asr_evidence": safe_evidence(transcription.get("evidence", {})),
                            "audio_sha256": expected_audio["sha256"]})
                save(path, row)
            phase = "parser"
            row.update(_parse(parser, sentence, sources[source_id]["context"]))
            row["end_to_end_s"] = time.perf_counter() - start
            row["status"] = "complete"
        except Exception as error:
            # Error text can include credentials or arbitrary provider output.
            row.update({"status": "error", "error_type": type(error).__name__,
                        "error_phase": phase, "failed_elapsed_s": time.perf_counter() - start})
        save(path, row)
        print(json.dumps({"language": language, "id": source_id, "condition": condition, "status": row["status"]}), flush=True)
    report = make_report(output)
    invocation.update({"finished_utc": datetime.now(timezone.utc).isoformat(), "end_source_sha256": source_hashes()})
    invocation["source_changed_during_invocation"] = invocation["start_source_sha256"] != invocation["end_source_sha256"]
    save(provenance_path, provenance)
    return report


def latency_summary(values):
    values = sorted(values)
    if not values:
        return {"n": 0}
    return {"n": len(values), "median_s": statistics.median(values),
            "p95_s": values[max(0, __import__("math").ceil(.95 * len(values)) - 1)], "min_s": values[0], "max_s": values[-1]}


def make_report(output=DEFAULT_OUTPUT):
    from experiments.mvp.reader.schema import canonicalize
    output = Path(output)
    cohort = load_cohort(output)
    sources = {row["id"]: row for row in cohort["english"]}
    rows = [json.loads(path.read_text()) for path in sorted((output / "checkpoints").glob("*.json"))]
    for row in rows:
        if row.get("cohort_sha256") != cohort["cohort_sha256"]:
            raise ValueError("Checkpoint cohort mismatch")
    typed = {(row["language"], row["source_id"]): row for row in rows if row["condition"] == "typed"}
    groups = {}
    for language, condition, n in (("en", "typed", 40), ("en", "clean", 40), ("en", "radio", 40), ("th", "typed", 20), ("th", "clean", 20)):
        selected = [r for r in rows if r["language"] == language and r["condition"] == condition]
        complete = [r for r in selected if r["status"] == "complete"]
        item = {"planned_n": n, "status_counts": dict(Counter(r["status"] for r in selected)),
                "not_started_n": n - len(selected), "completed_n": len(complete),
                "parse_gold_exact_n": sum(r["canonical"] == canonicalize(sources[r["source_id"]]["expected"], sources[r["source_id"]]["context"]) for r in complete),
                "latency": latency_summary([r["end_to_end_s"] for r in complete])}
        item["parse_gold_exact_rate_all_planned"] = item["parse_gold_exact_n"] / n
        if condition != "typed":
            transcripts = [r for r in selected if "transcript" in r]
            item["transcript_observed_n"] = len(transcripts)
            item["transcript_strict_exact_n"] = sum(r["transcript"] == r["script"] for r in transcripts)
            item["transcript_strict_exact_rate_all_planned"] = item["transcript_strict_exact_n"] / n
            item["transcript_normalized_exact_n"] = sum(normalized(r["transcript"]) == normalized(r["script"]) for r in transcripts)
            tokenise = (lambda s: normalized(s).split()) if language == "en" else (lambda s: list(re.sub(r"\s", "", s)))
            edits = sum(edit_distance(tokenise(r["script"]), tokenise(r["transcript"])) for r in transcripts)
            units = sum(len(tokenise(r["script"])) for r in transcripts)
            item["wer" if language == "en" else "cer"] = edits / units if units else None
            item["quality_reference_units"] = units
            for baseline_language in (["en"] if language == "en" else ["en", "th"]):
                pairs = [(r, typed.get((baseline_language, r["source_id"]))) for r in complete]
                pairs = [(r, t) for r, t in pairs if t and t["status"] == "complete"]
                matches = sum(r["canonical"] == t["canonical"] for r, t in pairs)
                item[f"parse_vs_{baseline_language}_typed"] = {"exact_n": matches, "paired_n": len(pairs), "planned_n": n,
                                                               "exact_rate_all_planned": matches / n,
                                                               "exact_rate_valid_pairs": matches / len(pairs) if pairs else None}
        groups[f"{language}/{condition}"] = item
    report = {"cohort_sha256": cohort["cohort_sha256"], "source_sha256": cohort["source_sha256"],
              "scope": "Synthetic development validation; not real speech, sealed evaluation, planner correctness, or Thai readiness",
              "latency_scope": "Observed ASR upload through validated constraint/refusal; excludes audio generation, user confirmation, planner; typed group is parser-only",
              "metrics": "Strict transcript equality includes punctuation/case; normalized equality and English normalized WER / Thai whitespace-free CER are supplementary. Parse equality uses reader canonicalize, retaining all constraint fields and refusal codes.",
              "groups": groups}
    save(output / "summary.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("cohort", "prepare", "run", "report"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    functions = {"cohort": freeze_cohort, "prepare": prepare, "run": run_measurement, "report": make_report}
    value = functions[args.action](args.output)
    if args.action in ("cohort", "report"):
        print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
