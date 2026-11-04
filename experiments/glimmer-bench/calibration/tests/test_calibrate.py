"""Calibration collection binding and input validation (code review w2-rev-c1, calibrate.py:32/:35). CPU only.

    uv run pytest -q -p no:cacheprovider experiments/glimmer-bench/calibration/tests -o addopts=""
"""

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from identity import write_identity  # noqa: E402

PROBS = {"pt_bf16": [.6, .3, .1], "mlx_bf16": [.6, .3, .1], "mlx_q4": [.55, .33, .12], "mlx_q4_fresh": [.55, .33, .12],
         "mlx_q3": [.3, .4, .3], "mlx_q4_swap": [.2, .2, .6]}
PROMPTS = {"a": [1, 2], "b": [3, 4, 5]}


def calibrate():
    spec = importlib.util.spec_from_file_location("calibrate_under_test", HERE / "calibrate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collection(tmp_path, prompts=PROMPTS, model=lambda tag, name: {"rev": tag}):
    (tmp_path / "prompts.json").write_text(json.dumps(prompts))
    for tag, p in PROBS.items():
        for name, ids in prompts.items():
            path = tmp_path / f"{tag}__{name}.npy"
            np.save(path, np.log(np.array([p] * len(ids))))
            write_identity(tmp_path, tag, name, ids, path, model=model(tag, name), settings={"keep": 64},
                           software={"x": "1"})
    return tmp_path


def run(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["calibrate.py", str(tmp_path), str(tmp_path / "result.json")])
    calibrate().main()
    return json.loads((tmp_path / "result.json").read_text())


def test_bound_collection_is_analysed_and_reports_its_identity(tmp_path, monkeypatch):
    result = run(collection(tmp_path), monkeypatch)
    assert result["implementation_floor_mean_kl"] == pytest.approx(0.0)
    assert set(result["collection"]["identities"]) == {"pt_bf16", "mlx_bf16", "mlx_q4", "mlx_q4_fresh", "mlx_q3",
                                                       "mlx_q4_swap"}
    assert result["collection"]["prompts"]["a"]["tokens"] == 2


def test_logits_for_other_prompt_ids_are_refused(tmp_path, monkeypatch):
    """Reviewer P3: same-named arrays for prompt IDs [1,2] must not pass for [999,888]."""
    collection(tmp_path)
    (tmp_path / "prompts.json").write_text(json.dumps({"a": [999, 888], "b": [3, 4, 5]}))
    with pytest.raises(ValueError, match="different prompt token IDs"):
        run(tmp_path, monkeypatch)


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_non_finite_logits_are_refused(tmp_path, monkeypatch, bad):
    """Reviewer P3: NaN implementation-control logits used to yield NaN metrics and a 'separable' tolerance."""
    collection(tmp_path)
    path = tmp_path / "mlx_bf16__a.npy"
    np.save(path, np.full((2, 3), bad))
    write_identity(tmp_path, "mlx_bf16", "a", [1, 2], path, model={"rev": "mlx_bf16"}, settings={"keep": 64},
                   software={"x": "1"})
    with pytest.raises(ValueError, match="non-finite"):
        run(tmp_path, monkeypatch)
    assert not (tmp_path / "result.json").exists()


def test_partial_mixed_or_tampered_collections_are_refused(tmp_path, monkeypatch):
    collection(tmp_path)
    (tmp_path / "mlx_q3__b.identity.json").unlink()
    with pytest.raises(ValueError, match="partial collection"):
        run(tmp_path, monkeypatch)
    collection(tmp_path, model=lambda tag, name: {"rev": tag + ("-other" if name == "b" and tag == "mlx_q4" else "")})
    with pytest.raises(ValueError, match="mixed"):
        run(tmp_path, monkeypatch)
    collection(tmp_path)
    np.save(tmp_path / "mlx_q4__a.npy", np.zeros((2, 3)))  # replaced after its identity was recorded
    with pytest.raises(ValueError, match="array bytes"):
        run(tmp_path, monkeypatch)
