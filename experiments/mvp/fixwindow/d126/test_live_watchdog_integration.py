"""Post-landing guard only: pinned live driver, checker, and promoted fixture.

Do not run before the D126 patches and production test have been installed.
Only AST-selected helper/test definitions are reused; the candidate integration
module is never imported and no candidate driver source is read by this test.
"""
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
LIVE_DRIVER = ROOT / "experiments/mvp/gold_tools/driver.py"
LIVE_TEST = ROOT / "tests/checker/test_external_watchdog.py"
LIVE_CHECKER = ROOT / "src/stowage/checker/stability.py"
RECEIPTS = HERE / "live-watchdog-receipts"
SHARED_GUARD = HERE / "test_watchdog_integration.py"
PINS = {
    LIVE_DRIVER: "14748e72644b7b20b8da96d81094734197cea6fa6c95e81e70857fc532f62bd4",
    LIVE_TEST: "e0bb4d81bf73e16f552a5ec7f28e87e2d7fba035564a36a45cc1dcc98304b25b",
    LIVE_CHECKER: "64c0a06761da330b5ab78efb2776c8cea12d37225f8059f581893dddfa5410e5",
}


def load_guard():
    for path, expected in PINS.items():
        assert path.is_file(), f"D126 installation missing: {path}"
        assert sha256(path.read_bytes()).hexdigest() == expected, (
            f"D126 installation/source epoch differs: {path}")
    spec = importlib.util.spec_from_file_location("d126_live_production_checker_guard", LIVE_TEST)
    staged = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = staged
    spec.loader.exec_module(staged)
    names = {"FakeClock", "FakeSignal", "extracted_stage",
             "test_actual_d126_handler_and_stage_reject_interruption"}
    definitions = [node for node in ast.parse(SHARED_GUARD.read_text()).body
                   if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    assert len(definitions) == len(names)

    class ReceiptNames(ast.NodeTransformer):
        """Change evidence labels only; preserve every execution/assertion branch."""
        replacements = 0

        def visit_Constant(self, node):
            if node.value == "watchdog-":
                self.replacements += 1
                return ast.copy_location(ast.Constant(value="live-watchdog-"), node)
            if node.value == "candidate_sha256":
                self.replacements += 1
                return ast.copy_location(ast.Constant(value="live_driver_sha256"), node)
            return node

    renamer = ReceiptNames()
    module = renamer.visit(ast.Module(body=definitions, type_ignores=[]))
    assert renamer.replacements == 2
    ast.fix_missing_locations(module)
    namespace = {
        "contextmanager": contextmanager, "sha256": sha256,
        "ast": ast, "json": json, "pytest": pytest,
        "stability_tables": stability_tables, "staged": staged,
        "CANDIDATE": LIVE_DRIVER, "STAGED_TEST": LIVE_TEST, "HERE": RECEIPTS,
        "__future__": __future__,
    }
    exec(compile(module, str(SHARED_GUARD), "exec",
                 flags=__future__.annotations.compiler_flag), namespace)
    return namespace["test_actual_d126_handler_and_stage_reject_interruption"]


@pytest.mark.parametrize("mode", ["normal", "alarm", "caught_base", "delayed_delivery"])
def test_live_driver_and_checker_watchdog_boundaries(monkeypatch, mode):
    guard = load_guard()
    RECEIPTS.mkdir(exist_ok=True)
    guard(monkeypatch, mode)
    target = RECEIPTS / f"live-watchdog-{mode}.json"
    evidence = json.loads(target.read_text())
    assert evidence["live_driver_sha256"] == PINS[LIVE_DRIVER]
    assert evidence["self_contained_test_sha256"] == PINS[LIVE_TEST]
    evidence.update({
        "live_driver_path": str(LIVE_DRIVER),
        "live_checker_sha256": PINS[LIVE_CHECKER],
        "promoted_test_path": str(LIVE_TEST),
        "shared_guard_sha256": sha256(SHARED_GUARD.read_bytes()).hexdigest(),
        "execution_kind": "post-landing production source with synthetic physics/clock",
    })
    target.write_text(json.dumps(evidence, indent=2) + "\n")
