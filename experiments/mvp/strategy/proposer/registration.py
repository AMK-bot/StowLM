"""Fixed zero-shot collection registration; no model loading or API calls.

Collection provenance is an attestation, not proof of how an external collector
behaved. Missing or contradictory attestations cannot enter a model comparison.
The evaluation seed is separate from the generation seed registered here.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


MODEL_CONFIGS = {
    "spark": "muse-spark-1.3",
    "glimmer": "meta-models/Muse-Glimmer-30B",
}
GENERATION_SETTINGS = {"temperature": 0.7, "top_p": 1.0, "max_output_tokens": 2048}
GENERATION_SEED_BASE = 179
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def collection_requirements(arm: str, attempt: int) -> dict[str, Any]:
    """Registered provenance apart from map-specific prompt and input hashes."""
    if arm not in MODEL_CONFIGS or type(attempt) is not int or attempt < 1:
        raise ValueError("collection requires a model arm and positive integer attempt")
    return dict(model=MODEL_CONFIGS[arm], generation_settings=deepcopy(GENERATION_SETTINGS),
                seed=GENERATION_SEED_BASE + attempt - 1, base_model=True,
                adapter=None, independent=True, feedback="none", zero_shot=True)


def validate_collection_provenance(provenance: Any, *, arm: str, attempt: int,
                                   prompt_sha256: str, input_sha256: str) -> dict[str, Any]:
    """Fail closed against the registration and the actual evaluation input."""
    if not isinstance(provenance, dict):
        raise ValueError("missing collection_provenance object")
    allowed = set(collection_requirements(arm, attempt)) | {"prompt_sha256", "input_sha256"}
    if set(provenance) != allowed:
        raise ValueError("collection provenance has missing or unregistered fields")
    for name, expected in (("prompt_sha256", prompt_sha256), ("input_sha256", input_sha256)):
        if not isinstance(expected, str) or not _DIGEST.fullmatch(expected):
            raise ValueError(f"invalid expected {name}")
        if provenance.get(name) != expected:
            raise ValueError(f"collection provenance {name} mismatch or missing")
    for name, expected in collection_requirements(arm, attempt).items():
        actual = provenance.get(name)
        if name not in provenance or actual != expected:
            raise ValueError(f"collection provenance {name} mismatch or missing")
        if type(actual) is not type(expected):
            raise ValueError(f"collection provenance {name} has invalid type")
        if name == "generation_settings":
            if any(isinstance(value, bool) for value in actual.values()):
                raise ValueError("collection provenance generation_settings has invalid type")
            if type(actual["max_output_tokens"]) is not int:
                raise ValueError("collection provenance generation_settings has invalid type")
    return deepcopy(provenance)


def validate_proposal_provenance(metadata: Any, *, arm: str, attempt: int,
                                 prompt_sha256: str, input_sha256: str,
                                 expected_source_sha256: str,
                                 map_id: str | None = None) -> dict[str, Any]:
    """Revalidate persisted imported metadata before comparison or resume."""
    if not isinstance(metadata, dict) or metadata.get("provider") != "jsonl":
        raise ValueError("zero-shot comparison requires an imported jsonl response")
    if (not isinstance(expected_source_sha256, str)
            or not _DIGEST.fullmatch(expected_source_sha256)
            or metadata.get("source_sha256") != expected_source_sha256):
        raise ValueError("proposal source_sha256 does not match run identity")
    if metadata.get("canned") or metadata.get("provider_error"):
        raise ValueError("canned or failed proposal is not a collected model response")
    if set(metadata) - {"provider", "source_sha256", "source_row", "model_called", "collection_provenance"}:
        raise ValueError("proposal metadata has unregistered collection fields")
    provenance = validate_collection_provenance(metadata.get("collection_provenance"), arm=arm,
        attempt=attempt, prompt_sha256=prompt_sha256, input_sha256=input_sha256)
    source_row = metadata.get("source_row")
    if not isinstance(source_row, dict):
        raise ValueError("missing original source_row")
    if set(source_row) - {"map_id", "arm", "attempt", "collection_provenance", "usage"}:
        raise ValueError("source_row has unregistered collection fields")
    if (source_row.get("arm") != arm or type(source_row.get("attempt")) is not int
            or source_row.get("attempt") != attempt
            or not isinstance(source_row.get("map_id"), str)
            or (map_id is not None and source_row.get("map_id") != map_id)
            or source_row.get("collection_provenance") != provenance):
        raise ValueError("source_row collection identity contradicts proposal provenance")
    return provenance
