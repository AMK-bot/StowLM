import json
from pathlib import Path

from . import report


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def setup_output(tmp_path):
    output = tmp_path / "run"
    save(output / "campaign.json", {"selected_medium": [dict(path=f"m{i}", cell="medium") for i in range(8)]})
    save(output / "reused.json", {"rows": [], "sources": [{"path": "frozen", "sha256": "abc"}], "medium_count": 0})
    return output


def row(kind="baseline", map="m0", **kwargs):
    return dict(key=map + "-" + kind, map=map, cell="medium", rule_type=kind,
                status="checked_plan", passed=True, lifts=100, wall_s=110, **kwargs)


def test_reused_and_new_deadlines_are_separate_and_paired(tmp_path):
    output = setup_output(tmp_path)
    base = row(first_checked_wall_s=80)
    save(output / "reused.json", {"rows": [base], "sources": [], "medium_count": 1})
    rule = row("close_bay", first_checked_wall_s=90,
               comparison={"binding_classification": "binding"})
    rule.update(status="deadline_exceeded", deadline_with_incumbent=True, wall_s=900, lifts=105)
    save(output / "jobs/close.json", rule)
    miss = row("max_tiers")
    miss.update(status="deadline_exceeded", passed=False, wall_s=900,
                plan={"untrusted": True}, live_outcome="no_plan_inside_live_budget")
    save(output / "jobs/tier.json", miss)
    text, status = report.build_report(output, now=123)
    assert status["medium_finalized"] == 3
    assert status["reused_rows"] == 1 and status["new_rows"] == 2
    close = status["statistics"]["medium:close_bay"]
    assert close["full_passes"] == close["deadlines_with_checked"] == close["binding_passes"] == 1
    assert close["first_checked_median_s"] == 90
    assert (close["paired_n"], close["paired_lifts_median"], close["paired_baseline_lifts_median"]) == (1, 105, 100)
    tier = status["statistics"]["medium:max_tiers"]
    assert tier["deadlines_without_checked"] == 1 and tier["full_passes"] == 0
    assert "not proof of impossibility" in text and "900-second deadline" in text


def test_failed_baseline_never_becomes_already_obeyed_or_paired(tmp_path):
    output = setup_output(tmp_path)
    base = row()
    base.update(passed=False, status="no_checked_plan")
    save(output / "jobs/base.json", base)
    good = row("dg_keep_away", binding_classification="already_obeyed",
               baseline_instruction_violations=[], evidence={"first_checked_wall_s": 42})
    save(output / "jobs/rule.json", good)
    failed = row("close_bay")
    failed.update(passed=False, status="no_checked_plan")
    save(output / "jobs/failed.json", failed)
    _, status = report.build_report(output)
    dg = status["statistics"]["medium:dg_keep_away"]
    assert dg["unknown_passes"] == 1 and dg["already_obeyed_passes"] == 0
    assert dg["paired_n"] == 0 and dg["first_checked_median_s"] == 42
    assert status["statistics"]["medium:close_bay"]["baseline_also_failed"] == 1


def test_completion_requires_every_selected_map_and_kind(tmp_path):
    output = setup_output(tmp_path)
    for i in range(8):
        for kind in report.KINDS:
            if i == 7 and kind == "one_port_per_hatch":
                continue
            save(output / "jobs" / f"{i}-{kind}.json", row(kind, f"m{i}"))
    # A duplicate or an unrelated medium map cannot cover a missing cell.
    save(output / "jobs/duplicate.json", row("baseline", "m0", finished_at=2))
    save(output / "jobs/foreign.json", row("one_port_per_hatch", "foreign"))
    _, status = report.build_report(output)
    assert status["medium_finalized"] == 39 and not status["medium_complete"]
    assert len(status["duplicate_records"]) == 1
    save(output / "jobs/last.json", row("one_port_per_hatch", "m7"))
    _, status = report.build_report(output)
    assert status["medium_complete"] and status["medium_remaining"] == 0


def test_new_replaces_snapshot_without_editing_snapshot_or_rows(tmp_path):
    output = setup_output(tmp_path)
    base = row()
    snapshot = {"rows": [base], "sources": [], "medium_count": 1}
    save(output / "reused.json", snapshot)
    newer = row()
    newer.update(key="fresh-baseline", lifts=99)
    save(output / "jobs/new.json", newer)
    before = {path: path.read_bytes() for path in (output / "reused.json", output / "jobs/new.json")}
    rows, _, duplicates, _ = report.load_rows(output)
    assert len(rows) == 1 and rows[0]["key"] == "fresh-baseline"
    assert rows[0]["report_origin"] == "new" and len(duplicates) == 1
    assert all(path.read_bytes() == raw for path, raw in before.items())


def test_sidecar_violations_are_required_and_paired_subset_matches(tmp_path):
    output = setup_output(tmp_path)
    for i in range(2):
        save(output / f"jobs/base{i}.json", row(map=f"m{i}"))
        save(output / f"jobs/rule{i}.json", row("max_tiers", f"m{i}"))
    save(output / "comparisons/rule.json", dict(row_key="m0-max_tiers", baseline_instruction_violations=["placement"]))
    _, status = report.build_report(output)
    stats = status["statistics"]["medium:max_tiers"]
    assert stats["binding_passes"] == 1 and stats["unknown_passes"] == 1
    assert stats["paired_n"] == 2


def test_refresh_is_lightweight_and_keeps_writes_in_fence(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "HERE", tmp_path)
    output = setup_output(tmp_path)
    save(output / "active.json", [{"pid": 42, "rule_type": "baseline"}])
    save(output / "jobs/broken.json", {"state": "running"})
    status = report.refresh(output)
    assert status["active"] == [{"pid": 42, "rule_type": "baseline"}]
    assert status["malformed_records"] == ["broken.json"]
    assert status["eta_scheduling_hours"] == 40 * 900 / 3 / 3600
    assert (output / "RESULTS.md").read_text() == (tmp_path / "RESULTS.md").read_text()
    assert (tmp_path / "RESULTS-LIVE.md").exists()
    try:
        report.refresh(tmp_path.parent / "outside")
    except ValueError:
        pass
    else:
        raise AssertionError("outside binding output accepted")
