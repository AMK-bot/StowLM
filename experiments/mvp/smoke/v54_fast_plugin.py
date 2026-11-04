"""Persist exact default-suite selection and incremental reports for interrupted runs."""
import json
import os
from pathlib import Path

import pytest

_report_fd = None
_write = os.write


def pytest_configure(config):
    global _report_fd
    target = os.environ.get("V54_REPORT_PATH")
    if target:
        _report_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)


def pytest_unconfigure(config):
    global _report_fd
    if _report_fd is not None:
        os.close(_report_fd)
        _report_fd = None


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    target = os.environ.get("V54_FILE_LIST")
    node_target = os.environ.get("V54_NODE_SELECTION_PATH")
    if target or node_target:
        nodes = set(json.loads(Path(node_target).read_text())) if node_target else None
        files = set(json.loads(target)) if target else None
        extra = set(json.loads(os.environ.get("V54_EXTRA_NODE_LIST", "[]")))
        def included(item):
            return (item.nodeid in nodes if nodes is not None else item.nodeid.split("::", 1)[0] in files) or item.nodeid in extra
        selected = [item for item in items if included(item)]
        deselected = [item for item in items if not included(item)]
        items[:] = selected
        config.hook.pytest_deselected(items=deselected)


def pytest_collection_finish(session):
    target = os.environ.get("V54_SELECTION_PATH")
    if target:
        Path(target).write_text(json.dumps([item.nodeid for item in session.items], indent=2) + "\n")


def pytest_runtest_logreport(report):
    if _report_fd is not None:
        record = {"nodeid": report.nodeid, "when": report.when, "outcome": report.outcome,
                  "duration": report.duration, "wasxfail": getattr(report, "wasxfail", None),
                  "longrepr": str(report.longrepr) if report.failed else None}
        # Tests deliberately monkeypatch Path.open/builtins.open; retain an
        # already-open descriptor so those probes cannot intercept telemetry.
        _write(_report_fd, (json.dumps(record) + "\n").encode())
