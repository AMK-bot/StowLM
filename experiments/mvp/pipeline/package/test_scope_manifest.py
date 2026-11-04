"""Offline evidence checks against the recorded checked 190-box section."""
import csv
import io
from html.parser import HTMLParser
from pathlib import Path

import pytest

from stowage.model import CheckReport, Instance, MovePlan

from .manifest import (manifest_rows, render_manifest_csv, render_restow_csv,
                       render_tank_csv, render_weight_by_port_csv, restow_rows)
from .render import render_bay_plan, stability_summary
from .scope import scope_summary


@pytest.fixture(scope='module')
def checked_section():
    folder = Path(__file__).parent / 'fixtures' / 'complete'
    return tuple(model.model_validate_json((folder / name).read_text()) for model, name in (
        (Instance, 'instance.json'), (MovePlan, 'plan.json'), (CheckReport, 'checker-report.json')))


def records(text):
    return list(csv.DictReader(io.StringIO(text.split('\n', 1)[1])))


def test_scope_exposes_background_and_closes_displacement(checked_section):
    instance, plan, report = checked_section
    scope = scope_summary(instance, plan, report)
    assert scope['banner'] == ('Half-ship section: 190 boxes planned in 8 of 14 bays. Other bays hold '
        '268 fixed synthetic background boxes (3,856 t). Stability and strength include them. '
        'No ship can sail on this plan alone.')
    assert scope['background_mass_kg'] == pytest.approx(3856193.3499181527)
    assert scope['planned_bays'] == [1, 3, 9, 11, 17, 19, 25, 27]
    assert scope['background_bays'] == [5, 7, 13, 15, 21, 23]
    assert scope['weight_total_kg'] == report.parts['stability'].metrics['displacement_kg']
    assert scope['weight_residual_kg'] == 0
    assert scope['weight_closes']
    assert sum(row['mass_kg'] for row in scope['weights']) == pytest.approx(scope['weight_total_kg'])
    assert 'Ballast unchanged from arrival: 1,552 t.' in scope['ballast_line']


def test_mass_closure_refuses_contradictory_checker_report(checked_section):
    instance, plan, report = checked_section
    bad = report.model_copy(deep=True)
    bad.parts['stability'].metrics['displacement_kg'] += 1000
    with pytest.raises(ValueError, match='does not close'):
        scope_summary(instance, plan, bad)


def test_hatched_background_covers_full_ship_without_extra_printed_pages(checked_section):
    instance, plan, report = checked_section
    scope = scope_summary(instance, plan, report)
    html = render_bay_plan(instance, plan, report, stability_summary(instance, plan, report),
                           banner=scope['banner'], scope=scope)
    class Document(HTMLParser):
        def __init__(self):
            super().__init__()
            self.pages, self.profiles, self.background = [], [], []
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'section':
                self.pages.append(attrs['data-bay'])
            if 'data-profile-bay' in attrs:
                self.profiles.append(attrs['data-profile-bay'])
                if attrs['data-background'] == 'true':
                    self.background.append(attrs['data-profile-bay'])
    document = Document()
    document.feed(html)
    assert len(document.pages) == 8
    assert len(document.profiles) == 14 * 8
    assert len(document.background) == 6 * 8
    assert 'fill="url(#background-hatch-001)"' in html
    assert '13,564.284 t' in html
    assert 'Closes to checker displacement.' in html
    assert not any(remote in html for remote in ('<script', '<link', '<iframe', 'src="http'))


def test_manifest_names_every_decision_and_background_box_once(checked_section):
    instance, plan, report = checked_section
    scope = scope_summary(instance, plan, report)
    text = render_manifest_csv(instance, plan, report, banner=scope['banner'])
    rows = records(text)
    assert text.splitlines()[0] == '# ' + scope['banner']
    assert len(rows) == 458
    assert len({row['box'] for row in rows}) == 458
    planned = [row for row in rows if row['record_kind'] == 'planned section cargo']
    background = [row for row in rows if row['record_kind'] != 'planned section cargo']
    assert len(planned) == 190 and len(background) == 268
    assert sum(float(row['mass_t']) for row in background) == pytest.approx(scope['background_mass_t'])
    assert sum(bool(row['dg_un']) for row in planned) == 5
    for row in planned:
        if row['dg_un']:
            assert row['dg_psn'] and row['dg_pg']
            assert row['dg_ems'] == 'not supplied in source; obtain DG declaration'
    assert sum(row['reefer'] == 'True' for row in planned) == 6
    assert sum(row['oog'] == 'True' for row in planned) == 2
    assert all(row['pod'] == '' and 'not supplied' in row['remarks'] for row in background)


def test_port_and_tank_tables_account_for_native_masses(checked_section):
    scope = scope_summary(*checked_section)
    port_rows = records(render_weight_by_port_csv(scope, banner=scope['banner']))
    tanks = records(render_tank_csv(scope, banner=scope['banner']))
    cargo = sum(row['mass_kg'] for row in scope['weights'] if row['component'] in (
        'Planned section cargo aboard', 'Fixed synthetic background cargo'))
    assert sum(float(row['mass_t']) for row in port_rows) * 1000 == pytest.approx(cargo)
    assert sum(float(row['departure_mass_kg']) for row in tanks if row['kind'] == 'ballast') == scope['ballast_mass_kg']


def test_future_restows_are_named_and_native_count_matches(checked_section):
    instance, plan, report = checked_section
    rows = restow_rows(instance, plan)
    assert {row['box'] for row in rows} == {'QZXU0000380', 'QZXU0000776'}
    assert all(row['call_index'] == 1 for row in rows)
    assert len(rows) == report.count.future[0].restowed_boxes
    named = {row['box']: row.get('restow_calls', '') for row in manifest_rows(instance, plan, report)}
    assert all(named[row['box']] == '1' for row in rows)
    scope = scope_summary(instance, plan, report)
    assert len(records(render_restow_csv(instance, plan, banner=scope['banner']))) == 2
