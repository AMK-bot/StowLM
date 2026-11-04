"""Actual queued D126 handler/stage integrated with real checker catch paths."""
from __future__ import annotations

import __future__
import ast
from contextlib import contextmanager
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from stowage.checker import stability_tables

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
CANDIDATE = HERE.parent / "d121-watchdog/D126/gold-driver-d126-candidate.py"
STAGED_TEST = HERE / "tests/checker/test_external_watchdog.py"
spec = importlib.util.spec_from_file_location("d126_self_contained_checker_guard", STAGED_TEST)
staged = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = staged
spec.loader.exec_module(staged)


class FakeClock:
    now = 0.0

    def monotonic(self):
        return self.now

    def perf_counter(self):
        return self.now

    def process_time(self):
        return 0.0


class FakeSignal:
    SIGALRM = 14
    ITIMER_REAL = 0

    def __init__(self):
        self.handler = "original-handler"
        self.timer = (0.0, 0.0)

    def getsignal(self, *args):
        return self.handler

    def signal(self, kind, handler):
        previous, self.handler = self.handler, handler
        return previous

    def getitimer(self, *args):
        return self.timer

    def setitimer(self, kind, seconds, interval=0.0):
        previous, self.timer = self.timer, (float(seconds), float(interval))
        return previous

    def fire(self):
        self.handler(self.SIGALRM, None)


def extracted_stage(clock, signals):
    source = CANDIDATE.read_bytes()
    tree = ast.parse(source)
    private = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name == "_WatchdogExpired")
    deadline = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "deadline")
    stage = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                 and node.name == "stage")
    wrapper = ast.parse("def make_stage(row):\n    current_stage = 'input'\n    target = None\n    return None\n").body[0]
    wrapper.body[-1:] = [stage, ast.Return(value=ast.Name(id="stage", ctx=ast.Load()))]
    module = ast.fix_missing_locations(ast.Module(body=[private, deadline, wrapper], type_ignores=[]))
    writes = []
    namespace = {"contextmanager": contextmanager, "signal": signals, "time": clock,
                 "child_cpu_s": lambda: 0.0,
                 "atomic_json": lambda target, row: writes.append(dict(row))}
    exec(compile(module, str(CANDIDATE), "exec", flags=__future__.annotations.compiler_flag), namespace)
    row = {"passed": False, "stages": {}}
    return namespace["make_stage"](row), namespace["_WatchdogExpired"], row, sha256(source).hexdigest()


@pytest.mark.parametrize("mode", ["normal", "alarm", "caught_base", "delayed_delivery"])
def test_actual_d126_handler_and_stage_reject_interruption(monkeypatch, mode):
    part, observed = staged.synthetic_checker.__wrapped__(monkeypatch)
    clock, signals = FakeClock(), FakeSignal()
    stage, expired_type, row, digest = extracted_stage(clock, signals)
    caught = {"inner_baseexception": False, "part_completed": False}

    def cache_query(*args):
        if mode == "normal":
            return None
        clock.now = 121.0
        if mode == "alarm":
            signals.fire()
        elif mode == "caught_base":
            try:
                signals.fire()
            except BaseException as error:
                assert isinstance(error, expired_type)
                caught["inner_baseexception"] = True
        return None

    monkeypatch.setattr(stability_tables, "cached_geometry", cache_query)
    unavailable = False
    try:
        with stage("checker", 120.0) as details:
            report = part()
            assert report.ran and not report.violations
            caught["part_completed"] = True
            details["passed"] = True
    except TimeoutError:
        unavailable = True
    assert not issubclass(expired_type, Exception)
    assert unavailable == (mode != "normal")
    assert signals.handler == "original-handler" and signals.timer == (0.0, 0.0)
    details = row["stages"]["checker"]
    if mode == "normal":
        assert details["passed"] and not row.get("unavailable", False)
    else:
        assert details["passed"] is False
        assert details["unavailable"] and details["watchdog_timeout"]
        assert row["unavailable"] and row["watchdog_timeout"]
        assert row["operational_status"] == "unavailable"
    if mode == "alarm":
        assert caught["part_completed"] is False
        assert observed["direct_equilibrium_completed"] == 0
    else:
        assert caught["part_completed"] is True
        assert observed["direct_equilibrium_completed"] == 1
    if mode == "caught_base":
        assert caught["inner_baseexception"]
    evidence = {"mode": mode, "candidate_sha256": digest, "row": row,
                "observations": {**caught, **observed}, "unavailable": unavailable,
                "actual_signals_used": False, "full_gold_worker_run": False,
                "numerical_ship_builder_run": False,
                "synthetic_clock": True, "row_timing_fields_are_not_measurements": True,
                "self_contained_test_sha256": sha256(STAGED_TEST.read_bytes()).hexdigest()}
    (HERE / ("watchdog-" + mode + ".json")).write_text(json.dumps(evidence, indent=2) + "\n")
