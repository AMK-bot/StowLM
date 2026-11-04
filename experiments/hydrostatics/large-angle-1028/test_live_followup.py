"""Actual IGES/reference and fresh feeder-area regression, single CPU.

Expected cost: actual published-IGES comparison ~16s/0.4GB; each fresh feeder
C1 condition ~5–12s/<0.6GB. No source/result files are overwritten.
"""
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import iges_upper19 as iges
from followup_numerics import verify_curve_areas, verify_feeder_table, verify_iges_reference


def test_actual_iges_parser_and_independent_published_reference_comparison(monkeypatch):
    captured = []
    target = HERE / 'iges-upper19-results.json'
    old = json.loads(target.read_text())

    def capture(path, contents, *args, **kwargs):
        assert path.resolve() == target.resolve(), path
        captured.append(json.loads(contents))
        return len(contents)

    monkeypatch.setattr(Path, 'write_text', capture)
    with patch.object(iges, 'read_patches', wraps=iges.read_patches) as parser:
        iges.main()
    assert parser.call_count == 1
    assert len(captured) == 1
    actual = captured[0]
    # Source hashes, including the IGES file, are verified by main itself.
    # Derive both external comparisons from published ordinates, not flags.
    assert verify_iges_reference(actual, HERE / 'keramati-fig8-samples.csv') == {'kn': 7, 'gz': 7}
    for fresh, archived in zip(actual['rows'], old['rows']):
        assert fresh['heel_deg'] == archived['heel_deg']
        for kind in ('kn', 'gz'):
            assert fresh['iges_upper'][f'{kind}_m'] == pytest.approx(archived['iges_upper'][f'{kind}_m'], abs=1e-8, rel=0)


@pytest.mark.parametrize('name', ['feeder_500', 'feeder_800', 'feeder_1700'])
def test_fresh_feeder_partial_and_total_areas_against_curve_and_archive(name):
    from feeder_envelope import manual_instance, evaluate, source_hashes
    from stowage.checker.stability_geometry import StabilityGeometry
    before = source_hashes(name)
    instance, params, _ = manual_instance(name)
    row, _, _ = evaluate(instance, instance.onboard, params, StabilityGeometry(params))
    areas = verify_curve_areas(row)
    archived = json.loads((HERE / 'followup' / f'{name}-C1.json').read_text())['rows'][0]
    for metric, area in areas.items():
        assert area == pytest.approx(archived['metrics'][metric], abs=1e-9, rel=0)
    assert before == source_hashes(name)


def test_archived_partial_areas_and_exported_table_are_derived_from_curves():
    baselines = {}
    for name in ('feeder_500', 'feeder_800', 'feeder_1700'):
        saved = json.loads((HERE / 'followup' / f'{name}-C1.json').read_text())
        for row in saved['rows']:
            verify_curve_areas(row)
        baselines[name] = saved['rows'][0]
    verify_feeder_table(HERE / 'followup' / 'feeder-areas.csv', baselines)


def test_corrupted_partial_area_is_rejected_even_when_pass_flags_remain_true():
    row = json.loads((HERE / 'followup' / 'feeder_800-C1.json').read_text())['rows'][0]
    row['metrics']['area_0_30_mrad'] = 999.
    with pytest.raises(AssertionError, match='area_0_30_mrad'):
        verify_curve_areas(row)
