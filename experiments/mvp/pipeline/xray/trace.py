"""JSON-only xray.trace/1 contract; probabilities are pre-mask, full-vocabulary."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import Mode, StageRecord, canonical_json, sha256_json
from experiments.mvp.reader.v3.decoder import _Grammar, context_json_schema

FORMAT = "xray.trace/1"
REASONS = {"not_in_grammar", "bay_not_on_ship", "tier_over_cap"}


def _payload(trace):
    value = {k: v for k, v in trace.items() if k != "payload_json"}
    value["stage_record"] = {k: v for k, v in value["stage_record"].items() if k != "output_sha256"}
    return value


def seal_trace(trace: dict) -> dict:
    """Bind payload and stage provenance using shared canonical JSON bytes.

    Keep those bytes for synchronous file:// playback: JavaScript cannot recover
    Python's float spellings (0.0 versus 0, for example) after JSON parsing.
    This checksum detects inconsistency; it is not an authenticity signature.
    """
    value = json.loads(json.dumps(trace, allow_nan=False))
    payload = _payload(value)
    value["payload_json"] = canonical_json(payload)
    value["stage_record"]["output_sha256"] = sha256_json(payload)
    return value


def validate_trace(trace: dict) -> dict:
    """Check replay invariants and return a detached JSON-able value."""
    value = json.loads(json.dumps(trace, allow_nan=False))
    if value.get("format") != FORMAT:
        raise ValueError("unknown x-ray trace format")
    source = value["source"]
    if source.get("kind") not in {"fake", "recorded"} or not source.get("label"):
        raise ValueError("trace needs explicit source provenance")
    record = StageRecord.model_validate(value["stage_record"])
    if record.stage != "reader_xray" or source.get("synthetic") is not True:
        raise ValueError("trace needs consistent stage provenance")
    expected_mode = Mode.generated if source["kind"] == "fake" else Mode.recorded
    if record.mode != expected_mode:
        raise ValueError("source and stage mode disagree")
    if source["kind"] == "fake" and "FAKE" not in source["label"]:
        raise ValueError("scripted traces must say FAKE")
    scripted = re.compile(r"fake|script", re.I)
    if source["kind"] == "fake":
        if record.model is not None and not scripted.search(record.model):
            raise ValueError("FAKE source and model provenance disagree")
    elif (not record.model or not record.model.strip() or scripted.search(record.model)
          or scripted.search(record.notes) or scripted.search(source["label"])
          or any(not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                 for digest in (record.adapter_sha256, record.prompt_sha256, record.source_sha256))):
        raise ValueError("recorded source needs consistent model provenance")
    if not isinstance(value["sentence"], str) or not isinstance(value["context"], dict):
        raise ValueError("trace needs sentence and ship context")
    text = ""
    stopped = False
    for index, step in enumerate(value["steps"], 1):
        if stopped or step["step"] != index:
            raise ValueError("steps must be consecutive and stop at EOS")
        probabilities = []
        ids = set()
        for candidate in step["top_k"]:
            if (type(candidate["token_id"]) is not int or candidate["token_id"] < 0
                    or not isinstance(candidate["token"], str) or type(candidate["blocked"]) is not bool
                    or candidate["token_id"] in ids):
                raise ValueError("invalid or duplicate ranked token")
            ids.add(candidate["token_id"])
            probability = candidate["probability"]
            if not isinstance(probability, (int, float)) or not math.isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError("invalid pre-mask probability")
            probabilities.append(probability)
            if candidate["blocked"]:
                if candidate["reason"] not in REASONS:
                    raise ValueError("blocked token needs a reason")
            elif candidate["reason"] is not None:
                raise ValueError("allowed token cannot have a blocked reason")
        if probabilities != sorted(probabilities, reverse=True) or sum(probabilities) > 1.000001:
            raise ValueError("top-k must be ranked full-vocabulary probabilities")
        chosen = step["chosen"]
        if type(chosen["token_id"]) is not int or chosen["token_id"] < 0 or not isinstance(chosen["token"], str):
            raise ValueError("invalid chosen token")
        if not 0 <= chosen["probability"] <= 1:
            raise ValueError("invalid chosen probability")
        if any(c["blocked"] and c["token_id"] == chosen["token_id"] for c in step["top_k"]):
            raise ValueError("chosen token was blocked")
        matching = [c for c in step["top_k"] if c["token_id"] == chosen["token_id"]]
        if matching and any(matching[0][key] != chosen[key] for key in ("token", "probability")):
            raise ValueError("chosen token differs from ranked candidate")
        stopped = step["is_stop"]
        if not isinstance(stopped, bool):
            raise ValueError("is_stop must be boolean")
        if not stopped:
            text += chosen["token"]
        if step["json_so_far"] != text:
            raise ValueError("JSON prefixes must match actual chosen tokens")
    if text != value["output"] or not isinstance(value["complete"], bool):
        raise ValueError("output differs from recorded token stream")
    grammar = _Grammar(context_json_schema(value["context"]))
    states = grammar.consume(grammar.start, text)
    if not states or value["complete"] != any(node.terminal for node in states):
        raise ValueError("completion flag disagrees with reader grammar")
    if stopped and not value["complete"]:
        raise ValueError("EOS requires a complete reader answer")
    payload = _payload(value)
    if value.get("payload_json") != canonical_json(payload) or record.output_sha256 != sha256_json(payload):
        raise ValueError("trace output hash mismatch")
    if record.input_sha256 != sha256_json({"sentence": value["sentence"], "context": value["context"]}):
        raise ValueError("trace input hash mismatch")
    return value


def load_trace(path: str | Path) -> dict:
    return validate_trace(json.loads(Path(path).read_text()))
