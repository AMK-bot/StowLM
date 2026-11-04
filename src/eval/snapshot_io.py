"""One immutable byte snapshot for parsing, validation and content identity.

Evidence readers must use this snapshot's data and digest together. A later
freshness check may compare a new snapshot with it, never repin a replacement.
Pure streaming hashes of large model weights need not materialize their bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import gzip
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    path: Path
    data: bytes
    sha256: str

    def json(self) -> Any:
        return json.loads(self.data)

    def model(self, model_type):
        """Parse the frozen JSON contracts with the ordinary I/O schema rules."""
        from stowage.io import _check_version
        from stowage.model import PlannerResult
        data = gzip.decompress(self.data) if self.path.suffix == '.gz' else self.data
        payload = json.loads(data.decode('utf-8'))
        _check_version(payload.get('schema_version') if isinstance(payload, dict) else None)
        if model_type is PlannerResult:
            payload.pop('schema_version')
            later = payload.get('later_plans', [])
            plans = [payload.get('plan')]
            if isinstance(later, list):
                plans.extend(later)
            for plan in plans:
                if isinstance(plan, dict):
                    _check_version(plan.get('schema_version'))
        return model_type.model_validate(payload)

    def require_fresh(self) -> None:
        """Refuse drift; the original admitted digest remains authoritative."""
        if read_snapshot(self.path).sha256 != self.sha256:
            raise ValueError(f'file changed since validated read: {self.path}')


def read_snapshot(path: str | Path, expected_sha256: str | None = None) -> FileSnapshot:
    """Read once and return exactly those bytes with their SHA-256 digest."""
    path = Path(path)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError(f'file checksum mismatch: {path}')
    return FileSnapshot(path=path, data=data, sha256=digest)
