"""Repo-wide pytest configuration (plan 0.9). Settings live in pyproject.toml [tool.pytest.ini_options].

Speed policy: mark anything over ~10 s ``@pytest.mark.slow``, anything needing the GPU/MLX weights ``gpu``, anything
needing the network ``net``; the default run excludes all three. Reuse the session fixtures below instead of
rebuilding expensive objects. **Session fixtures are shared: never mutate them** — take ``obj.model_copy(deep=True)``
first if a test needs to change one.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent
FEEDERS = ("feeder_500", "feeder_800", "feeder_1700")
SLOW_THRESHOLD_S = 10.0


def pytest_configure(config: pytest.Config) -> None:
    """The archive opt-in includes slow benchmarks unless a caller explicitly selects markers."""
    import os
    import shlex

    args = (*shlex.split(os.environ.get("PYTEST_ADDOPTS", "")), *config.invocation_params.args)
    explicit_markers = any(arg == "-m" or arg.startswith(("-m", "--markexpr")) for arg in args)
    if (config.getoption("--run-brp-benchmarks", default=False)
            and config.option.markexpr == "not slow and not gpu and not net"
            and not explicit_markers):
        config.option.markexpr = "(not slow or benchmark) and not gpu and not net"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """``data/fixtures/``: small tracked JSON fixtures (task 1.1: ``empty_ship.json``, ``layover.json``)."""
    return REPO_ROOT / "data" / "fixtures"


@pytest.fixture(scope="session")
def fleet_vessel() -> Callable[..., object]:
    """Memoised ``stowage.vessel.fleet.load_vessel(name, with_hull=...)`` for the whole session (2.2's disk cache
    sits underneath). Usage: ``vessel = fleet_vessel("feeder_800")``. Don't mutate the result."""
    from stowage.vessel.fleet import load_vessel  # noqa: PLC0415 — keep conftest import cheap

    memo: dict[tuple[str, bool], object] = {}

    def get(name: str, *, with_hull: bool = True) -> object:
        key = (name, with_hull)
        if key not in memo:
            memo[key] = load_vessel(name, with_hull=with_hull)
        return memo[key]

    return get


@pytest.fixture(scope="session")
def feeder_500(fleet_vessel):  # noqa: ANN001, ANN201
    """The ~500 TEU feeder with hull (session-shared; don't mutate)."""
    return fleet_vessel("feeder_500")


@pytest.fixture(scope="session")
def feeder_800(fleet_vessel):  # noqa: ANN001, ANN201
    """The ~800 TEU feeder with hull (session-shared; don't mutate)."""
    return fleet_vessel("feeder_800")


@pytest.fixture(scope="session")
def feeder_1700(fleet_vessel):  # noqa: ANN001, ANN201
    """The ~1,700 TEU feeder with hull (session-shared; don't mutate)."""
    return fleet_vessel("feeder_1700")


@pytest.fixture(scope="session")
def fleet(fleet_vessel):  # noqa: ANN001, ANN201
    """All three feeders with hulls: ``{"feeder_500": Vessel, ...}`` (session-shared; don't mutate)."""
    return {name: fleet_vessel(name) for name in FEEDERS}


@pytest.fixture(scope="session")
def empty_ship_instance(fixtures_dir):  # noqa: ANN001, ANN201
    """``data/fixtures/empty_ship.json`` as an Instance (stage A; session-shared; don't mutate)."""
    from stowage.io import load_instance  # noqa: PLC0415

    return load_instance(fixtures_dir / "empty_ship.json")


@pytest.fixture(scope="session")
def layover_instance(fixtures_dir):  # noqa: ANN001, ANN201
    """``data/fixtures/layover.json`` as an Instance (stage B; session-shared; don't mutate)."""
    from stowage.io import load_instance  # noqa: PLC0415

    return load_instance(fixtures_dir / "layover.json")


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:  # noqa: ANN001, ARG001
    """List tests that took > SLOW_THRESHOLD_S (setup + call + teardown) without a ``slow`` mark, so owners mark
    or speed them up."""
    totals: dict[str, float] = {}
    unmarked: set[str] = set()
    for reports in terminalreporter.stats.values():
        for rep in reports:
            nodeid, duration = getattr(rep, "nodeid", None), getattr(rep, "duration", None)
            if not nodeid or duration is None:
                continue
            totals[nodeid] = totals.get(nodeid, 0.0) + duration
            if "slow" not in getattr(rep, "keywords", {}):
                unmarked.add(nodeid)
    offenders = sorted(((t, n) for n, t in totals.items() if t > SLOW_THRESHOLD_S and n in unmarked), reverse=True)
    if offenders:
        terminalreporter.write_sep("-", f"tests over {SLOW_THRESHOLD_S:.0f} s without @pytest.mark.slow")
        for t, n in offenders:
            terminalreporter.write_line(f"{t:8.1f}s  {n}")
