"""Published method and paired tables identify their comparison populations."""
from types import SimpleNamespace

from eval import harness
from stowage.model import PlannerResult
from tests.eval.test_harness import Diagnostic, campaign


def test_harness_publishes_labeled_method_and_paired_tables(tmp_path, empty_ship_instance, monkeypatch):
    root = campaign(tmp_path, empty_ship_instance)
    monkeypatch.setattr(harness, 'run_planner', lambda planner, instance, **kw: (
        PlannerResult(status='error', planner=planner.name, planner_version=planner.version), None))
    monkeypatch.setattr(harness, 'lower_bound', lambda instance: SimpleNamespace(model_dump=lambda **kw: {}))
    comparison = harness.run_paired_evaluation(
        Diagnostic(), Diagnostic(), run_dir=tmp_path / 'paired', instances_dir=root,
        rungs=['tiny'], split='val', tiny_run=True, n_bootstrap=10)
    label = "over this method's passing plans; not comparable across rows"
    table = (tmp_path / 'paired/first/TABLE.md').read_text()
    assert label in table.splitlines()[0]
    assert label in (tmp_path / 'paired/first/ROW.md').read_text()
    paired = (tmp_path / 'paired/ROW.md').read_text()
    assert f"n_pairs={comparison['n_pairs']}" in paired
    assert f"n_excluded={comparison['n_excluded']}" in paired
    assert 'paired' in (tmp_path / 'paired/TABLE.md').read_text().splitlines()[0].lower()
