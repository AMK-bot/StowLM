"""Offline proposal sources only. No model import, API call, or GPU access."""
from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from experiments.mvp.strategy.proposer.registration import validate_collection_provenance


def read_source(path: Path | str, expected_sha256: str | None = None) -> tuple[bytes, str]:
    """Read once, hash the exact parsed bytes, and reject a changed source."""
    data = Path(path).read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and actual != expected_sha256:
        raise ValueError("provider source changed: sha256 does not match run identity")
    return data, actual


@dataclass(frozen=True)
class Proposal:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


class DryRunProvider:
    """Repeat a fixed text, or the canonical unrestricted permission layout.

    This exercises parsing and evaluation; it does not simulate model quality.
    Each call is an independent proposal with no evaluator feedback.
    """

    def __init__(self, text: str | None = None, *, source_sha256: str | None = None):
        self.text = text
        self.source_sha256 = source_sha256

    def propose(self, *, instance, map_id: str, arm: str, attempt: int, prompt: str,
                input_sha256: str | None = None) -> Proposal:
        text = self.text
        if text is None:
            from experiments.mvp.strategy.headroom.layout import unrestricted_layout
            from experiments.mvp.strategy.proposer.format import format_layout
            text = format_layout(unrestricted_layout(instance), instance)
        metadata = {"provider": "dry-run", "model_called": False,
                    "canned": "unrestricted" if self.text is None else "fixed-text"}
        if self.source_sha256 is not None:
            metadata["source_sha256"] = self.source_sha256
        return Proposal(text, metadata)


class JsonlProvider:
    """Replay responses bound to registered independent zero-shot collection.

    Each row requires map_id, arm, attempt (1-based), text and a
    collection_provenance object. The collection hashes/settings are checked
    against the current input, actual prompt and fixed registration at propose.
    Missing or unverifiable responses raise rather than recycle another try.
    """

    def __init__(self, path: Path | str, *, expected_sha256: str | None = None):
        self.path = Path(path)
        source, self.sha256 = read_source(self.path, expected_sha256)
        self.rows: dict[tuple[str, str, int], dict] = {}
        for lineno, line in enumerate(source.decode("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            key = (row["map_id"], row["arm"], row["attempt"])
            if (not isinstance(key[0], str) or key[1] not in {"spark", "glimmer"}
                    or type(key[2]) is not int or key[2] < 1 or not isinstance(row["text"], str)):
                raise ValueError(f"invalid proposal row at line {lineno}")
            if key in self.rows:
                raise ValueError(f"duplicate proposal key at line {lineno}: {key}")
            self.rows[key] = row

    def propose(self, *, instance, map_id: str, arm: str, attempt: int, prompt: str,
                input_sha256: str | None = None) -> Proposal:
        key = (map_id, arm, attempt)
        if key not in self.rows:
            raise LookupError(f"no imported proposal for {key}")
        row = self.rows[key]
        if set(row) - {"map_id", "arm", "attempt", "text", "collection_provenance", "usage"}:
            raise ValueError("proposal row has unregistered collection fields")
        provenance = validate_collection_provenance(row.get("collection_provenance"),
            arm=arm, attempt=attempt, prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            input_sha256=input_sha256)
        return Proposal(row["text"], {"provider": "jsonl", "source_sha256": self.sha256,
                                       "collection_provenance": provenance,
                                       "source_row": deepcopy({k: v for k, v in row.items() if k != "text"}),
                                       "model_called": False})


def make_provider(name: str, *, proposals: str | Path | None = None,
                  canned_text: str | Path | None = None,
                  expected_sha256: str | None = None):
    if name == "dry-run":
        if canned_text:
            data, source_sha256 = read_source(canned_text, expected_sha256)
            return DryRunProvider(data.decode("utf-8"), source_sha256=source_sha256)
        if expected_sha256 is not None:
            raise ValueError("expected provider digest requires a source file")
        return DryRunProvider()
    if name == "jsonl" and proposals:
        return JsonlProvider(proposals, expected_sha256=expected_sha256)
    raise ValueError("only offline dry-run and jsonl providers exist; jsonl requires --proposals")
