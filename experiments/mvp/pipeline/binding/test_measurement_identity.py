import copy
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.binding.measurement_identity import build_identity, compatibility, attempt_key


def identity(path, *, config=1, source="a", native="n", engine="e"):
    return build_identity(input_path=path, cell="tiny", profile="default", rule=None,
        settings={"config": {"value": config}, "budget_s": 120, "finalize_s": 600},
        engine_identity={"passed": True, "identity_sha256": engine, "native_manifest_sha256": native},
        implementation={"source_sha256": source, "loaded_code_sha256": source})


@pytest.mark.parametrize("passed", [True, False])
@pytest.mark.parametrize("change", ["input", "config", "source", "native", "engine"])
def test_cached_pass_and_failure_require_exact_current_identity(tmp_path, passed, change):
    path = tmp_path / "input.json"
    path.write_text('{"same_path": 1}')
    old = identity(path)
    row = {"passed": passed, "measurement_identity": old}
    assert compatibility(row, old) == (True, [])
    options = {}
    if change == "input":
        path.write_text('{"same_path": 2}')
    elif change == "config":
        options[change] = 2
    else:
        options[change] = "changed"
    valid, reasons = compatibility(row, identity(path, **options))
    assert not valid and reasons
    assert row["measurement_identity"] == old


def test_legacy_pass_and_forged_identity_are_never_upgraded(tmp_path):
    path = tmp_path / "input"; path.write_bytes(b"x")
    expected = identity(path)
    assert not compatibility({"passed": True}, expected)[0]
    forged = copy.deepcopy(expected); forged["payload"]["seed"] = 99
    assert not compatibility({"measurement_identity": forged}, expected)[0]


def test_attempt_keys_do_not_replace_earlier_same_identity_attempt(tmp_path):
    path = tmp_path / "input"; path.write_bytes(b"x")
    expected = identity(path)
    assert attempt_key(expected, "first") != attempt_key(expected, "second")


def test_default_settings_are_resolved_not_none():
    from types import SimpleNamespace
    from engine.config import Config
    from dataclasses import asdict
    from experiments.mvp.pipeline.binding.measurement_identity import resolved_settings
    instance = SimpleNamespace(load_list=["a"], onboard=SimpleNamespace(placements=[]))
    resolved = resolved_settings(instance, None, None, None)
    assert resolved["config"] == asdict(Config())
    assert resolved["budget_s"] == 120 and resolved["finalize_s"] == 600
    assert resolved_settings(instance, Config(), 90, 30)["effective_finalize_s"] == 300


def test_implementation_covers_every_binding_python_file_pins_and_loaded_code(monkeypatch):
    import experiments.mvp.pipeline.binding.measurement_identity as module
    root = Path(module.__file__).parent
    before = module.implementation_identity(root)
    assert set(path.name for path in root.glob("*.py")) <= set(before["source_files"])
    assert "provenance-pins.json" in before["source_files"]
    probe = lambda: "loaded replacement"
    probe.__module__ = module.__name__
    monkeypatch.setattr(module, "_loaded_probe", probe, raising=False)
    after = module.implementation_identity(root)
    assert before["source_sha256"] == after["source_sha256"]
    assert before["loaded_code_sha256"] != after["loaded_code_sha256"]
