"""Actual checker branches with tiny synthetic physics and fake alarm delivery."""
from __future__ import annotations

import __future__
import ast
from contextlib import contextmanager
from hashlib import sha256
import json
import math
from pathlib import Path
from types import SimpleNamespace

from stowage.checker import stability, stability_tables
from tests.checker.test_stability_in_port import isolated

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def candidate_checker(path):
    """Compile real patched check functions against existing synthetic helpers.

    Only the public provenance decorator is omitted, as in the original review
    probe; the inner checker control flow and candidate diff remain intact.
    """
    tree = ast.parse(Path(path).read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {"_check_stability", "check_stability"}]
    assert len(functions) == 2
    for function in functions:
        function.decorator_list = []
    namespace = dict(stability.__dict__)
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec",
                 flags=__future__.annotations.compiler_flag), namespace)
    return namespace["check_stability"]


def prepare_case(monkeypatch, *, patched=False, error=None, on_cache=None):
    from stowage.vessel import fleet

    def forbidden_builder(*args, **kwargs):
        raise AssertionError("Watchdog regression must not construct a real numerical ship")

    monkeypatch.setattr(fleet, "load_vessel", forbidden_builder)
    instance, geometry, _ = isolated.__wrapped__(monkeypatch)
    observation = {"cache_called": 0, "direct_equilibrium_completed": 0}
    original_equilibrium = geometry.equilibrium

    def completed(*args):
        result = original_equilibrium(*args)
        observation["direct_equilibrium_completed"] += 1
        return result

    def cache_query(*args):
        observation["cache_called"] += 1
        if on_cache is not None:
            on_cache()
        if error is not None:
            raise error
        return None

    geometry.equilibrium = completed
    monkeypatch.setattr(stability_tables, "cached_geometry", cache_query)
    function = (candidate_checker(HERE / "stability-cache-timeout.py")
                if patched else stability.check_stability)

    def check():
        return function(instance, instance.onboard, call_index=0, in_port=True)

    return check, observation


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


class FakeSignal:
    """Records timer registration only; never installs or sends a real signal."""
    SIGALRM = 14
    ITIMER_REAL = 0

    def __init__(self):
        self.handler = "original-handler"
        self.timer = (0.0, 0.0)
        self.events = []

    def getsignal(self, kind):
        return self.handler

    def signal(self, kind, handler):
        previous = self.handler
        self.handler = handler
        self.events.append(("handler", str(type(handler))))
        return previous

    def getitimer(self, kind):
        return self.timer

    def setitimer(self, kind, seconds, interval=0.0):
        previous = self.timer
        self.timer = (float(seconds), float(interval))
        self.events.append(("timer", float(seconds), float(interval)))
        return previous

    def fire(self):
        assert callable(self.handler)
        self.handler(self.SIGALRM, None)


def deadline_from_source(path, clock, signals):
    source = Path(path).read_bytes()
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "deadline")
    namespace = {"contextmanager": contextmanager, "signal": signals, "time": clock,
                 "math": math, "_deadline_stack": []}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec",
                 flags=__future__.annotations.compiler_flag), namespace)
    result = namespace["deadline"]
    result._guard_source_sha256 = sha256(source).hexdigest()
    return result


def public_observation(report, observation):
    return {**observation, "ran": report.ran, "error": report.error,
            "violations": [value.rule_id for value in report.violations],
            "accepted_part": report.ran and not report.violations}


def source_pins():
    return {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest()
        for path in (ROOT / "src/stowage/checker/stability.py",
                     ROOT / "src/stowage/checker/stability_budget.py",
                     HERE / "stability-cache-timeout.py",
                     HERE / "checker_timeout_probes.py",
                     HERE / "test_checker_watchdog.py",
                     ROOT / "tests/checker/test_stability_in_port.py",
                     ROOT / "tests/checker/test_stability_policy_fixtures.py")}
