"""Offline structural checks; the full package fixture gets a real checker verdict elsewhere."""
from html.parser import HTMLParser

import pytest

from stowage.model import CheckReport, MovePlan, PartReport
from stowage.model.fixtures import build_instance

from experiments.mvp.pipeline.package.render import render_bay_plan, stability_summary


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.bays, self.boxes, self.remote, self.text = [], [], [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'section':
            self.bays.append(attrs.get('data-bay'))
        if 'data-box' in attrs:
            self.boxes.append(attrs['data-box'])
        if tag in ('script', 'link', 'iframe') or attrs.get('src') or attrs.get('href'):
            self.remote.append((tag, attrs))

    def handle_data(self, data):
        self.text.append(data)


@pytest.fixture
def inputs():
    instance = build_instance()
    ids = [c.id for c in instance.containers]
    plan = MovePlan.model_validate(dict(id='render-test', instance_id=instance.id, call_index=0, moves=[
        dict(kind='load', call_index=0, container_id=ids[0], src='A-001-01-01', dst='0020182'),
        dict(kind='load', call_index=0, container_id=ids[1], src='B-001-01-01', dst='0010282'),
    ]))
    report = CheckReport(passed=False, parts={
        'stability': PartReport(name='stability', metrics=dict(gm_m=1.8, trim_m=.12,
            list_rad=.01, draught_aft_m=5.2, draught_fwd_m=5.08, draught_mean_m=5.14)),
        'strength': PartReport(name='strength', metrics=dict(max_sf_ratio=.62, max_bm_ratio=.48),
            margins=dict(sf_ratio=.38, bm_ratio=.52)),
        'lashing': PartReport(name='lashing', ran=False, error='Synthetic test: arrangement unavailable'),
    })
    return instance, plan, report


def test_stability_is_report_evidence_with_unknown_securing(inputs):
    instance, plan, report = inputs
    summary = stability_summary(instance, plan, report)
    assert summary['synthetic'] is True
    assert summary['gm_m'] == report.parts['stability'].metrics['gm_m']
    assert summary['strength_margins'] == {'sf_ratio': .38, 'bm_ratio': .52}
    assert summary['securing']['state'] == 'unavailable'
    assert summary['securing']['passed'] is None
    assert summary['strength_limits']['stations_x_m'] == [0, 60, 120]
    assert summary['input_digests']['instance_sha256']


def test_printable_pages_preserve_box_evidence_and_warning(inputs):
    instance, plan, report = inputs
    summary = stability_summary(instance, plan, report)
    banner = 'RELAXED PLAN — synthetic test; all-loaded rule broken'
    html = render_bay_plan(instance, plan, report, summary, banner=banner)
    document = Document(html)
    assert html.splitlines()[0] == f'<!-- {banner} -->'
    assert document.bays == ['001', '003']
    # Shared 40-foot box has an explicit slot and appears once in each supporting physical bay.
    assert document.boxes.count(instance.containers[0].id) == 2
    assert document.boxes.count(instance.containers[1].id) == 1
    text = ' '.join(document.text)
    for expected in ('20.00 t', '12.00 t', 'DG 3 / UN 1263', 'THBKK', 'THSGZ',
                     '0020182', 'Longitudinal side elevation', 'unavailable'):
        assert expected in text
    assert not document.remote
    assert html.count(f'class="banner">{banner}') == 2
    assert '@page{size:A3 landscape' in html
    assert 'page-break-after:always' in html
    assert 'print-color-adjust:exact' in html
    assert 'Full checker: FAIL' in html


def test_illegal_move_refuses_to_draw_partial_plan(inputs):
    instance, plan, report = inputs
    bad = plan.model_copy(update={'moves': [plan.moves[0].model_copy(update={'dst': '0020184'})]})
    with pytest.raises(ValueError, match='move replay failed'):
        render_bay_plan(instance, bad, report, {}, banner='RELAXED PLAN')


def test_user_labels_are_escaped(inputs):
    instance, plan, report = inputs
    html = render_bay_plan(instance, plan, report, {}, banner='RELAXED <script>alert(1)</script> -->')
    assert '<script>' not in html
    assert '&lt;script&gt;' in html
