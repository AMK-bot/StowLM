"""Which pipeline this app runs: a frozen one (a verified manifest, D270) or a development build.

``python -m app --v6 --manifest PATH`` (or ``PIPELINE_MANIFEST``) verifies the tree against the manifest before anything
loads, and refuses to start on any drift. ``pipeline_sha256`` is the pipeline layer's manifest digest (D275): a pipeline
manifest's own digest, or the ``pipeline_layer.manifest_sha256`` a film manifest pins (the verifier has checked it against
the pipeline manifest's bytes). A film manifest's own digest is ``film_sha256``, shown beside it and never in its place.
Every receipt the app writes carries both, and the pages show the first 12 characters. With no manifest every receipt and the stage page say
"Development build, not frozen". Child processes inherit the verified values through the environment, never re-verify.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

MANIFEST_ENV = "PIPELINE_MANIFEST"
SHA_ENV, TREE_ENV, FILM_ENV = "STOWLM_PIPELINE_SHA256", "STOWLM_PIPELINE_TREE", "STOWLM_FILM_SHA256"
DEVELOPMENT = "Development build, not frozen"
SHORT = 12


class Refused(SystemExit):
    """The app won't start. The message is plain text for the terminal, never a traceback."""


def _drift_lines(drifts) -> list[str]:
    rows = []
    for row in drifts or []:
        if isinstance(row, dict):
            detail = f" ({row['detail']})" if row.get("detail") else ""
            rows.append(f"  {row.get('path', '?')}: {row.get('reason', '?')}{detail}")
        else:
            rows.append(f"  {row}")
    return rows


def require(manifest_path: str) -> dict:
    """Verify the tree against the manifest, or raise ``Refused`` with every drift. On success the values are published."""
    try:
        from experiments.mvp.pipeline.freeze.verify import FreezeDrift, require_frozen
    except Exception as error:  # noqa: BLE001 - any import failure with a manifest given refuses, plainly
        raise Refused(f"Refusing to start: the freeze verifier could not be loaded to check {manifest_path} "
                      f"({type(error).__name__}: {error}).") from None
    try:
        pins = require_frozen(manifest_path, full_weights=False)
    except FreezeDrift as drift:
        lines = _drift_lines(getattr(drift, "drifts", None)) or [f"  {drift}"]
        raise Refused("\n".join([f"Refusing to start: the tree differs from the freeze manifest {manifest_path} "
                                 f"({len(lines)} drift{'s' if len(lines) != 1 else ''}):", *lines])) from None
    except Exception as error:  # noqa: BLE001 - an unreadable manifest is no freeze either
        raise Refused(f"Refusing to start: could not verify {manifest_path} ({type(error).__name__}: {error}).") from None
    sha, tree = (pins or {}).get("manifest_sha256"), (pins or {}).get("tree_digest")
    if not _is_sha(sha):
        raise Refused(f"Refusing to start: the verifier returned no manifest_sha256 for {manifest_path}.")
    pipeline, pipeline_tree, film = _layers(manifest_path, sha, tree)
    os.environ[SHA_ENV], os.environ[TREE_ENV] = pipeline, str(pipeline_tree or "")
    if film:
        os.environ[FILM_ENV] = film
    else:
        os.environ.pop(FILM_ENV, None)
    return current()


def _is_sha(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _layers(manifest_path: str, sha: str, tree) -> tuple[str, str | None, str | None]:
    """(pipeline sha, pipeline tree, film sha) from the very bytes the verifier hashed; a film manifest names its pipeline layer."""
    try:
        raw = Path(manifest_path).read_bytes()
        manifest = json.loads(raw)
    except (OSError, ValueError) as error:
        raise Refused(f"Refusing to start: could not read back {manifest_path} ({type(error).__name__}: {error}).") from None
    if hashlib.sha256(raw).hexdigest() != sha:
        raise Refused(f"Refusing to start: {manifest_path} changed after it was verified.")
    if not isinstance(manifest, dict) or manifest.get("scope", "pipeline") == "pipeline":
        return sha, tree, None
    layer = manifest.get("pipeline_layer") if manifest.get("scope") == "film" else None
    if not isinstance(layer, dict) or not _is_sha(layer.get("manifest_sha256")):
        raise Refused(f"Refusing to start: the film manifest {manifest_path} names no pipeline layer digest.")
    return layer["manifest_sha256"], layer.get("tree_digest"), sha


def manifest_from(argument: str | None) -> str | None:
    return argument or os.environ.get(MANIFEST_ENV) or None


def current() -> dict:
    """The build this process runs, as data. The words on screen come from here."""
    sha = os.environ.get(SHA_ENV) or None
    if not sha:
        return {"frozen": False, "pipeline_sha256": None, "tree_digest": None, "short": None, "film_sha256": None, "film_short": None,
                "label": DEVELOPMENT, "receipt_line": DEVELOPMENT}
    short, film = sha[:SHORT], os.environ.get(FILM_ENV) or None
    film_short = film[:SHORT] if film else None
    return {"frozen": True, "pipeline_sha256": sha, "tree_digest": os.environ.get(TREE_ENV) or None, "short": short,
            "film_sha256": film, "film_short": film_short, "label": f"Frozen pipeline {short}",
            "receipt_line": f"pipeline {short}" + (f" · film manifest {film_short}" if film else "")}


def stamp(record: dict) -> dict:
    """A receipt the app writes, with the build it came from: the pipeline layer's sha (and the film's) when frozen, the development label when not."""
    build = current()
    return {**record, "pipeline_sha256": build["pipeline_sha256"], "film_manifest_sha256": build["film_sha256"], "build": build["label"]}
