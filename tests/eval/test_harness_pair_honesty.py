"""Cross-method claims must compare matched checked passes, not row means."""
import pytest

from eval import harness_metrics as metrics


def row(identity, objective, *, passed=True, reference=None):
    return {"instance_id": identity, "input_sha256": identity, "spec_key": identity,
            "split": "test", "rung": "small", "reference_solver": reference,
            "report": {"passed": passed, "parts": {}, "count": {
                "weighted_nonproductive": objective}, "violations": []}}


def test_survivorship_row_means_cannot_be_used_for_fewer_moves_claims():
    first = [row("easy", 1), row("hard", 100, passed=False)]
    second = [row("easy", 2), row("hard", 100)]
    own_summary = metrics.aggregate_results(first)
    assert own_summary["moves_population"] == "over this method's passing plans; not comparable across rows"
    assert "over this method's passing plans; not comparable across rows" in metrics.render_row(own_summary)
    paired = metrics.paired_comparison(first, second, n_bootstrap=10)
    text = metrics.render_paired_comparison(paired, first_method="first", second_method="second")
    assert "-1" in text
    assert "n_pairs=1; n_excluded=1" in text
    assert "both methods' passing plans" in text
    assert paired["mean_difference"] == -1
    assert paired["source"] == "paired_comparison"
    assert "not comparable across rows" in metrics.evaluation_table_header()
    assert "n_pairs" in metrics.paired_table_header()
    assert len(metrics.render_table_header().splitlines()[0].split("|")) == len(metrics.render_row(own_summary).split("|"))
    assert len(metrics.paired_table_header().splitlines()[0].split("|")) == len(text.split("|"))


def test_comparison_renderer_rejects_independent_row_mean_arithmetic():
    with pytest.raises(ValueError, match="paired_comparison"):
        metrics.render_paired_comparison({"mean_difference": -50, "n_pairs": 1, "n_excluded": 0},
                                         first_method="first", second_method="second")
    old_summary = metrics.aggregate_results([row("i", 1)])
    old_summary["gap_to_solver"] = {"absolute": {"mean": -99, "n": 1}}
    assert "paired comparison unavailable" in metrics.render_row(old_summary)
    assert "-99" not in metrics.render_row(old_summary)


def test_solver_gap_uses_paired_comparison_with_exclusions_visible(monkeypatch):
    calls = []
    original = metrics.paired_comparison
    def observe(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        return result
    monkeypatch.setattr(metrics, "paired_comparison", observe)
    rows = [row("easy", 1, reference=2), row("hard", 100, passed=False, reference=100),
            row("no-reference", 3)]
    summary = metrics.aggregate_results(rows)
    gap = summary["gap_to_solver"]
    assert len(calls) == 1
    assert gap["source"] == "paired_comparison"
    assert gap["absolute"]["mean"] == calls[0]["mean_difference"] == -1
    assert gap["n_pairs"] == 1
    assert gap["n_excluded"] == 2
    assert "n_pairs=1; n_excluded=2" in metrics.render_row(summary)


def test_solver_reference_with_unshippable_report_is_not_a_comparable_pass():
    reference = row("i", 3)
    reference["shippable"] = False
    summary = metrics.aggregate_results([row("i", 2, reference=reference)])
    assert summary["gap_to_solver"]["absolute"]["mean"] is None
    assert summary["gap_to_solver"]["n_excluded"] == 1


def test_paired_rendering_handles_no_comparable_plans_without_claim():
    comparison = metrics.paired_comparison([row("i", 1, passed=False)], [row("i", 2)], n_bootstrap=2)
    rendered = metrics.render_paired_comparison(comparison, first_method="A", second_method="B")
    assert "N/A" in rendered
    assert "n_pairs=0; n_excluded=1" in rendered
    assert "fewer" not in rendered


def test_method_table_flags_measured_training_epoch_mismatches():
    first = row('older-adapter', 1)
    first['training_epoch_differs'] = True
    second = row('same-epoch', 2)
    second['training_epoch_differs'] = False
    summary = metrics.aggregate_results([first, second, row('unknown-lineage', 3)])
    assert summary['training_epoch_mismatch_count'] == 1
    assert summary['training_epoch_known_count'] == 2
    assert 'training epoch ≠ evaluation epoch; n=1' in metrics.render_row(summary)
