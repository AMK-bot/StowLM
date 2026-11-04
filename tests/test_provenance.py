"""Tiny-run stub mechanism: overrides route calls, taint provenance, and are refused by require_clean."""

from __future__ import annotations

import pytest

import stowage.interfaces  # noqa: F401
from stowage import moves
from stowage.frozen import (
    StubTaintError,
    active_stubs,
    current_provenance,
    require_clean,
    stub_override,
    unimplemented_interfaces,
)
from stowage.model import MovePlan, Provenance


def test_override_routes_and_is_scoped() -> None:
    """Works whether or not the real move engine has landed: outside the block the call must not reach the stub."""
    sentinel = object()

    def tiny(instance, plan, *, stop_at_first=True):  # noqa: ANN001, ANN202
        return sentinel

    with stub_override("moves.apply_plan", tiny, reason="2.3 not done; tiny run"):
        assert moves.apply_plan(None, None) is sentinel
        assert active_stubs() == {"moves.apply_plan": "2.3 not done; tiny run"}
    assert active_stubs() == {}
    try:
        out = moves.apply_plan(None, None)  # real impl (or stub) with junk args: raises
    except Exception:  # noqa: BLE001
        out = None
    assert out is not sentinel


def test_provenance_is_tainted_inside_override_and_refused() -> None:
    with stub_override("checker.check", lambda *a, **k: None, reason="tiny"):
        prov = current_provenance(created_by="test")
    assert prov.tainted and "checker.check" in prov.stubs
    assert set(prov.versions) >= {"schema", "move_engine", "checker", "feasibility", "linear_model", "ballast"}
    with pytest.raises(StubTaintError):
        require_clean(prov, what="gold-plan writer")
    require_clean(current_provenance(), what="gold-plan writer")  # clean outside the block


def test_taint_is_contagious_through_merge_and_json() -> None:
    tainted = Provenance(stubs={"checker.check": "tiny"}, parents=["inst-1"])
    clean = Provenance(versions={"checker": "1.0"})
    merged = clean.merge(tainted)
    assert merged.tainted and merged.parents == ["inst-1"] and merged.versions == {"checker": "1.0"}
    plan = MovePlan(id="p", instance_id="inst-1", call_index=0, provenance=merged)
    again = MovePlan.model_validate_json(plan.model_dump_json())
    with pytest.raises(StubTaintError):
        require_clean(again.provenance, what="eval harness")


def test_unknown_interface_and_empty_reason_rejected() -> None:
    with pytest.raises(KeyError):
        with stub_override("no.such", lambda: None, reason="x"):
            pass
    with pytest.raises(ValueError):
        with stub_override("checker.check", lambda: None, reason=" "):
            pass


def test_unimplemented_list_is_subset_of_frozen() -> None:
    from stowage.frozen import FROZEN

    assert set(unimplemented_interfaces()) <= set(FROZEN)


def test_stub_output_is_stamped_and_require_clean_reads_artifacts() -> None:
    from stowage import checker
    from stowage.model import CheckReport

    with stub_override("checker.check", lambda *a, **k: CheckReport(passed=True), reason="tiny"):
        report = checker.check(None, None)
    assert report.provenance.stubs == {"checker.check": "tiny"}
    with pytest.raises(StubTaintError):
        require_clean(report, what="eval harness")
    plan = MovePlan(id="p", instance_id="i", call_index=0, provenance=Provenance(stubs={"x.y": "tiny"}))
    with pytest.raises(StubTaintError):
        require_clean(plan, what="gold-plan writer")
    with pytest.raises(TypeError):
        require_clean("not an artifact", what="x")


def test_merge_records_parent_ids_and_mixed_versions() -> None:
    out = Provenance(versions={"checker": "2.0"}).merge(Provenance(versions={"checker": "1.0"}), parent_ids=["inst"])
    assert out.parents == ["inst"] and out.input_versions == {"checker": ["1.0"]}


def test_require_clean_rejects_tainted_later_plan() -> None:
    from stowage.model import PlannerResult

    result = PlannerResult(
        status="ok", planner="test", planner_version="1",
        plan=MovePlan(id="now", instance_id="i", call_index=0),
        later_plans=[MovePlan(id="later", instance_id="i", call_index=1,
                              provenance=Provenance(stubs={"checker.check": "tiny later call"}))],
    )
    with pytest.raises(StubTaintError, match="tiny later call"):
        require_clean(result, what="gold-plan writer")


def test_require_clean_checks_nested_artifacts_and_handles_cycles() -> None:
    from pydantic import Field
    from stowage.model.base import StowModel

    class Bundle(StowModel):
        provenance: Provenance = Field(default_factory=Provenance)
        components: dict[str, list[object]] = Field(default_factory=dict)

    bundle = Bundle()
    bundle.components["self"] = [bundle]
    require_clean(bundle, what="nested consumer")
    bundle.components["inputs"] = [Provenance(stubs={"inputs.fixture": "tiny"})]
    with pytest.raises(StubTaintError, match="inputs.fixture"):
        require_clean(bundle, what="nested consumer")


@pytest.mark.parametrize("start_method", ["fork", "spawn"])
def test_workers_do_not_inherit_stub_overrides(start_method: str) -> None:
    import multiprocessing

    if start_method not in multiprocessing.get_all_start_methods():
        pytest.skip(f"{start_method} is unavailable")
    ctx = multiprocessing.get_context(start_method)
    receiver, sender = ctx.Pipe(duplex=False)
    with stub_override("checker.check", lambda *a, **k: None, reason="parent only"):
        # A builtin target is importable under pytest's importlib mode, including spawn.
        worker = ctx.Process(target=exec, args=(
            "from stowage.frozen import active_stubs\nconnection.send(active_stubs())\nconnection.close()",
            {"connection": sender},
        ))
        worker.start()
        sender.close()
        try:
            assert receiver.poll(15), "worker did not report its stub state"
            assert receiver.recv() == {}
        finally:
            receiver.close()
            worker.join(timeout=15)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
        assert worker.exitcode == 0
        assert active_stubs() == {"checker.check": "parent only"}
    assert active_stubs() == {}


def test_fork_cannot_restore_overrides_from_contexts_or_nested_unwinding() -> None:
    import contextvars
    import multiprocessing

    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("fork is unavailable")
    ctx = multiprocessing.get_context("fork")
    receiver, sender = ctx.Pipe(duplex=False)
    with stub_override("checker.check", lambda *a, **k: None, reason="parent outer"):
        copied = contextvars.copy_context()
        with stub_override("checker.check", lambda *a, **k: None, reason="parent inner"):
            # Manually unwinding a separate nested manager in the child emulates returning
            # across an inherited `with` after a raw os.fork(). Parent state stays intact.
            inner = stub_override("checker.check", lambda *a, **k: None, reason="parent innermost")
            inner.__enter__()
            try:
                worker = ctx.Process(target=exec, args=(
                    "from stowage.frozen import active_stubs, stub_override\n"
                    "states = [copied.run(active_stubs)]\n"
                    "inner.__exit__(None, None, None)\n"
                    "states.append(active_stubs())\n"
                    "with stub_override('checker.check', lambda *a, **k: None, reason='child explicit'):\n"
                    "    states.append(active_stubs())\n"
                    "states.append(active_stubs())\n"
                    "connection.send(states)\nconnection.close()",
                    {"connection": sender, "copied": copied, "inner": inner},
                ))
                worker.start()
                sender.close()
                try:
                    assert receiver.poll(15), "worker did not report its stub state"
                    assert receiver.recv() == [{}, {}, {"checker.check": "child explicit"}, {}]
                finally:
                    receiver.close()
                    worker.join(timeout=15)
                    if worker.is_alive():
                        worker.terminate()
                        worker.join(timeout=5)
                assert worker.exitcode == 0
            finally:
                inner.__exit__(None, None, None)
        assert active_stubs() == {"checker.check": "parent outer"}


def test_require_clean_checks_provenance_property_on_model() -> None:
    from stowage.model.base import StowModel

    class Artifact(StowModel):
        @property
        def provenance(self) -> Provenance:
            return Provenance(stubs={"checker.check": "property taint"})

    with pytest.raises(StubTaintError, match="property taint"):
        require_clean(Artifact(), what="property consumer")


def test_artifact_stubs_collects_shared_nested_inputs_without_mutation() -> None:
    from types import SimpleNamespace
    from stowage.frozen import _artifact_stubs

    first = Provenance(stubs={"checker.check": "first"})
    second = Provenance(stubs={"planner.adapter": "second"})
    inputs = [first, {"adapter": (second, first)}]
    inputs.append(inputs)
    artifact = SimpleNamespace(provenance=Provenance(), later_plans=inputs)
    assert _artifact_stubs(artifact) == {"checker.check": "first", "planner.adapter": "second"}
    assert artifact.provenance.stubs == {}
    assert first.stubs == {"checker.check": "first"}
