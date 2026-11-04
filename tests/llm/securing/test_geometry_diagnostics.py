"""Early checker geometry failures retain their diagnosis and policy verdict."""
from pathlib import Path
import runpy

import pytest

from stowage.checker import lashing, visibility
from stowage.model import PartReport, Placement, ShipState
from stowage.model.fixtures import build_instance
from stowage.solver.securing_screen import DeckSecuringScreen, NecessaryDeckSecuringScreen


synthetic_stack = runpy.run_path(str(
    Path(__file__).resolve().parents[2] / 'solver/test_securing_screen.py'
))['synthetic_stack']


@pytest.mark.parametrize('screen_type', [DeckSecuringScreen, NecessaryDeckSecuringScreen])
def test_unequal_support_height_preserves_checker_failure(screen_type, monkeypatch):
    instance, placements = synthetic_stack([10000., 10000.], 40)
    left = instance.containers[0].model_copy(update={'length_ft': 20, 'height_m': 2.591})
    right = left.model_copy(update={'id': build_instance().containers[2].id, 'height_m': 2.9})
    instance = instance.model_copy(update={'containers': [left, right, instance.containers[1]]})
    placements = [Placement(container_id=left.id, slot='0010182'),
                  Placement(container_id=right.id, slot='0030182'), placements[1]]
    screen = screen_type(instance)
    monkeypatch.setattr(visibility, '_loading_condition', lambda *_: screen.condition)
    report = lashing.check_lashing(instance,
        ShipState(vessel_id=instance.vessel.id, placements=placements), call_index=0)
    reason = 'paired cargo supports must have equal actual height'
    assert report.violations[0].message == reason
    assert not report.metrics

    result = screen.evaluate(placements)
    necessary = screen_type is NecessaryDeckSecuringScreen
    assert result.passed == necessary
    assert reason in result.reason
    assert 'injected securing' not in result.reason
    counter = 'fail_open:geometry' if necessary else 'rejections:geometry'
    assert screen.metrics[counter] == 1
    assert not any(key.startswith('exceptions:') for key in screen.metrics)
    assert screen.evaluate(placements) == result
    assert screen.metrics['cache_hits'] == 1


@pytest.mark.parametrize('screen_type', [DeckSecuringScreen, NecessaryDeckSecuringScreen])
@pytest.mark.parametrize('reported_gm', [None, -1.])
def test_completed_calculation_still_requires_reported_injected_gm(
        screen_type, reported_gm, monkeypatch):
    instance, placements = synthetic_stack([10000., 10000.], 40)
    screen = screen_type(instance)
    original = lashing._check_lashing

    def without_gm_evidence(instance, ship, *, call_index):
        # Execute the real calculation with this screen's private dependencies,
        # then simulate a checker refactor losing/corrupting its GM metric.
        report = _bind(original, __builtins__=__builtins__,
                       _load_graph=_load_graph, _motions=_motions,
                       derive_cell_guide_design=derive_cell_guide_design)(
                           instance, ship, call_index=call_index)
        metrics = dict(report.metrics)
        if reported_gm is None:
            metrics.pop('gm_m', None)
        else:
            metrics['gm_m'] = reported_gm
        return PartReport(name='lashing', metrics=metrics)

    from stowage.solver.securing_screen import _bind
    monkeypatch.setattr(lashing, '_check_lashing', without_gm_evidence)
    result = screen.evaluate(placements)
    assert result.passed == (screen_type is NecessaryDeckSecuringScreen)
    assert 'checker did not evidence the injected securing GM/draught' in result.reason
    assert screen.metrics['exceptions:ValueError'] == 1
