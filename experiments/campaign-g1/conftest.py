"""Diagnostic source copies and generated releases are artifacts, not tests."""

from pathlib import Path

collect_ignore_glob = ["diagnostic-*", "canary-diagnostic-*", ".canary-admission",
                       "*/diagnostic-source", "*/regen", "*/initial-attempt", ".pytest-tmp"]


def pytest_configure(config):
    if config.option.basetemp is None:
        config.option.basetemp = str(Path(__file__).resolve().parent / ".pytest-tmp")
