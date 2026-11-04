"""Identity records for calibration logits (code review w2-rev-c1: bind every array to its inputs).

Stdlib only, so both the uv environment (MLX collector, analyzer) and the isolated PyTorch environment
(``experiments/grpo-venue/.venv-pt``) import it. Each ``<tag>__<prompt>.npy`` gets ``<tag>__<prompt>.identity.json``:
the prompt's token-ID digest, the array's shape and SHA-256, the model identity (pinned revision or content hash),
collection settings and software versions. The analyzer refuses anything missing, mismatched or mixed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def ids_sha256(ids) -> str:
    return hashlib.sha256(json.dumps([int(i) for i in ids], separators=(",", ":")).encode()).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def write_identity(out: Path, tag: str, prompt: str, ids, array_path: Path, *, model: dict, settings: dict,
                   software: dict) -> dict:
    import numpy as np  # both environments have NumPy

    arr = np.load(array_path)
    record = {"tag": tag, "prompt": prompt, "prompt_ids_sha256": ids_sha256(ids), "n_prompt_tokens": len(ids),
              "array": Path(array_path).name, "array_sha256": file_sha256(array_path), "shape": list(arr.shape),
              "dtype": str(arr.dtype), "model": model, "settings": settings, "software": software}
    (Path(out) / f"{tag}__{prompt}.identity.json").write_text(json.dumps(record, indent=1, sort_keys=True))
    return record
