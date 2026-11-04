from types import ModuleType

import pytest

from experiments.mvp.pipeline.binding.review3 import watchdog


def fake_module():
    native = ModuleType("fake_faulthandler")
    native.dump_traceback_later = lambda *args, **kwargs: None
    def blocked_cancel():
        raise RuntimeError("simulated cancellation that would block")
    native.cancel_dump_traceback_later = blocked_cancel
    native.dump_traceback = lambda: "manual traceback preserved"
    native.enable = lambda: "fatal handler preserved"
    owner = ModuleType("fake_measure")
    owner.faulthandler = native
    exec("def flow(events):\n"
         "    faulthandler.dump_traceback_later(60, repeat=True)\n"
         "    events.append('immutable final row written')\n"
         "    faulthandler.cancel_dump_traceback_later()\n"
         "    events.append('returned to map checkpoint')\n", owner.__dict__)
    return owner, native


def test_private_view_avoids_cancel_failure_after_final_row():
    owner, native = fake_module()
    events = []
    with pytest.raises(RuntimeError, match="would block"):
        owner.flow(events)
    assert events == ["immutable final row written"]
    status = watchdog.install(modules=[owner])
    events = []
    owner.flow(events)
    assert events == ["immutable final row written", "returned to map checkpoint"]
    assert status["instrumentation_only"] and not status["planning_core_changed"]
    assert owner.faulthandler is not native
    with pytest.raises(RuntimeError):
        native.cancel_dump_traceback_later()  # process-wide original untouched


def test_all_other_handlers_are_preserved_and_install_is_idempotent():
    owner, native = fake_module()
    watchdog.install(modules=[owner])
    view = owner.faulthandler
    assert view.dump_traceback is native.dump_traceback
    assert view.enable is native.enable
    assert view.dump_traceback() == "manual traceback preserved"
    watchdog.install(modules=[owner])
    assert owner.faulthandler is view


def test_already_resolved_old_cancel_is_not_claimed_rescued():
    owner, native = fake_module()
    already_resolved = owner.faulthandler.cancel_dump_traceback_later
    status = watchdog.install(modules=[owner])
    assert status["cannot_rescue_inflight_cancel"]
    assert owner.faulthandler.cancel_dump_traceback_later() is None
    with pytest.raises(RuntimeError):
        already_resolved()


def test_global_standard_library_module_is_not_modified():
    import faulthandler
    dump, cancel = faulthandler.dump_traceback_later, faulthandler.cancel_dump_traceback_later
    view = watchdog.private_view(faulthandler)
    assert view is not faulthandler
    assert faulthandler.dump_traceback_later is dump
    assert faulthandler.cancel_dump_traceback_later is cancel
