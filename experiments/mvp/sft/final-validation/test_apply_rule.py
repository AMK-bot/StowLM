"""Boundary tests of the D152 rule. Run: pytest --noconftest experiments/mvp/sft/final-validation/test_apply_rule.py"""
import apply_rule as a


def maps(values):
    return {str(i): v for i, v in enumerate(values)}


def test_replaces_at_exactly_five_percent():
    v = a.decide(maps([95.0, None]), maps([100.0, 50.0]))
    assert v['passes_ok'] and v['moves_ok'] and v['replace']


def test_just_under_five_percent_keeps():
    assert not a.decide(maps([95.5]), maps([100.0]))['replace']


def test_one_fewer_pass_allowed_two_not():
    one = a.decide(maps([90.0, None, 5.0]), maps([100.0, 1.0, 5.0]))
    assert one['passes_ok'] and one['moves_ok'] and one['replace']
    two = a.decide(maps([90.0, None, None]), maps([100.0, 1.0, 1.0]))
    assert not two['passes_ok'] and not two['replace']


def test_more_passes_but_no_move_gain_keeps():
    v = a.decide(maps([100.0, 10.0]), maps([100.0, None]))
    assert v['passes_ok'] and not v['moves_ok'] and not v['replace']


def test_mutual_only_ignores_maps_one_side_failed():
    v = a.decide(maps([90.0, 500.0]), maps([100.0, None]))
    assert v['mutual'] == 1 and v['replace']


def test_no_mutual_maps_keeps():
    assert not a.decide(maps([None]), maps([5.0]))['replace']


def test_sign_p_matches_recorded_values():
    assert abs(a.sign_p(7, 16)-0.093) < .001 and abs(a.sign_p(4, 0)-.125) < 1e-9 and a.sign_p(0, 0) == 1.0


def test_different_maps_refused():
    try:
        a.decide({'a': 1.0}, {'b': 1.0})
    except ValueError:
        return
    raise AssertionError


def _fake(monkeypatch, transform):
    original = a.sets_for

    def sets(_stand_in):
        new, old, two = original(True)
        return {k: transform(k, [dict(r) for r in v]) for k, v in new.items()}, old, two
    monkeypatch.setattr(a, 'sets_for', sets)


def test_end_to_end_replace(monkeypatch):
    def better(_name, rows):
        for r in rows:
            if r.get('passed') is True:
                r['weighted_moves'] *= .9
        return rows
    _fake(monkeypatch, better)
    text, decided, verdict = a.render(True)
    assert decided and verdict['replace'] and '8.3 replaces 8.2' in text and 'NOT a result' not in text and 'PIPELINE TEST' in text


def test_end_to_end_keeps_on_identical_rows(monkeypatch):
    _fake(monkeypatch, lambda _n, rows: rows)
    text, decided, verdict = a.render(True)
    assert decided and not verdict['replace'] and '8.2 stays' in text


def test_refuses_missing_map_and_unavailable_and_epoch(monkeypatch):
    def broken(name, rows):
        if name == 'reserve25':
            rows = rows[:-1]
            rows[0]['unavailable'], rows[0]['passed'] = True, False
            rows[1]['audit_outcome_epoch_id'] = 'other'
        return rows
    _fake(monkeypatch, broken)
    text, decided, _ = a.render(True)
    assert not decided and 'NOT DECIDED' in text and '24 rows' in text and 'unavailable' in text and 'outcome epochs' in text


def test_d158_labels():
    assert a.d158(maps([95.0, 10.0]), maps([100.0, 10.5]))['label'] in ('Par',)
    assert a.d158(maps([90.0, None]), maps([100.0, 5.0]))['label'] == 'Better'
    assert a.d158(maps([96.0, None, None]), maps([100.0, 5.0, 5.0]))['label'] == 'Par'
    assert a.d158(maps([96.0, None, None, None]), maps([100.0, 5.0, 5.0, 5.0]))['label'] == 'Worse'
    assert a.d158(maps([105.0]), maps([100.0]))['label'] == 'Worse'
    assert a.d158(maps([125.0, None]), maps([100.0, 5.0]))['label'] == 'Worse'
