"""Opt-in markers for the explicitly reviewed MVP archive/C2 evidence lane.

Load with ``-p v54_mvp_triage_plugin --mvp-triage`` and choose markers with
pytest's normal ``-m`` option. Loading this plugin alone changes no selection.
The full test bodies and physics assertions remain untouched.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

MANIFEST = Path(__file__).with_name("v54-triage-exclusions.json")
ROOT = Path(__file__).resolve().parents[3]
_candidates: list[dict] = []


def pytest_addoption(parser):
    group = parser.getgroup("MVP historical evidence triage")
    group.addoption("--mvp-triage", action="store_true", default=False,
                    help="Mark the explicitly reviewed stale archive/C2 cases; does not skip them itself.")
    group.addoption("--mvp-triage-report", default=None,
                    help="Write matching/selected/excluded node IDs and reasons as JSON.")


def pytest_configure(config):
    config.addinivalue_line("markers", "archive: historical archive inputs or snapshots need evidence maintenance")
    config.addinivalue_line("markers", "requires_linear_requalification: C2-on evidence is stale; MVP uses C2 off (D79)")


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_collection_modifyitems(config, items):
    global _candidates
    _candidates = []
    if config.getoption("--mvp-triage"):
        manifest = json.loads(MANIFEST.read_text())
        approved = {row["nodeid"]: row for row in manifest["cases"]}
        checked = set()
        for item in items:
            row = approved.get(item.nodeid)
            if row is None:
                continue
            filename = row["test_file"]
            if filename not in checked:
                current = hashlib.sha256((ROOT / filename).read_bytes()).hexdigest()
                if current != row["test_file_sha256"]:
                    raise pytest.UsageError(f"MVP triage evidence changed: {filename}; review its node exclusions again")
                checked.add(filename)
            # APPROXIMATION: exclude only explicitly reviewed historical evidence
            # in the opt-in MVP lane, never relabel it as a passing physics test.
            item.add_marker(getattr(pytest.mark, row["marker"])(reason=row["reason"]))
            _candidates.append(dict(row))
    yield


def pytest_collection_finish(session):
    target = session.config.getoption("--mvp-triage-report")
    if target:
        selected = {item.nodeid for item in session.items}
        report = {
            "enabled": session.config.getoption("--mvp-triage"),
            "marker_expression": session.config.option.markexpr,
            "manifest": str(MANIFEST.relative_to(ROOT)),
            "manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
            "selected_total": len(selected),
            "matching_historical_cases": [dict(row, selected=row["nodeid"] in selected) for row in _candidates],
            "excluded_historical_count": sum(row["nodeid"] not in selected for row in _candidates),
            "note": "Exclusions are known historical failures, not passing results. Nonhistorical markers may deselect additional cases.",
        }
        Path(target).write_text(json.dumps(report, indent=2) + "\n")
