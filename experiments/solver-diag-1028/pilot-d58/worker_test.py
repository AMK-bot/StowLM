"""Harness regression: decoder's successor engine is the completion authority."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
import stowage.feasibility


def test_token_replay_checks_successor_and_end_marker(monkeypatch):
    spec = spec_from_file_location("d58_worker", Path(__file__).with_name("worker.py"))
    worker = module_from_spec(spec)
    spec.loader.exec_module(worker)
    instance = tiny_instance(0, n_load=1)
    space = get_action_space()
    original = make_feasibility_engine(instance, space,
                                      config=FeasibilityConfig(extra={"global_screen": 0}))
    completed = original.copy()
    completed.push(completed.legal_actions()[0])
    assert completed.is_complete()
    # Unit fixture has no hull. Only bypass the C2 availability admission guard;
    # the real CPU mask, tokenizer and action admission still execute unchanged.
    original.diagnostics["global_screen"] = "on"
    monkeypatch.setattr(stowage.feasibility, "make_feasibility_engine",
                        lambda *_args, **_kw: original)
    replay = worker.replay_mask(instance, completed.to_plan())
    assert replay["passed"] and replay["decoder_status"] == "complete"
    assert replay["tokens"] > 0 and replay["pushed"] == 1
    assert not original.is_complete()  # decoder uses successor copies


def test_early_end_marker_is_rejected(monkeypatch):
    spec = spec_from_file_location("d58_worker", Path(__file__).with_name("worker.py"))
    worker = module_from_spec(spec)
    spec.loader.exec_module(worker)
    instance = tiny_instance(0, n_load=1)
    space = get_action_space()
    original = make_feasibility_engine(instance, space,
                                      config=FeasibilityConfig(extra={"global_screen": 0}))
    original.diagnostics["global_screen"] = "on"
    monkeypatch.setattr(stowage.feasibility, "make_feasibility_engine",
                        lambda *_args, **_kw: original)
    replay = worker.replay_mask(instance, space.expand(instance, []))
    assert not replay["passed"]
    assert replay["failure_kind"] == "end_marker_rejected"
