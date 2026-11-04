"""Fail-closed deployment admission tied to the two D109 review reports.

The resolution JSON is a human-reviewed inventory, not an automatic claim that
changing code fixed a review.  Binding report bytes means a new finding closes
the gate until it has also been reviewed.  CPU diagnostics need not use it.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[3]
REPORTS = (
    "experiments/review/v4/rev-exit-dpo.md",
    "experiments/review/v4/logic-exit.md",
)
RESOLUTION = "experiments/mvp/exit/review-resolution.json"
_CRITICAL = re.compile(r"\bP[01]\b", re.IGNORECASE)


@dataclass(frozen=True)
class ReviewGateResult:
    ready: bool
    reasons: tuple[str, ...]


def report_fingerprint(path):
    """Return the exact report hash and 1-based lines mentioning P0 or P1."""
    data = Path(path).read_bytes()
    text = data.decode("utf-8")
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "critical_lines": [
            index for index, line in enumerate(text.splitlines(), 1)
            if _CRITICAL.search(line)
        ],
    }


def _nonempty_text(value):
    return isinstance(value, str) and bool(value.strip())


def check_review_gate(root=None, resolution_path=None):
    """Require complete, evidence-bearing review dispositions for current bytes.

    Resolution schema::

        {"schema_version": 1, "reports": {
          "experiments/review/v4/rev-exit-dpo.md": {
            "sha256": "...", "exhaustive_review": true,
            "findings": [{"id": "F1", "severity": "P1", "status": "closed",
                          "evidence": "test command and result", "lines": [12]}],
            "non_findings": [{"lines": [2], "reason": "summary, not a finding"}]
          }, "experiments/review/v4/logic-exit.md": { ... }
        }}

    Every P0/P1 marker line must be covered explicitly.  This deliberately also
    covers summaries such as "no P0/P1": classify these in ``non_findings`` with
    a reason rather than silently assuming they are safe.  P2 findings may stay
    open.  An exhaustive inventory can be empty for a report with no markers.
    """
    root = Path(root or ROOT)
    resolution_path = Path(resolution_path or root / RESOLUTION)
    reasons = []
    fingerprints = {}
    for relative in REPORTS:
        path = root / relative
        try:
            fingerprints[relative] = report_fingerprint(path)
        except (OSError, UnicodeError) as exc:
            reasons.append(f"review report unavailable: {relative}: {exc}")
    try:
        resolution = json.loads(resolution_path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        reasons.append(f"review resolution unavailable: {exc}")
        return ReviewGateResult(False, tuple(reasons))
    if not isinstance(resolution, dict) or resolution.get("schema_version") != 1:
        reasons.append("review resolution schema_version must be 1")
        return ReviewGateResult(False, tuple(reasons))
    records = resolution.get("reports")
    if not isinstance(records, dict):
        reasons.append("review resolution reports must be an object")
        return ReviewGateResult(False, tuple(reasons))
    for relative, fingerprint in fingerprints.items():
        record = records.get(relative)
        if not isinstance(record, dict):
            reasons.append(f"review disposition missing: {relative}")
            continue
        if record.get("sha256") != fingerprint["sha256"]:
            reasons.append(f"review report changed or hash missing: {relative}")
        if record.get("exhaustive_review") is not True:
            reasons.append(f"review inventory is not exhaustive: {relative}")
        findings = record.get("findings")
        non_findings = record.get("non_findings", [])
        if not isinstance(findings, list) or not isinstance(non_findings, list):
            reasons.append(f"review inventories must be lists: {relative}")
            continue
        covered = set()
        ids = set()
        for finding in findings:
            if not isinstance(finding, dict):
                reasons.append(f"malformed review finding: {relative}")
                continue
            identifier = finding.get("id")
            severity = finding.get("severity")
            status = finding.get("status")
            if not _nonempty_text(identifier):
                reasons.append(f"review finding id missing or duplicated: {relative}")
            elif identifier in ids:
                reasons.append(f"review finding id missing or duplicated: {relative}")
            else:
                ids.add(identifier)
            if severity not in ("P0", "P1", "P2", "P3"):
                reasons.append(f"unknown review severity: {relative}: {identifier}")
            if status not in ("open", "closed"):
                reasons.append(f"unknown review status: {relative}: {identifier}")
            if severity in ("P0", "P1"):
                if status != "closed" or not _nonempty_text(finding.get("evidence")):
                    reasons.append(f"unresolved {severity}: {relative}: {identifier}")
            lines = finding.get("lines", [])
            if not isinstance(lines, list) or any(type(line) is not int or line < 1 for line in lines):
                reasons.append(f"invalid finding line inventory: {relative}: {identifier}")
            else:
                covered.update(lines)
        for item in non_findings:
            if not isinstance(item, dict) or not _nonempty_text(item.get("reason")):
                reasons.append(f"non-finding classification needs a reason: {relative}")
                continue
            lines = item.get("lines")
            if not isinstance(lines, list) or not lines or any(type(line) is not int or line < 1 for line in lines):
                reasons.append(f"invalid non-finding line inventory: {relative}")
            else:
                covered.update(lines)
        missing = set(fingerprint["critical_lines"]) - covered
        if missing:
            reasons.append(f"unreviewed P0/P1 marker lines: {relative}: {sorted(missing)}")
    return ReviewGateResult(not reasons, tuple(reasons))


def require_review_gate(root=None, resolution_path=None):
    """Raise before any generation if the reviews are absent, stale or open."""
    result = check_review_gate(root=root, resolution_path=resolution_path)
    if not result.ready:
        raise RuntimeError("ExIt review deployment gate: " + "; ".join(result.reasons))
    return result
