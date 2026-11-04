"""rev-v4-h regressions for the 7.1 row cache: admission is never cached (P1 #1), the code key is a complete
fingerprint (P1 #2), and referenced instance bytes are part of the record key (P1 #3)."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[5]
BUILD = Path(__file__).resolve().parents[1] / "build.py"
REC = ROOT / "experiments/mvp/runs/test-twostep/instances/sim.tiny.8000011/record.json"


def _builder():
    spec = importlib.util.spec_from_file_location("_mvp_build", BUILD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(not REC.exists(), reason="mvp-refs fixture missing")
def test_cached_fixture_success_cannot_enter_a_normal_build(tmp_path):
    env = {**os.environ, "STOWLM_SFT_ROW_CACHE": str(tmp_path / "cache")}
    run = lambda out, *extra: subprocess.run(["uv", "run", "python", str(BUILD), "--out", str(out), *extra,
                                               "--train", str(REC), "--val", str(REC)],
                                              cwd=ROOT, env=env, capture_output=True, text=True)
    first = run(tmp_path / "sft-pilot" / "fixture", "--test-fixture")
    assert first.returncode == 0, first.stderr
    assert list((tmp_path / "cache").rglob("*.json"))  # the fixture row is cached
    normal = run(tmp_path / "normal")  # warm cache, normal mode: must still refuse the test input
    assert normal.returncode != 0
    assert "frozen MVP test/showcase split" in (normal.stdout + normal.stderr)


def test_code_key_covers_checker_securing_and_physical_data(tmp_path, monkeypatch):
    b = _builder()
    for rel in ("src/stowage/checker/rules/x.py", "src/llm/securing/assets.py", "params/fleet/ship.toml", "data/t.csv"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("v1")
    monkeypatch.setattr(b, "ROOT", tmp_path)
    base = b.code_key()
    for rel in ("src/stowage/checker/rules/x.py", "src/llm/securing/assets.py", "params/fleet/ship.toml", "data/t.csv"):
        (tmp_path / rel).write_text("v2")
        assert b.code_key() != base, rel
        base = b.code_key()


def test_record_key_includes_the_referenced_instance_bytes(tmp_path, monkeypatch):
    b = _builder()
    inst = tmp_path / "instance.json"
    inst.write_text('{"a": 1}')
    rec = tmp_path / "record.json"
    rec.write_text(json.dumps({"instance_path": str(inst)}))
    k1 = b.record_key(rec)
    inst.write_text('{"a": 2}')
    k2 = b.record_key(rec)
    inst.unlink()
    k3 = b.record_key(rec)
    assert len({k1, k2, k3}) == 3
