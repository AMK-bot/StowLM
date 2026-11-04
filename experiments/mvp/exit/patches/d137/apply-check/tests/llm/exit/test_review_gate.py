import json

import pytest

from llm.exit.review_gate import (
    REPORTS, RESOLUTION, check_review_gate, report_fingerprint, require_review_gate,
)


def write_reviews(root, *, severity="P1", status="closed", evidence="uv run pytest: passed"):
    records = {}
    for relative in REPORTS:
        report = root / relative
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(f"# Review\n## {severity}: finding\nDetails.\n")
        records[relative] = {
            "sha256": report_fingerprint(report)["sha256"],
            "exhaustive_review": True,
            "findings": [{"id": "F1", "severity": severity, "status": status,
                          "evidence": evidence, "lines": [2]}],
        }
    resolution = root / RESOLUTION
    resolution.parent.mkdir(parents=True, exist_ok=True)
    resolution.write_text(json.dumps({"schema_version": 1, "reports": records}))
    return resolution


def test_missing_reports_and_attestation_block(tmp_path):
    result = check_review_gate(tmp_path)
    assert not result.ready
    assert sum("report unavailable" in reason for reason in result.reasons) == 2
    with pytest.raises(RuntimeError, match="review deployment gate"):
        require_review_gate(tmp_path)


def test_changed_report_invalidates_resolution(tmp_path):
    write_reviews(tmp_path)
    report = tmp_path / REPORTS[0]
    report.write_text(report.read_text() + "New evidence.\n")
    assert any("report changed" in reason for reason in check_review_gate(tmp_path).reasons)


@pytest.mark.parametrize("severity", ["P0", "P1"])
def test_open_critical_finding_blocks(tmp_path, severity):
    write_reviews(tmp_path, severity=severity, status="open")
    assert any(f"unresolved {severity}" in reason for reason in check_review_gate(tmp_path).reasons)


def test_closed_finding_needs_verification_evidence(tmp_path):
    write_reviews(tmp_path, evidence="")
    assert not check_review_gate(tmp_path).ready


def test_resolved_current_reports_pass(tmp_path):
    write_reviews(tmp_path)
    assert check_review_gate(tmp_path).ready
    assert require_review_gate(tmp_path).ready


def test_open_p2_allowed(tmp_path):
    write_reviews(tmp_path, severity="P2", status="open", evidence="")
    assert check_review_gate(tmp_path).ready


def test_empty_inventory_cannot_hide_critical_marker(tmp_path):
    path = write_reviews(tmp_path)
    data = json.loads(path.read_text())
    data["reports"][REPORTS[0]]["findings"] = []
    path.write_text(json.dumps(data))
    assert any("unreviewed P0/P1" in reason for reason in check_review_gate(tmp_path).reasons)


def test_nonfinding_marker_requires_explicit_reason(tmp_path):
    path = write_reviews(tmp_path)
    data = json.loads(path.read_text())
    record = data["reports"][REPORTS[0]]
    record["findings"] = []
    record["non_findings"] = [{"lines": [2], "reason": "Summary referring to resolved finding."}]
    path.write_text(json.dumps(data))
    assert check_review_gate(tmp_path).ready


def test_exhaustive_attestation_required(tmp_path):
    path = write_reviews(tmp_path)
    data = json.loads(path.read_text())
    data["reports"][REPORTS[0]]["exhaustive_review"] = False
    path.write_text(json.dumps(data))
    assert not check_review_gate(tmp_path).ready
