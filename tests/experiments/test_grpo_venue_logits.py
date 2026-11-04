"""Small synthetic distributions exercise KL direction without model dependencies."""
import json
from pathlib import Path
import runpy
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / "experiments/grpo-venue"


def test_compare_reports_bf16_to_mlx_direction(tmp_path, monkeypatch):
    (tmp_path / "prompts.json").write_text(json.dumps({"toy": [1]}))
    for name, prob in [("pt_bf16_base", [.9, .1]), ("pt_bf16_adapter", [.8, .2]),
                       ("mlx_q4_base", [.5, .5]), ("mlx_q4_adapter", [.6, .4])]:
        np.save(tmp_path / f"{name}__toy.npy", np.log([prob]))
    monkeypatch.setattr(sys, "argv", ["compare_logits.py", str(tmp_path)])
    runpy.run_path(str(FOLDER / "compare_logits.py"), run_name="__main__")
    row = json.loads((tmp_path / "compare.json").read_text())[0]
    assert row["kl_base_bf16_to_mlx"] == pytest.approx(.3680642071684971)
    assert row["kl_base_bf16_to_mlx"] != pytest.approx(.5108256237659907)
    assert row["kl_base_q4_vs_bf16"] == row["kl_base_bf16_to_mlx"]  # historical alias
    assert row["kl_direction"] == "PyTorch bf16 || MLX (precision identified by mlx tag)"


@pytest.mark.parametrize("filename", ["compare_logits.json", "compare_q4_vs_bf16.json"])
def test_stored_measurements_declare_actual_kl_direction(filename):
    for row in json.loads((FOLDER / filename).read_text()):
        assert row["kl_base_bf16_to_mlx"] == row["kl_base_q4_vs_bf16"]
        assert row["kl_adapted_bf16_to_mlx"] == row["kl_adapted_q4_vs_bf16"]
        assert "bf16 || MLX" in row["kl_direction"]
