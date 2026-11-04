"""Keep the complete public-data scan opt-in while retaining quick checks."""

from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-brp-benchmarks", action="store_true", default=False,
        help="Run the slow complete public BRP archive scan (task 4.10).",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: complete public benchmark scan")
    config.addinivalue_line("markers", "benchmark: public benchmark validation")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-brp-benchmarks"):
        return
    for item in items:
        if str(item.path).startswith(str(Path(__file__).parent)) and item.get_closest_marker("slow"):
            item.add_marker(pytest.mark.skip(reason="use --run-brp-benchmarks for the complete archive"))
