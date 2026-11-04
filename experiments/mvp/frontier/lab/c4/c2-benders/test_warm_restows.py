"""JSON-only objective accounting; no archived input or numerical work."""
from warm_restows import _parts


def test_projected_restows_are_kept_separate_from_hatches_and_current_work():
    row = _parts({'count': dict(weighted_nonproductive=114., relocations=24,
        hatch_cover_lifts=12, restow_lifts=0,
        future=[dict(restow_lifts=58, hatch_cover_lifts=10),
                dict(restow_lifts=0, hatch_cover_lifts=10)])})
    assert row == dict(lifts=114., relocations=24, hatch_now=12,
                       hatch_later=20, restow_now=0, restow_later=58)
