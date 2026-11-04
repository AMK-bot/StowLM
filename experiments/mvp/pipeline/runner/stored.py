"""Application-pinned stored inputs. Trust anchors never come from the input."""
from pathlib import Path
import hashlib

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json

HERE = Path(__file__).resolve().parent
BUNDLED_PLAN_SHA256 = '7a71ebe8f5f8f5dc1741f02bd4d7bb85fb4fecee8a80f814e7d12498467780ac'
BUNDLED_READINGS_SHA256 = '242de7df2a800444a1d90ac0cd850364482e2f0758ca6e29895ab8231294ba12'
BUNDLED_CANDIDATE_SHA256 = '2cfa1e6868210ba2ffda781b3af738ea2183324219f79b5b65bb005d8475cda6'


def authenticate_file(path, *, expected_sha256=None, bundled=None, bundled_sha256=None, data=None):
    path = Path(path).resolve()
    expected = expected_sha256
    if expected is None and bundled is not None and path == Path(bundled).resolve():
        expected = bundled_sha256
    actual = hashlib.sha256(data if data is not None else path.read_bytes()).hexdigest()
    if not expected or actual != expected:
        raise ValueError('Stored file is not authenticated. Supply its independently trusted SHA256 or use the bundled fixture.')
    return expected


def authenticate_candidate(candidate, *, expected_sha256=None):
    expected = expected_sha256 or BUNDLED_CANDIDATE_SHA256
    if sha256_json(candidate) != expected:
        raise ValueError('Stored plan is not authenticated. Supply its independently trusted canonical SHA256 or use the bundled fixture.')
    return expected
