"""Execute the exact staged regression against pinned before/after checker code."""
from __future__ import annotations

import __future__
import ast
from hashlib import sha256
import importlib.util
import json
from pathlib import Path

import pytest

from stowage.checker import stability


HERE = Path(__file__).resolve().parent
STAGED_TEST = HERE / "tests/checker/test_external_watchdog.py"
SOURCES = {
    "before": HERE.parent / "d121-watchdog/stability-before.py",
    "after": HERE.parent / "d121-watchdog/stability-cache-timeout.py",
}
SOURCE_SHA256 = {
    "before": "72c4ec7390b541dd5dd397b1a18c433547f1851b04a49b8cd49589f7da4a2e97",
    "after": "64c0a06761da330b5ab78efb2776c8cea12d37225f8059f581893dddfa5410e5",
}


def checker_from_source(path):
    tree = ast.parse(path.read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {"_check_stability", "check_stability"}]
    assert len(functions) == 2
    for function in functions:
        # Public provenance decoration is omitted; branch code is unchanged.
        function.decorator_list = []
    namespace = dict(stability.__dict__)
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec",
                 flags=__future__.annotations.compiler_flag), namespace)
    return namespace["check_stability"]


@pytest.mark.parametrize("version", ["before", "after"])
def test_exact_staged_cache_timeout_regression_before_after(monkeypatch, version):
    source = SOURCES[version]
    assert sha256(source.read_bytes()).hexdigest() == SOURCE_SHA256[version]
    spec = importlib.util.spec_from_file_location("d126_staged_watchdog", STAGED_TEST)
    staged = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(staged)
    fixture = staged.synthetic_checker.__wrapped__(monkeypatch)
    monkeypatch.setattr(stability, "check_stability", checker_from_source(source))
    regression = staged.test_optional_cache_timeout_is_unavailable_without_direct_fallback
    if version == "before":
        with pytest.raises(AssertionError):
            regression(monkeypatch, fixture)
        assert fixture[1]["direct_equilibrium_completed"] == 1
    else:
        regression(monkeypatch, fixture)
        assert fixture[1]["direct_equilibrium_completed"] == 0
    (HERE / f"cache-timeout-{version}.json").write_text(json.dumps({
        "checker_source": str(source),
        "checker_sha256": SOURCE_SHA256[version],
        "staged_test_sha256": sha256(STAGED_TEST.read_bytes()).hexdigest(),
        "exact_regression_passes": version == "after",
        **fixture[1],
        "actual_signals_used": False,
        "real_numerical_ship_builder_used": False,
        "input_kind": "self-contained synthetic checker fixture",
    }, indent=2) + "\n")
