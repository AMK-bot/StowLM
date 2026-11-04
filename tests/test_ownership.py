"""Historical ownership map: v4 task ids have homes; code homes sit in existing packages."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from stowage.ownership import TASK_HOMES

ROOT = Path(__file__).resolve().parents[1]


def test_every_tier_1_to_5_task_has_a_home() -> None:
    # The frozen registry predates the v5/v6 task-id reset.
    plan = ROOT / "docs" / "archive" / "plan-v4-2026-10-31.md"
    if not plan.is_file():
        pytest.skip("archived v4 plan not present (docs are untracked)")
    ids = set(re.findall(r"^- \[.\] \*\*(\d+\.\d+) ", plan.read_text(), flags=re.M))
    wanted = {t for t in ids if 1 <= int(t.split(".")[0]) <= 5}
    assert wanted, "no tier 1–5 tasks parsed from archived v4 plan"
    missing = sorted(wanted - set(TASK_HOMES), key=lambda s: tuple(map(int, s.split("."))))
    assert not missing, f"tasks without a home path: {missing}"


def test_src_homes_live_in_existing_packages() -> None:
    for task, (_title, paths, _tests) in TASK_HOMES.items():
        for p in paths:
            if not p.startswith("src/"):
                continue
            parent = (ROOT / p).parent if not p.endswith("/") else ROOT / p
            assert (parent / "__init__.py").is_file(), f"{task}: package for {p} missing"


def test_no_path_owned_twice_except_sequential_ui() -> None:
    from itertools import combinations
    from stowage.ownership import LATER_HOMES

    homes = {task: paths + tests for task, (_, paths, tests) in TASK_HOMES.items()}
    homes.update(LATER_HOMES)
    for (task_a, paths_a), (task_b, paths_b) in combinations(homes.items(), 2):
        if {task_a, task_b} == {"4.9", "5.8"}:
            continue
        for left in paths_a:
            for right in paths_b:
                assert not _overlap(left, right), f"{task_a}: {left} overlaps {task_b}: {right}"


def test_ownership_documentation_matches_map() -> None:
    from stowage.ownership import LATER_HOMES

    document = (ROOT / "docs/architecture.md").read_text()
    table = document.split("## Module ownership\n", 1)[1].split("## Tech choices", 1)[0]
    rows = {}
    for line in table.splitlines():
        if re.match(r"\| \d+\.\d+ \|", line):
            task, paths, tests = (part.strip() for part in line.strip("|").split("|"))
            rows[task] = (tuple(re.findall(r"`([^`]+)`", paths)),
                          tuple(re.findall(r"`([^`]+)`", tests)))
    expected = {task: (paths, tests) for task, (_, paths, tests) in TASK_HOMES.items()}
    expected.update({task: (paths, ()) for task, paths in LATER_HOMES.items()})
    assert rows == expected


def test_all_source_files_have_a_home() -> None:
    from fnmatch import fnmatchcase
    from stowage.ownership import LATER_HOMES

    paths = [p for _, homes, _ in TASK_HOMES.values() for p in homes]
    paths += [p for homes in LATER_HOMES.values() for p in homes]
    unowned = []
    for directory in ("src", "app"):
        for file in (ROOT / directory).rglob("*"):
            if not file.is_file() or "__pycache__" in file.parts:
                continue
            relative = file.relative_to(ROOT).as_posix()
            if not any(fnmatchcase(relative, p + "*" if p.endswith("/") else p) for p in paths):
                unowned.append(relative)
    assert not unowned, f"source files without owners: {sorted(unowned)}"


def _overlap(left: str, right: str) -> bool:
    """Intersect the literal/*/? path patterns used in the ownership map."""
    from posixpath import normpath

    left = normpath(left) + ("/*" if left.endswith("/") else "")
    right = normpath(right) + ("/*" if right.endswith("/") else "")
    assert not any(c in left + right for c in "[]"), "ownership patterns support only * and ?"
    pending, seen = [(0, 0)], set()
    while pending:
        i, j = pending.pop()
        if (i, j) in seen:
            continue
        seen.add((i, j))
        if i == len(left) and j == len(right):
            return True
        a = left[i] if i < len(left) else None
        b = right[j] if j < len(right) else None
        if a == "*":
            pending.append((i + 1, j))
        if b == "*":
            pending.append((i, j + 1))
        if a is not None and b is not None and (a in "*?" or b in "*?" or a == b):
            pending.append((i if a == "*" else i + 1, j if b == "*" else j + 1))
    return False


@pytest.mark.parametrize(("left", "right", "expected"), [
    ("src/stowage/rules/catalogue*", "src/stowage/rules/catalogue.py", True),
    ("src/stowage/rules/", "src/stowage/rules/catalogue.py", True),
    ("src/stowage/rules/catalogue*.py", "src/stowage/rules/catalogue*.toml", False),
    ("src/stowage/rules/cat*", "src/stowage/rules/*logue.py", True),
    ("./src/stowage//rules/", "src/stowage/rules/catalogue.py", True),
])
def test_path_pattern_overlap(left: str, right: str, expected: bool) -> None:
    assert _overlap(left, right) is expected


@pytest.mark.parametrize(("options", "environment", "selected", "other_slow"), [
    ([], "", False, False),
    (["--run-brp-benchmarks"], "", True, False),
    (["--run-brp-benchmarks", "-m", "not slow and not gpu and not net"], "", False, False),
    (["--run-brp-benchmarks"], '-m "not slow and not gpu and not net"', False, False),
    (["--run-brp-benchmarks"], '-m slow', True, True),
    (["--run-brp-benchmarks", "-m", "benchmark"], '-m "not slow"', True, False),
])
def test_benchmark_opt_in_marker_selection(
    options: list[str], environment: str, selected: bool, other_slow: bool,
) -> None:
    import os
    import subprocess
    import sys

    env = dict(os.environ, PYTEST_ADDOPTS=environment)
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-vv", "-p", "no:cacheprovider",
         "tests/benchmarks", "tests/checker/test_full_cases.py", *options],
        cwd=ROOT, capture_output=True, text=True, check=True, env=env,
    )
    assert ("test_complete_public_archive" in collected.stdout) is selected
    assert ("test_ever_smart_approximation_reported_departure_gm" in collected.stdout) is other_slow


def test_collision_guard_rejects_glob_file_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(TASK_HOMES, "test-owner", ("Collision probe", ("src/stowage/rules/catalogue*",), ()))
    with pytest.raises(AssertionError, match="overlaps"):
        test_no_path_owned_twice_except_sequential_ui()
