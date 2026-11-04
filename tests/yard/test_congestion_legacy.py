"""Full off-state outputs match the pre-D72 simulator for overlapping traffic."""
from dataclasses import asdict
from hashlib import sha256
import importlib.util
import json
from pathlib import Path

from pydantic_core import to_jsonable_python
import pytest

from stowage.ports import load_simulator_params
from stowage.rules.catalogue import rule_freeze_v1
from stowage.yard import simulate_port


@pytest.mark.parametrize("noise,expected", [
    (0, "8f243596a0f8d8ab51ac5af49c58df8aa19ed4f23fbe705af0fd50c5c5284c92"),
    (.2, "55c2b7413a28d50415ea51f636f569febacee316bf36ea895803d48c840f417b"),
    (.5, "abd6118105addb42918ddd5733b4fc73c1e781e8a66456c28db003d2dd6b2601"),
    (.8, "082fee95bd5b9c4bd3c9effd091c76b66a3ddd897fd0d9b826ea77b27022bfef"),
])
def test_overlapping_imports_exports_preserve_full_legacy_payload(noise, expected):
    path = Path(__file__).with_name("d72_measurement.py")
    spec = importlib.util.spec_from_file_location("d72_legacy_workload", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    with rule_freeze_v1(enabled=False):
        result = simulate_port(fixture.workload(), load_simulator_params("laem_chabang_like"),
                               seed=7, noise=noise)
    # Captured with the saved pre-D72 source, same inputs and dependency epoch;
    # every output field, including version metadata, participates in the hash.
    encoded = json.dumps(to_jsonable_python(asdict(result)), sort_keys=True, separators=(",", ":")).encode()
    assert sha256(encoded).hexdigest() == expected
