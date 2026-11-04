"""Fresh synthetic model-builder variants; no production terminal data."""
import sys
from pathlib import Path

# The brief flow imports experiments.mvp.reader and .voice; `python -m app` gets the repo root from its cwd, pytest doesn't.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


import pytest

from stowage.model import MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.moves import HatchCoverOff, HatchCoverOn, Load, Relocate


@pytest.fixture
def replay_case():
    instance = build_instance()
    _, dg, special, _ = instance.load_list
    plan = MovePlan(
        id="ui.replay.contract", instance_id=instance.id, call_index=0,
        moves=[
            Relocate(call_index=0, container_id=dg, src="B-001-01-01", dst="B-002-01-01"),
            HatchCoverOff(call_index=0, cover_id="HC-02-1"),
            Load(call_index=0, container_id=special, src="A-003-01-01", dst="0010102"),
            HatchCoverOn(call_index=0, cover_id="HC-02-1"),
            Load(call_index=0, container_id=dg, src="B-002-01-01", dst="0010182"),
        ],
    )
    return instance, plan


@pytest.fixture(autouse=True)
def _no_check_cache(monkeypatch):
    """Tests run the checker fresh (many monkeypatch checker internals); test_check_cache.py re-enables it."""
    monkeypatch.setenv("STOWLM_NO_CHECK_CACHE", "1")
