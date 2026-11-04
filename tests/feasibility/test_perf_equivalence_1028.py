"""Never-promoted perf-1028 archive; opt in with --run-feasibility-archive.

Historical shared-source pins intentionally remain fail-closed. This experiment
is excluded before import from the default current-source qualification suite.
Full archived walks additionally require ``-m ''`` and one BLAS/HiGHS thread.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.feasibility_archive

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "experiments/feasibility/perf-1028"
SPEC = importlib.util.spec_from_file_location("perf_equivalence_1028", AUDIT / "equivalence.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)
SELECTED_MANIFEST = Path(os.environ.get("STOWAGE_PERF_EQUIVALENCE_MANIFEST", AUDIT / "corpus-manifest.json"))
CORPUS = audit.manifest(SELECTED_MANIFEST)
ROWS = CORPUS["instances"]


def test_equivalence_corpus_has_required_pinned_coverage():
    assert len(ROWS) >= 24
    assert len({row["sha256"] for row in ROWS}) == len(ROWS)
    measurements = [row for row in ROWS if row.get("measurement", False)]
    if SELECTED_MANIFEST == AUDIT / "corpus-manifest.json":
        assert {row["rung"] for row in measurements} == {"tiny", "small", "medium", "full"}
        assert len(ROWS) - len(measurements) >= 20
    else:
        assert {row["rung"] for row in ROWS} >= {"tiny", "small", "medium", "full"}
    assert {row["generator"] for row in ROWS} == {"random_yard", "simulated_yard"}
    assert {row["vessel"] for row in ROWS} >= {"feeder_500", "feeder_1000", "feeder_1700"}
    assert len(CORPUS["seeds"]) >= 5


def test_reference_and_optimized_terminal_walks_c2_off():
    row = next(row for row in ROWS if row["rung"] == "tiny")
    for seed in CORPUS["seeds"]:
        result = audit.walk(row, False, seed)
        assert result["status"] in ("complete", "dead")
        assert len(result["states"]) == len(result["actions"]) + 1


@pytest.mark.slow
@pytest.mark.parametrize("seed", CORPUS["seeds"], ids=lambda seed: f"seed-{seed}")
@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["id"])
@pytest.mark.parametrize("c2", [False, True], ids=["c2-off", "c2-on"])
def test_full_corpus_terminal_masked_rollout(row, c2, seed):
    result = audit.walk(row, c2, seed)
    assert result["status"] in ("complete", "dead")
    assert len(result["states"]) == len(result["actions"]) + 1


@pytest.mark.slow
@pytest.mark.parametrize("row", [row for row in ROWS if row.get("measurement", False)], ids=lambda row: row["id"])
@pytest.mark.parametrize("c2", [False, True], ids=["c2-off", "c2-on"])
def test_saved_measurement_prefixes(row, c2):
    actions = audit.measurement_actions(row, c2, AUDIT / "corpus-measurement-prefixes.json")
    result = audit.walk(row, c2, 1704, saved_actions=actions)
    assert len(result["actions"]) == len(actions)
    assert result["status"] in ("complete", "dead", "measurement-prefixes")
