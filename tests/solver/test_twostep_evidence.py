"""Evidence denominators must retain failures and quarantine epoch drift."""
import importlib.util
from pathlib import Path


def _summary_module():
    path = Path(__file__).resolve().parents[2] / "experiments/twostep/summarize.py"
    spec = importlib.util.spec_from_file_location("twostep_evidence_summary", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_summary_retains_failures_and_uses_reached_terminal_denominator():
    rows = [
        {"rung": "tiny", "metrics": {"preplan_status": "timeout", "terminal_status": "not_started"}},
        {"rung": "tiny", "metrics": {"preplan_status": "ok", "terminal_status": "timeout", "fixed_slot_failure": 1}},
        {"rung": "tiny", "metrics": {"preplan_status": "ok", "terminal_status": "ok"},
         "checker_seconds": 1, "checker_passed": False, "ballast_present": True},
    ]
    for row in rows:
        row['epoch_stable'] = True
    result = _summary_module().summarize_rows(rows)["tiny"]
    assert result["requests"] == 3
    assert result["terminal_reached"] == 2
    assert result["terminal_failures"] == 1
    assert result["fixed_slot_failures"] == 1
    assert result["terminal_failure_frequency"] == 0.5
    assert result["checker_after_ballast_submissions"] == 1
    assert result["checker_after_ballast_pass_rate"] == 0


def test_summary_unsubmitted_checker_rate_is_unknown_and_drift_is_quarantined():
    rows = [{"rung": "small", "legal": True, "epoch_stable": False,
             "checker_seconds": 1, "checker_passed": True, "ballast_present": True},
            {"rung": "small", "metrics": {"preplan_status": "timeout"}, "epoch_stable": True}]
    result = _summary_module().summarize_rows(rows)["small"]
    assert result["requests"] == 2
    assert result["quarantined"] == 1
    assert result["qualified_requests"] == 1
    assert result["legal"] == 0
    assert result["terminal_failure_frequency"] is None
    assert result["checker_after_ballast_submissions"] == 0
    assert result["checker_after_ballast_pass_rate"] is None


def test_unpinned_historical_rows_do_not_become_current_evidence():
    result = _summary_module().summarize_rows([
        {"rung": "tiny", "legal": True, "checker_seconds": 1,
         "checker_passed": True, "ballast_present": True,
         "metrics": {"preplan_status": "ok", "terminal_status": "ok"}}
    ])["tiny"]
    assert result['unpinned'] == 1
    assert result['qualified_requests'] == 0
    assert result['legal'] == 0
    assert result['terminal_reached'] == 0
    assert result['checker_after_ballast_pass_rate'] is None


def test_default_table_excludes_drifted_legal_and_unballasted_checks(tmp_path, monkeypatch, capsys):
    import json
    import sys
    rows = [{"rung": "tiny", "epoch_stable": True, "legal": False,
             "checker_seconds": 1, "checker_passed": True, "ballast_present": False,
             "process_seconds": 2},
            {"rung": "tiny", "epoch_stable": False, "legal": True,
             "checker_seconds": 1, "checker_passed": True, "ballast_present": True,
             "objective": 7, "gap_to_lb": 0, "lifts": {"load": 4}, "process_seconds": 3}]
    path = tmp_path / 'rows.json'
    path.write_text(json.dumps(rows))
    monkeypatch.setattr(sys, 'argv', ['summarize.py', str(path)])
    _summary_module().main()
    assert '| tiny | 0/1/2 | 0/0 | 0/0 | — |' in capsys.readouterr().out
