"""Disclose adapter training inputs from content-validated trainer sidecars.

This is local byte binding, not third-party authentication. ``run.json`` is
informational and never supplies lineage. Missing historical fields stay unknown.
"""
from __future__ import annotations

from pathlib import Path

from eval.harness_integrity import _path_digest
from eval.snapshot_io import read_snapshot


_VERSION_KEYS = ("prompt", "format", "dataset", "rule_catalogue")


def data_version_identity(source):
    """Pure producer helper: retain observed dataset versions without filling unknowns."""
    versions = {}
    for name in _VERSION_KEYS:
        value = source.get(name)
        if value is None:
            value = source.get({"dataset": "dataset_version", "rule_catalogue": "training_catalogue_sha256"}.get(name, name))
        values = [value] if isinstance(value, str) else value
        if values is not None and (not isinstance(values, list) or
                any(not isinstance(item, str) or not item for item in values)):
            raise ValueError(f"training {name} version must be a string or list of strings")
        if name == "rule_catalogue" and values is not None:
            values = ["sha256:" + item.removeprefix("sha256:") if item != "unknown" else item for item in values]
        versions[name] = values
    return versions


def current_data_versions():
    """Representation and rule versions used for this evaluation, never training evidence."""
    from llm.stowlm.format import FORMAT_VERSION
    from llm.stowlm.format_prompt import PROMPT_VERSION
    from llm.stowlm.sft_data import DATASET_VERSION
    from stowage.versions import component_versions
    return {"prompt": PROMPT_VERSION, "format": FORMAT_VERSION, "dataset": DATASET_VERSION,
            "rule_catalogue": component_versions()["rule_catalogue"]}


def _bound_data_stats(training, data):
    """Recover old omitted fields only from the exact dataset snapshot admitted by training."""
    configuration, files = training.get("config") or {}, data.get("files_sha256") or {}
    if not isinstance(configuration, dict) or not isinstance(files, dict):
        return None
    directory, expected = configuration.get("data_dir"), files.get("stats.json")
    if not isinstance(directory, str) or not isinstance(expected, str):
        return None
    try:
        snapshot = read_snapshot(Path(directory) / "stats.json", expected_sha256=expected)
        return snapshot if isinstance(snapshot.json(), dict) else None
    except (OSError, ValueError):
        return None


def adapter_training_lineage(planner, *, parameters=None):
    """Extract training.data_identity only after the planner validates its sidecar."""
    validator = getattr(planner, "_adapter_provenance", None)
    stowlm = callable(validator)
    adapter = getattr(planner, "adapter_path", None)
    result = {"training_epochs": None, "training_catalogue_sha256": None,
              "training_versions": {key: None for key in _VERSION_KEYS},
              "training_lineage": {"status": "not_applicable", "adapter_content_sha256": None,
                                   "adapter_weights_sha256": None, "metadata_sha256": None}}
    if not stowlm:
        return result
    if adapter is None:
        result["training_lineage"]["status"] = "base_model"
        return result
    sidecar = Path(adapter) / "adapters.safetensors.provenance.json"
    try:
        snapshot = read_snapshot(sidecar)
        # This verifies schema, explicit provenance/stubs, and the actual adapter
        # weights SHA-256 using the same contract as production model loading.
        validator()
        metadata = snapshot.json()
        identity = _path_digest(adapter)
        expected = (parameters if parameters is not None else
                    getattr(planner, "_harness_weight_identity", {}) or {}).get("adapter_content_sha256")
        if expected is not None and identity != expected:
            raise ValueError("adapter content changed from the pinned evaluation identity")
        read_snapshot(sidecar, expected_sha256=snapshot.sha256)
        training = metadata.get("training") or {}
        if not isinstance(training, dict):
            raise ValueError("training metadata must be an object")
        data = training.get("data_identity") or {}
        if not isinstance(data, dict):
            raise ValueError("training data_identity must be an object")
        for name in ("training_epochs", "training_catalogue_sha256"):
            values = data.get(name)
            if values is not None and (not isinstance(values, list) or
                    any(not isinstance(value, str) or not value for value in values)):
                raise ValueError(f"{name} must be a list of nonempty strings")
            if name == "training_catalogue_sha256" and values is not None and any(
                    len(value.removeprefix("sha256:")) != 64 or any(c not in "0123456789abcdef"
                    for c in value.removeprefix("sha256:")) for value in values):
                raise ValueError("training catalogue digests must be SHA-256 hex strings")
            result[name] = values
        versions = data_version_identity(data)
        stats = _bound_data_stats(training, data)
        if stats is not None:
            recorded = data_version_identity(stats.json())
            versions = {key: versions[key] if versions[key] is not None else recorded[key] for key in _VERSION_KEYS}
        result["training_versions"] = versions
        result["training_lineage"] = {"status": "validated", "adapter_content_sha256": identity,
                                      "adapter_weights_sha256": metadata["weights_sha256"],
                                      "metadata_sha256": snapshot.sha256,
                                      "data_stats_sha256": None if stats is None else stats.sha256}
        return result
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"untrusted adapter training lineage: {exc}") from exc


def training_lineage_fields(lineage, evaluation_epoch):
    """Flag any measured training epoch different from the evaluation epoch."""
    epochs = lineage["training_epochs"]
    differs = any(epoch != evaluation_epoch for epoch in epochs) if epochs and evaluation_epoch else None
    versions = lineage.get("training_versions", {key: None for key in _VERSION_KEYS})
    current = current_data_versions()
    applicable = lineage["training_lineage"]["status"] == "validated"
    mismatches = [key for key in _VERSION_KEYS if versions[key] and any(
        version != "unknown" and current[key] != "unknown" and version != current[key]
        for version in versions[key])]
    missing = [key for key in _VERSION_KEYS if not versions[key] or "unknown" in versions[key]]
    unknown = applicable and (not epochs or bool(missing))
    version_differs = bool(mismatches) if applicable and (mismatches or not missing) else None
    return {**lineage, "training_epoch_differs": differs,
            "training_epoch_note": "training epoch ≠ evaluation epoch" if differs else None,
            "evaluation_versions": current, "training_version_differs": version_differs,
            "training_version_mismatches": mismatches, "training_lineage_unknown": unknown,
            "training_lineage_note": "[training lineage unknown]" if unknown else None}
