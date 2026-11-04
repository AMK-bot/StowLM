"""Live input stages, exercised through production JS without browser/model/network."""
from __future__ import annotations

import base64
import copy
import hashlib
import html
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import wave

import pytest

HERE = Path(__file__).parent
STATIC = HERE.parent / 'static'
NODE = shutil.which('node')
VIEWS = json.loads((HERE / 'fixtures/planner-live/input-views.json').read_text())
pytestmark = pytest.mark.skipif(NODE is None, reason='Node is required for the DOM harness')


def sample(name):
    return copy.deepcopy(VIEWS[name])


def render(area, name='ready', steps=(), view=None, **options):
    result = subprocess.run([NODE, str(HERE / 'planner_live_harness.js')], input=json.dumps({
        'area': area, 'view': sample(name) if view is None else view, 'steps': steps, **options,
    }), text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def words(page):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', ' ', page['html']))).strip()


def click(action, response=None):
    step = {'selector': f'[data-action="{action}"]', 'action': True}
    if response is not None:
        step['response'] = response
    return step


def field(selector, value, event='input'):
    return {'selector': selector, 'value': value, 'event': event}


def events(page, name):
    return [event.get('value') for event in page['events'] if event['name'] == name]


def control(page, action):
    return next(c for c in page['controls'] if c.get('action') == action)


@pytest.mark.parametrize('area,order', [('intake', 10), ('instruct', 20)])
def test_registration_and_javascript_syntax(area, order):
    page = render(area)
    assert page['definition'] == {'id': area, 'mode': 'live', 'order': order, 'features': []}
    result = subprocess.run([NODE, '--check', str(STATIC / f'planner-live-{area}.js')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('area', ['intake', 'instruct'])
def test_live_assets_never_reference_demo_routes_or_recordings(area):
    for suffix in ('js', 'css'):
        source = (STATIC / f'planner-live-{area}.{suffix}').read_text()
        for forbidden in ('/api/v6/', 'planner-data', 'RecordedClient', 'RecordedParser', 'fetch(', 'XMLHttpRequest'):
            assert forbidden not in source


# Cargo cases: keep separate from the shared harness's other stage owners.
def test_intake_generated_papers_grouped_and_downloadable():
    page = render('intake', 'needs-papers')
    text = words(page)
    assert text.count('Synthetic document') == 3
    assert 'Booking SYN-A' in text and 'Booking SYN-B' in text
    assert '/api/functional/calls/c20261103-151500-a1b2c3/file/papers/generated/SYN-B/booking.txt' in page['html']
    assert 'Read these papers with Muse Spark' in text
    assert "Clean synthetic papers: a no-model parser could read these too. Upload messier synthetic papers (scans, photos, papers that disagree) to test Muse Spark's intake." in text
    assert 'Synthetic papers only. Never upload real company or customer documents.' in text
    assert 'Use the generated load list, not read by a model' in text
    assert "You set each paper's role; the model never does" in text
    for role in ('booking', 'shipping_instructions', 'vgm', 'dg_declaration', 'gate_slip', 'reefer_list', 'oog_list', 'amendment', 'correspondence', 'unspecified'):
        assert f'value="{role}"' in page['html']
    assert not events(page, 'loadlist:ready')


def test_intake_generated_metadata_merges_same_names_by_booking():
    view = sample('needs-papers')
    view['papers']['documents'] = [{**p, 'path': None, 'origin':'generated'} for p in view['case']['papers']]
    view['papers']['documents'].append({'name':'uploaded.txt', 'origin':'uploaded', 'synthetic':True, 'role':'booking'})
    page = render('intake', view=view)
    assert words(page).count('Synthetic document') == 3
    assert '/file/papers/generated/SYN-A/booking.txt' in page['html']
    assert '/file/papers/generated/SYN-B/booking.txt' in page['html']
    assert 'uploaded.txt' not in page['html']


def medium_generated_papers():
    view=sample('needs-papers')
    papers=[]
    for booking in range(1,16):
        for number in range(7):
            name=f'B{booking}-paper-{number+1}.txt'
            papers.append({'name':name,'path':f'papers/generated/{name}','media_type':'text/plain',
                           'role':'booking' if number==0 else 'vgm','booking_ref':f'B{booking}','synthetic':True})
    papers.append({'name':'load-list.csv','path':'papers/generated/load-list.csv','media_type':'text/csv',
                   'role':'unspecified','booking_ref':None,'synthetic':True})
    view['case']['papers']=papers
    return view


def test_intake_generated_actions_and_parser_caption_precede_106_papers():
    page=render('intake',view=medium_generated_papers())
    start=min(page['html'].index('data-live-paper-booking'),page['html'].index('data-live-load-list-paper'))
    assert page['html'].index('data-action="read-generated"')<start
    assert page['html'].index('data-action="use-generated-load-list"')<start
    assert page['html'].index('Clean synthetic papers: a no-model parser could read these too.')<start
    assert page['html'].index('data-live-field="files"')>start


def test_intake_106_papers_fold_by_booking_with_load_list_visible_outside_groups():
    page=render('intake',view=medium_generated_papers())
    groups=re.findall(r'<details\b([^>]*data-live-paper-booking[^>]*)>(.*?)</details>',page['html'],re.S)
    assert len(groups)==15
    assert all(not re.search(r'\bopen(?:=|\s|$)',attrs) for attrs,_ in groups)
    assert all(body.count('Synthetic document')==7 for _,body in groups)
    assert 'Booking B3 · 7 papers' in words(page)
    unfolded=re.sub(r'<details\b.*?</details>','',page['html'],flags=re.S)
    assert 'load-list.csv' in unfolded and 'data-live-load-list-paper' in unfolded
    assert page['html'].index('data-live-load-list-paper')<page['html'].index('data-live-paper-booking')
    assert '/file/papers/generated/load-list.csv' in unfolded
    assert '/file/papers/generated/B15-paper-7.txt' in page['html']
    assert words(page).count('Synthetic document')==106


def test_intake_paper_group_expansion_survives_updates_and_resets_for_new_call():
    view=medium_generated_papers()
    steps=[{'selector':'[data-live-paper-booking="B3"]','open':True,'event':'toggle'}, {'call':view},{'status':KEY_READY}]
    page=render('intake',view=view,steps=steps)
    group=re.search(r'<details\b([^>]*data-live-paper-booking="B3"[^>]*)>',page['html']).group(1)
    assert re.search(r'\bopen(?:=|\s|$)',group)
    other=copy.deepcopy(view);other['id']='c20261103-151501-abcdef'
    page=render('intake',view=view,steps=[*steps,{'call':other}])
    assert not re.search(r'<details\b[^>]*\bopen=',page['html'])


@pytest.mark.parametrize('action,body',[('read-generated',{'use_generated_papers':True}),
                                       ('use-generated-load-list',{'use_generated_load_list':True})])
def test_intake_reordered_generated_actions_keep_the_same_request(action,body):
    page=render('intake',view=medium_generated_papers(),steps=[click(action)])
    assert page['posts']==[{'path':f"calls/{VIEWS['ready']['id']}/papers",'body':body}]


def test_intake_needs_answers_generated_choice_stays_below_questions_and_above_papers():
    view=many_booking_questions();view['case']['papers']=medium_generated_papers()['case']['papers']
    page=render('intake',view=view)
    choice=page['html'].index('data-action="use-generated-load-list"')
    assert page['html'].rindex('data-question-id=')<choice<page['html'].index('data-live-load-list-paper')
    assert '90 questions across 15 bookings' in words(page)


def test_intake_short_booking_paper_title_keeps_full_reference_and_download_path():
    view=medium_generated_papers()
    for doc in view['case']['papers']:
        if doc['booking_ref']:doc['booking_ref']='SYN-abcdef-'+doc['booking_ref']
    page=render('intake',view=view)
    assert 'Booking B3 · 7 papers' in words(page)
    assert 'data-live-paper-booking="SYN-abcdef-B3"' in page['html']
    assert '/file/papers/generated/B3-paper-1.txt' in page['html']


@pytest.mark.parametrize('action,body', [('read-generated', {'use_generated_papers': True}),
                                       ('use-generated-load-list', {'use_generated_load_list': True})])
def test_intake_generated_choices_post_explicit_body(action, body):
    page = render('intake', 'needs-papers', [click(action)])
    assert page['posts'] == [{'path': f"calls/{VIEWS['ready']['id']}/papers", 'body': body}]


def test_intake_pasted_paper_sends_host_metadata():
    page = render('intake', 'needs-papers', [
        field('[data-live-field="paste"]', 'SYNTHETIC DOCUMENT\nNo reefers in SYN-A.'),
        field('[data-live-field="role"]', 'reefer_list', 'change'),
        field('[data-live-field="booking-ref"]', 'SYN-A'),
        {'selector': '[data-live-field="complete-booking-list"]', 'checked': True, 'event': 'change'},
        click('read-uploaded'),
    ])
    doc = page['posts'][0]['body']['documents'][0]
    assert doc == {'name': 'pasted-papers.txt', 'media_type': 'text/plain', 'text': 'SYNTHETIC DOCUMENT\nNo reefers in SYN-A.',
                   'role': 'reefer_list', 'booking_ref': 'SYN-A', 'complete_booking_list': True, 'synthetic': False}


def test_intake_uploaded_papers_have_independent_roles():
    page = render('intake', 'needs-papers', [
        {'selector': '[data-live-field="files"]', 'event': 'change', 'files': [
            {'name': 'mass.csv', 'type': 'text/csv', 'bytes': [65, 66]}, {'name': 'booking.txt', 'type': 'text/plain', 'bytes': [67]}]},
        field('[data-file-index="0"][data-doc-field="role"]', 'vgm', 'change'),
        field('[data-file-index="0"][data-doc-field="booking_ref"]', 'SYN-A'),
        field('[data-file-index="1"][data-doc-field="role"]', 'booking', 'change'),
        field('[data-file-index="1"][data-doc-field="booking_ref"]', 'SYN-B'), click('read-uploaded'),
    ])
    docs = page['posts'][0]['body']['documents']
    assert [(d['role'], d['booking_ref'], d['data_b64']) for d in docs] == [('vgm', 'SYN-A', 'QUI='), ('booking', 'SYN-B', 'Qw==')]
    assert all(d['synthetic'] is False for d in docs)


def test_intake_live_elapsed_and_per_booking_progress():
    page = render('intake', 'reading-papers', [{'tick': True}])
    text = words(page)
    assert re.search(r'\d+\.\d s elapsed', text)
    assert 'measured on this machine, this run' in text
    assert 'Booking SYN-A · done' in text and 'Booking SYN-B · running' in text
    assert not page['posts'] and not events(page, 'loadlist:ready')


def test_intake_booking_progress_from_actual_backend_stage_records():
    view = sample('reading-papers'); del view['papers']['progress']
    view['stages'] += [
        {'stage':'intake','instance_id':'bi1','who':'Muse Spark · booking SYN-A','status':'running'},
        {'stage':'intake','instance_id':'bi2','who':'Muse Spark · booking SYN-B','status':'running'},
        {'stage':'intake','instance_id':'bi1','who':'Muse Spark · booking SYN-A · 2 boxes','status':'done','wall_s':3.2},
    ]
    page = render('intake', view=view)
    assert 'Booking SYN-A · done' in words(page)
    assert 'Booking SYN-A · running' not in words(page)
    assert 'Booking SYN-B · running' in words(page)


def test_intake_questions_submit_two_answers_and_publish_only_ready_rows():
    page = render('intake', 'needs-answers')
    assert 'What is the gross mass' in words(page) and 'Is SYNX0000002 a reefer?' in words(page)
    assert not events(page, 'loadlist:ready')
    page = render('intake', 'needs-answers', [
        field('[data-question-id="q1"]', '21000'), field('[data-question-id="q2"]', 'no', 'change'),
        click('submit-answers', sample('ready')),
    ])
    assert page['posts'][0]['body'] == {'answers': {'q1': '21000', 'q2': 'no'}}
    assert events(page, 'loadlist:ready') == [VIEWS['ready']['load_list']['rows']]
    assert '2 boxes read by Muse Spark' in page['statuses']


def test_intake_incomplete_answers_do_not_post():
    page = render('intake', 'needs-answers', [field('[data-question-id="q1"]', '21000'), click('submit-answers')])
    assert not page['posts'] and 'Answer every cargo question' in words(page)


def many_booking_questions():
    view=sample('needs-answers')
    intake=view['papers']['intake']
    intake['bookings']=[{'id':f'b{i:04d}','booking_ref':f'B{i+1}','complete':False} for i in range(15)]
    intake['questions']=[{'id':f'B{i+1}::b{i:04d}::q{j}','text':f'Booking {i+1}, question {j+1}: confirm the synthetic field.',
                          'field':'gross_mass_kg','box':f'SYNX{i:03d}{j:04d}'} for i in range(15) for j in range(6)]
    return view


def test_intake_needs_answers_keeps_generated_list_choice_below_cards():
    page=render('intake','needs-answers')
    assert not control(page,'use-generated-load-list')['disabled']
    assert "Spark's reading is set aside; the generator's load list is planned instead" in words(page)
    assert page['html'].index('data-action="use-generated-load-list"') > page['html'].rindex('data-question-id=')
    assert page['html'].count('data-action="use-generated-load-list"') == 1
    assert not any(c.get('action')=='read-generated' for c in page['controls'])


def test_intake_needs_answers_generated_choice_bypasses_answers_and_paid_setup():
    page=render('intake',view=many_booking_questions(),status=KEY_ABSENT,
                steps=[click('use-generated-load-list',sample('ready-generated'))])
    assert page['posts']==[{'path':f"calls/{VIEWS['ready']['id']}/papers",'body':{'use_generated_load_list':True}}]
    assert events(page,'loadlist:ready')==[sample('ready-generated')['load_list']['rows']]
    assert '2 boxes from the generated load list (not read by a model)' in page['statuses']


def test_intake_many_questions_are_counted_and_collapsed_by_booking():
    view=many_booking_questions()
    view['papers']['intake']['bookings'].append({'id':'b0015','booking_ref':'B16','complete':True})
    page=render('intake',view=view)
    assert '90 questions across 15 bookings' in words(page)
    groups=re.findall(r'<details\b([^>]*data-live-question-booking[^>]*)>(.*?)</details>',page['html'],re.S)
    assert len(groups)==15 and page['html'].count('data-question-id=')==90
    assert all(not re.search(r'\bopen(?:=|\s|$)',attrs) for attrs,_ in groups)
    assert all(body.count('data-question-id=')==6 for _,body in groups)
    assert page['html'].index('data-live-question-summary') < page['html'].index('<details')
    assert 'data-live-question-booking="B16"' not in page['html']
    assert not events(page,'loadlist:ready')


def test_intake_question_group_expansion_and_answers_survive_call_and_status_updates():
    view=many_booking_questions()
    page=render('intake',view=view,steps=[
        {'selector':'[data-live-question-booking="B1"]','open':True,'event':'toggle'},
        field('[data-question-id="B1::b0000::q0"]','21000'),{'call':view},{'status':KEY_READY}])
    group=re.search(r'<details\b([^>]*data-live-question-booking="B1"[^>]*)>',page['html'])
    assert re.search(r'\bopen(?:=|\s|$)',group.group(1))
    assert any(c['tag']=='INPUT' and c['value']=='21000' for c in page['controls'])
    other=re.search(r'<details\b([^>]*data-live-question-booking="B2"[^>]*)>',page['html'])
    assert not re.search(r'\bopen(?:=|\s|$)',other.group(1))


def test_intake_new_call_resets_question_expansion_and_answer_values():
    view=many_booking_questions();other=copy.deepcopy(view);other['id']='c20261103-151501-abcdef'
    page=render('intake',view=view,steps=[
        {'selector':'[data-live-question-booking="B1"]','open':True,'event':'toggle'},
        field('[data-question-id="B1::b0000::q0"]','21000'),{'call':other}])
    assert not any(c['tag']=='INPUT' and c['value']=='21000' for c in page['controls'])
    assert not re.search(r'<details\b[^>]*\bopen=',page['html'])


def test_intake_question_group_names_decode_host_namespaces_without_changing_post_ids():
    view=sample('needs-answers')
    first='SYN%2FA%20B::b0000::q1'
    view['papers']['intake']['questions'][0]['id']=first
    view['papers']['intake']['questions'][1].update(booking_ref='SYN/A B')
    page=render('intake',view=view,steps=[field(f'[data-question-id="{first}"]','21000'),
        field('[data-question-id="q2"]','no','change'),click('submit-answers')])
    assert '2 questions across 1 booking' in words(page)
    assert page['posts'][0]['body']=={'answers':{first:'21000','q2':'no'}}


def test_intake_unknown_or_cross_booking_questions_are_not_given_invented_references():
    view=sample('needs-answers')
    view['papers']['intake']['questions'].append({'id':'merge::duplicate-box::SYNX1','text':'Review duplicate synthetic booking membership.'})
    page=render('intake',view=view)
    assert 'Booking not specified' in words(page) and 'Across bookings' in words(page)
    assert 'Booking merge' not in words(page)
    assert page['html'].count('data-question-id=')==3


def test_intake_grouped_booking_labels_are_escaped():
    view=sample('needs-answers')
    for question in view['papers']['intake']['questions']:question['booking_ref']='<script>synthetic</script>'
    page=render('intake',view=view)
    assert '&lt;script&gt;synthetic&lt;/script&gt;' in page['html'] and '<script>' not in page['html']


@pytest.mark.parametrize('name,label', [('ready', '2 boxes read by Muse Spark'),
                                       ('ready-generated', '2 boxes from the generated load list (not read by a model)')])
def test_intake_ready_list_and_source_status(name, label):
    page = render('intake', name, [click('loadlist'), {'call': sample(name)}])
    assert label in words(page) and label in page['statuses']
    assert 'SYNX0000001' in words(page)
    assert len(events(page, 'loadlist:ready')) == 1
    assert page['list']['rows'][0]['box_id'] == 'SYNX0000001'


def test_intake_unavailable_offers_explicit_generated_alternative():
    page = render('intake', 'spark-unavailable', [click('read-generated')])
    assert "Muse Spark unavailable: MODEL_API_KEY isn't set in this launch. Nothing was replayed in its place." in words(page)
    assert control(page, 'read-generated')['disabled'] and not page['posts']
    assert not control(page, 'use-generated-load-list')['disabled']
    assert not events(page, 'loadlist:ready')


def test_intake_incomplete_api_answer_is_shown_verbatim_without_replay():
    reason = "Muse Spark's answer was cut off at its output limit (24576 tokens); nothing from it was used"
    page = render('intake', 'needs-papers', [click('read-generated', {'error': reason})])
    assert reason in words(page)
    assert not events(page, 'loadlist:ready')


def partial_intake():
    return json.loads((HERE / 'fixtures/planner-live/intake-partial-cap.json').read_text())


def public_booking_index():
    view=partial_intake()
    index=view['papers'].pop('intake_index')
    view['papers']['bookings']=[]
    for record in index['bookings']:
        result=record.get('view') or {}
        view['papers']['bookings'].append({'booking_ref':record['booking_ref'],'status':record['status'],
            'boxes':record['boxes'],'wall_s':result.get('read_wall_s'),'cost_usd':result.get('read_cost_usd'),
            'file':f"intake-results/{record['booking_ref']}.json",'sha256':'c'*64})
        if record.get('reason'):
            view['stages'].append({'stage':'intake','status':'failed','who':f"Muse Spark · booking {record['booking_ref']}",'notes':record['reason']})
    return view


def test_intake_actual_public_booking_index_includes_stage_failure_reasons():
    page=render('intake',view=public_booking_index())
    assert page['html'].count('data-live-booking-progress=')==11
    assert '10.0 s' in words(page) and '$0.1000' in words(page)
    assert 'The launch spend cap has been reached. Raise the cap before retrying.' in words(page)
    assert 'Retry the failed bookings' in words(page) and not control(page,'retry-failed-bookings')['disabled']
    assert not events(page,'loadlist:ready')


def test_intake_public_index_failed_and_reused_rows_come_from_latest_call_update():
    view=public_booking_index();view['state']='reading_papers'
    view['papers']['bookings'][0]['status']='reused'
    view['papers']['bookings'][9]['status']='reading'
    page=render('intake','needs-papers',steps=[{'call':view}])
    assert page['html'].count('data-live-booking-progress=')==11
    assert 'kept from earlier read' in words(page) or 'kept from an earlier read' in words(page)
    assert 'reading' in words(page).lower() and not events(page,'loadlist:ready')


def test_intake_public_index_booking_reasons_do_not_match_name_prefixes():
    view=public_booking_index()
    view['papers']['bookings']=[{'booking_ref':'B1','status':'failed'},{'booking_ref':'B10','status':'failed'}]
    view['stages']=[{'stage':'intake','status':'failed','who':'Muse Spark · booking B1','notes':'First booking has an unreadable paper.'},
                    {'stage':'intake','status':'failed','who':'Muse Spark · booking B10','notes':'Tenth booking hit the cap.'}]
    page=render('intake',view=view)
    b1=re.search(r'<p[^>]*data-live-booking-progress="B1"[^>]*>(.*?)</p>',page['html'],re.S).group(1)
    assert 'First booking has an unreadable paper.' in b1 and 'Tenth booking' not in b1


def test_intake_failed_call_still_offers_retry_and_skip_with_unfinished_index_row():
    view=public_booking_index();view['papers']['bookings'][9]['status']='reading'
    page=render('intake',view=view,steps=[click('retry-failed-bookings')])
    assert page['posts'][0]['body']=={'use_generated_papers':True}
    assert not control(page,'use-generated-load-list')['disabled']
    assert 'Muse Spark is reading the papers' not in page['statuses']


def test_intake_concurrency_note_with_at_once_wording_remains_after_failure():
    view=public_booking_index();view['stages'][0]['notes']='Reading 3 bookings at once; the rest are queued.'
    page=render('intake',view=view)
    assert 'Reading 3 bookings at once; the rest are queued.' in words(page)


def test_intake_partial_spend_cap_failure_keeps_all_booking_results_visible():
    page = render('intake',view=partial_intake())
    text = words(page)
    assert page['html'].count('data-live-booking-progress=') == 11
    assert 'Booking B1' in text and '10.0 s' in text and '$0.1000' in text
    assert 'Booking B9' in text and '18.0 s' in text and '$0.1800' in text
    assert 'Booking B10' in text and 'Booking B11' in text
    assert 'failed' in text and 'The launch spend cap has been reached. Raise the cap before retrying.' in text
    assert 'Reading up to 3 bookings at a time; completed bookings are kept on retry.' in text
    assert 'Retry the failed bookings' in text and 'Raise the spend cap' in text
    assert 'Use the generated load list, not read by a model' in text
    assert not events(page,'loadlist:ready')


def test_intake_index_shows_reading_and_kept_results_during_retry():
    view=partial_intake();view['state']='reading_papers'
    view['papers']['intake_index']['bookings'][0]['status']='kept'
    view['papers']['intake_index']['bookings'][9].update(status='reading',reason=None)
    page=render('intake',view=view)
    assert 'kept from an earlier read' in words(page) or 'kept from earlier read' in words(page)
    assert 'reading' in words(page).lower()
    assert page['html'].count('data-live-booking-progress=') == 11
    assert not any(c.get('action')=='retry-failed-bookings' and not c['disabled'] for c in page['controls'])
    assert not events(page,'loadlist:ready')


def test_intake_retry_sends_generated_choice_and_only_shows_kept_status_from_server():
    failed=partial_intake(); retry=copy.deepcopy(failed);retry['state']='reading_papers'
    for record in retry['papers']['intake_index']['bookings']:
        record['status']='kept' if record['status']=='done' else 'reading'
        record.pop('reason',None)
    page=render('intake',view=failed,steps=[click('retry-failed-bookings',retry)])
    assert page['posts'] == [{'path':f"calls/{failed['id']}/papers",'body':{'use_generated_papers':True}}]
    assert 'kept from an earlier read' in words(page) or 'kept from earlier read' in words(page)
    assert not events(page,'loadlist:ready')


def test_intake_retry_upload_uses_exact_original_documents_and_roles():
    failed=partial_intake();failed['papers']['intake_index']['choice']={'source':'uploaded'}
    failed['papers']['documents']=[{'name':'test.txt','origin':'pasted','role':'vgm','synthetic':True}]
    page=render('intake','needs-papers',steps=[field('[data-live-field="paste"]','SYNTHETIC DOCUMENT\nMass 21000 kg'),
        field('[data-live-field="role"]','vgm'),field('[data-live-field="booking-ref"]','B1'),
        click('read-uploaded',failed),click('retry-failed-bookings')])
    assert len(page['posts'])==2
    assert page['posts'][1]==page['posts'][0]
    assert page['posts'][1]['body']['documents'][0]['booking_ref']=='B1'


def test_intake_cap_button_opens_panel_and_does_not_raise_or_retry_automatically():
    page=render('intake',view=partial_intake(),steps=[click('raise-spend-cap')])
    assert page['keyPanels']==1 and not page['posts']
    assert 'status' in page['gets']


def test_intake_failed_read_can_explicitly_choose_generated_load_list_without_key():
    page=render('intake',view=partial_intake(),status=KEY_ABSENT,steps=[click('use-generated-load-list',sample('ready-generated'))])
    assert page['posts'][0]['body']=={'use_generated_load_list':True}
    assert events(page,'loadlist:ready')==[sample('ready-generated')['load_list']['rows']]


def test_intake_retry_still_requires_paid_call_consent():
    page=render('intake',view=partial_intake(),status=KEY_ABSENT,steps=[click('retry-failed-bookings')])
    assert not page['posts'] and control(page,'retry-failed-bookings')['disabled']


def test_intake_retry_keeps_index_visible_if_server_rejects_before_worker_starts():
    page=render('intake',view=partial_intake(),steps=[click('retry-failed-bookings',{'error':'The launch spend cap has been reached.'})])
    assert page['html'].count('data-live-booking-progress=')==11
    assert 'Raise the spend cap' in words(page)
    assert 'Retry the failed bookings' in words(page)
    assert not events(page,'loadlist:ready')


def test_intake_cap_panel_and_generated_skip_stay_safe_without_optional_shell_hook():
    page=render('intake',view=partial_intake(),no_key_panel=True,steps=[click('raise-spend-cap')])
    assert not page['posts']
    assert not control(page,'use-generated-load-list')['disabled']


def test_intake_reload_of_uploaded_failure_does_not_silently_read_generated_papers():
    view=partial_intake();view['papers']['intake_index']['choice']={'source':'uploaded'}
    view['papers']['documents'].append({'name':'test.txt','origin':'uploaded','role':'vgm'})
    page=render('intake',view=view)
    retry=[c for c in page['controls'] if c.get('action')=='retry-failed-bookings']
    assert not retry or retry[0]['disabled']
    assert not page['posts']


def test_intake_retry_needs_choice_provenance_even_if_booking_refs_match_generated_set():
    view=partial_intake();view['papers']['documents']=[]
    view['papers']['intake_index'].pop('choice')
    view['papers']['intake_index']['bookings'][0]['booking_ref']='SYN-A'
    view['papers']['intake_index']['bookings']=view['papers']['intake_index']['bookings'][:1]
    page=render('intake',view=view)
    assert control(page,'retry-failed-bookings')['disabled']
    assert 'Upload or paste the same papers again' in words(page)


def test_intake_index_failure_reason_is_escaped_and_non_cap_failure_has_no_raise_button():
    view=partial_intake();view['message']='An intake booking failed.'
    for stage in view['stages']:stage['notes']='An intake booking failed.'
    for record in view['papers']['intake_index']['bookings'][9:]:record['reason']='Bad <script>synthetic</script> paper.'
    page=render('intake',view=view)
    assert '&lt;script&gt;synthetic&lt;/script&gt;' in page['html']
    assert '<script>' not in page['html']
    assert not any(c.get('action')=='raise-spend-cap' for c in page['controls'])


def test_intake_failed_index_does_not_publish_stale_complete_receipt():
    view=partial_intake();view['papers']['intake']=sample('ready')['papers']['intake'];view['load_list']=sample('ready')['load_list']
    page=render('intake',view=view)
    assert not events(page,'loadlist:ready')
    assert '2 boxes read by Muse Spark' not in page['statuses']


def test_intake_answers_are_credited_to_planner_and_original_read_metrics_survive():
    view = sample('needs-answers')
    view['papers']['intake'].update(read_wall_s=18.7,read_cost_usd=0.0432,answer_wall_s=0.001,wall_s=0.001,cost_usd=0.0432)
    page = render('intake', view=view)
    assert 'Your answers · no model' in words(page)
    assert '18.7 s' in words(page) and '$0.0432' in words(page)
    assert words(page).count('$0.0432') == 1
    # Credits and spend belong to separate cards, including before all answers arrive.
    assert re.search(r'data-live-read-summary', page['html'])
    answer_card = re.search(r'<section[^>]*data-live-answers-credit[^>]*>(.*?)</section>', page['html'], re.S)
    assert answer_card is not None and '$' not in answer_card.group(1)


def test_intake_per_booking_read_times_and_summed_spend_after_answers():
    view = sample('ready')
    view['papers']['intake'].update(read_wall_s=19.2,read_cost_usd=0.0415,answer_wall_s=0.002,wall_s=0.002,
        bookings=[{'booking_ref':'B3','boxes':8,'complete':True,'read_wall_s':11.5,'wall_s':0.001,'read_cost_usd':0.021},
                  {'booking_ref':'B4','boxes':8,'complete':True,'read_wall_s':7.7,'wall_s':0.001,'read_cost_usd':0.0205}])
    page = render('intake', view=view)
    assert 'Booking B3 · 8 boxes · 11.5 s from start to answer, including any wait for spend room' in words(page)
    assert 'Booking B4 · 8 boxes · 7.7 s from start to answer, including any wait for spend room' in words(page)
    assert '$0.0415' in words(page) and words(page).count('$0.0415') == 1


def test_intake_sums_booking_costs_when_no_aggregate_metric_is_supplied():
    view = sample('ready')
    intake = view['papers']['intake']
    intake.pop('cost_usd')
    intake['bookings'] = [{'booking_ref':'B3','boxes':1,'complete':True,'wall_s':11.5,'cost_usd':0.021},
                          {'booking_ref':'B4','boxes':1,'complete':True,'wall_s':7.7,'cost_usd':0.0205}]
    page = render('intake', view=view)
    assert '$0.0415' in words(page)


# Round-2 review: accounting scopes, honest partial reads, and input labels.
def test_intake_elapsed_and_total_booking_time_are_distinct_measurements():
    view=sample('ready')
    view['papers']['intake'].update(read_elapsed_s=8.4,read_wall_s=19.2,max_concurrency=3,
        bookings=[{'booking_ref':'B3','boxes':1,'wall_s':11.5,'cost_usd':0.021},
                  {'booking_ref':'B4','boxes':1,'wall_s':7.7,'cost_usd':0.0205}])
    page=render('intake',view=view)
    assert "8.4 s elapsed for Muse Spark's reading of 2 bookings (up to 3 at a time); 19.2 s of booking time in total; measured on this machine, this run" in words(page)
    assert '19.2 s elapsed' not in words(page)
    assert '11.5 s from start to answer, including any wait for spend room' in words(page)


def test_intake_aggregate_stage_elapsed_is_used_without_inventing_concurrency():
    view=sample('ready');view['papers']['intake'].update(read_wall_s=120.7)
    view['stages']=[{'stage':'intake','status':'done','who':'Muse Spark','wall_s':40.3}]
    page=render('intake',view=view)
    assert '40.3 s elapsed' in words(page) and '120.7 s of booking time in total' in words(page)
    assert 'up to 3 at a time' not in words(page) and 'up to 6 at a time' not in words(page)


def test_intake_unknown_elapsed_is_not_replaced_with_summed_booking_time():
    view=sample('ready');view['papers']['intake'].update(read_wall_s=120.7)
    page=render('intake',view=view)
    assert '120.7 s of booking time in total' in words(page)
    assert '120.7 s elapsed' not in words(page)


def test_intake_partial_cap_read_explicitly_keeps_work_and_plans_nothing():
    view=public_booking_index()
    page=render('intake',view=view)
    assert '9 other bookings were read and are kept for a retry; nothing has been planned from a partial reading.' in words(page)
    assert 'Raise the cap in the API key panel to read the rest.' in words(page)
    assert '58' not in words(page)  # no question count was supplied
    assert not events(page,'loadlist:ready')


def test_intake_completed_read_is_not_called_partial_when_a_later_engine_run_fails():
    view=sample('ready');view['state']='failed';view['message']='The engine run failed.'
    view['stages']=[{'stage':'intake','status':'done','who':'Muse Spark'}, {'stage':'engine','status':'failed'}]
    page=render('intake',view=view)
    assert 'nothing has been planned from a partial reading' not in words(page).lower()
    assert not any(c.get('action')=='retry-failed-bookings' for c in page['controls'])


def test_intake_cap_refusal_uses_exact_review_message_only_with_explicit_facts():
    view=public_booking_index()
    view['message']='The booking was not sent.'
    for stage in view['stages']:
        if stage['status']=='failed':stage['notes']='The booking was not sent.'
    view['papers']['bookings'][9]['cap_refusal']={'booking_ref':'B10','spent_usd':0.67,'hold_usd':1.384448,
        'cap_usd':2.0,'sent':False,'charged_usd':0}
    page=render('intake',view=view)
    exact="Booking B10 wasn't sent: $0.67 spent plus this call's worst-case hold of $1.384448 would pass your $2.00 cap. Nothing was charged for it. 9 other bookings were read and are kept for a retry; nothing has been planned from a partial reading. Raise the cap in the API key panel to read the rest."
    assert exact in words(page)
    assert 'Raise the spend cap' in words(page)


def test_intake_cap_refusal_does_not_infer_uncharged_from_missing_metadata():
    view=public_booking_index()
    view['papers']['bookings'][9]['cap_refusal']={'booking_ref':'B10','spent_usd':0.67,'hold_usd':1.384448,'cap_usd':2.0}
    page=render('intake',view=view)
    assert "Booking B10 wasn't sent:" not in words(page)
    assert 'Nothing was charged for it.' not in words(page)


def test_intake_partial_question_totals_are_shown_only_from_booking_metadata():
    view=public_booking_index()
    for record,count in zip(view['papers']['bookings'],[10,10,10,10,10,8,0,0,0]):record['question_count']=count
    page=render('intake',view=view)
    assert '6 of those bookings still hold open questions (58 in all).' in words(page)


def test_intake_exact_server_cap_explanation_is_preserved_and_opens_cap_panel():
    view=public_booking_index()
    exact="Booking B10 wasn't sent: $0.67 spent plus this call's worst-case hold of $1.384448 would pass your $2.00 cap. Nothing was charged for it. 9 other bookings were read and are kept for a retry; nothing has been planned from a partial reading. Raise the cap in the API key panel to read the rest."
    view['message']=exact
    for stage in view['stages']:
        if stage['status']=='failed':stage['notes']=exact
    page=render('intake',view=view,steps=[click('raise-spend-cap')])
    assert exact in words(page) and page['keyPanels']==1
    assert not page['posts']


def test_intake_spend_advice_points_to_visible_key_panel():
    view=partial_intake()
    view['message']="Muse Spark's spend cap prevented the call from completing; check the spend ledger."
    view['stages'][-1]['notes']=view['message']
    for record in view['papers']['intake_index']['bookings'][9:]:record['reason']=view['message']
    page=render('intake',view=view)
    assert 'check the spend ledger' not in words(page).lower()
    assert 'see spent and held in the API key panel.' in words(page)


@pytest.mark.parametrize('name',['needs-papers','ready-generated'])
def test_intake_never_credits_spark_when_no_read_ran(name):
    page=render('intake',name)
    assert 'Muse Spark · Meta Model API, cloud' not in words(page)
    if name=='ready-generated':assert 'Generated load list · software, no model call' in words(page)


def test_intake_partial_index_times_include_possible_spend_wait():
    page=render('intake',view=public_booking_index())
    assert '10.0 s from start to answer, including any wait for spend room' in words(page)


@pytest.mark.parametrize('area', ['intake','instruct'])
def test_inputs_never_invent_seed_claims(area):
    view = sample('needs-papers' if area == 'intake' else 'ready')
    view['case']['seed_status'] = {'label':'Fresh seed: in no published map set','unseen':True}
    page = render(area, view=view)
    assert 'unseen' not in words(page).lower()
    assert 'unseen' not in (STATIC / f'planner-live-{area}.js').read_text().lower()


KEY_READY = {'key':{'present':True,'paid_calls_accepted':True,'cap_usd':2,'spent_usd':0}}
KEY_ABSENT = {'key':{'present':False,'paid_calls_accepted':False,'cap_usd':2,'spent_usd':0}}
CONSENT_ABSENT = {'key':{'present':True,'paid_calls_accepted':False,'cap_usd':2,'spent_usd':0}}


@pytest.mark.parametrize('area', ['intake','instruct'])
@pytest.mark.parametrize('launch_status', [KEY_ABSENT,CONSENT_ABSENT,{}, {'error':'Status is unavailable.'}])
def test_paid_input_actions_fail_closed_without_key_and_consent(area, launch_status):
    steps = []
    if area == 'intake':
        steps = [field('[data-live-field="paste"]','SYNTHETIC DOCUMENT'), click('read-uploaded'), click('read-generated'), {'lifecycle':'start'}]
    else:
        steps = [{'selector':'[data-live-audio]','files':[wav_file()],'event':'change'}, click('transcribe')]
    page = render(area, 'needs-papers' if area == 'intake' else 'ready', steps, status=launch_status)
    assert not page['posts']
    assert control(page, 'read-generated' if area == 'intake' else 'transcribe')['disabled']
    assert any(c.get('action') == 'open-key-panel' for c in page['controls'])
    if launch_status == KEY_ABSENT:
        assert 'No Meta Model API key: enter yours in the app, or put MODEL_API_KEY in .env (see .env.example).' in words(page)
    elif launch_status == CONSENT_ABSENT:
        assert 'paid calls' in words(page).lower()


@pytest.mark.parametrize('area', ['intake','instruct'])
def test_key_panel_button_refreshes_gate_without_handling_a_secret(area):
    page = render(area, 'needs-papers' if area == 'intake' else 'ready', [click('open-key-panel')],
                  status=KEY_ABSENT, key_panel_status=KEY_READY)
    assert page['keyPanels'] == 1 and not page['posts']
    if area == 'intake':
        assert not control(page, 'read-generated')['disabled']
    else:
        assert 'No Meta Model API key:' not in words(page)


@pytest.mark.parametrize('area', ['intake','instruct'])
def test_missing_optional_key_panel_hook_is_safe(area):
    page = render(area, 'needs-papers' if area == 'intake' else 'ready', [click('open-key-panel')], status=KEY_ABSENT, no_key_panel=True)
    assert not page['posts'] and page['keyPanels'] == 0


@pytest.mark.parametrize('area', ['intake','instruct'])
def test_paid_action_rechecks_launch_status_before_posting(area):
    steps = [] if area == 'intake' else [{'selector':'[data-live-audio]','files':[wav_file()],'event':'change'}]
    steps += [{'status_only':CONSENT_ABSENT}, click('read-generated' if area == 'intake' else 'transcribe')]
    page = render(area, 'needs-papers' if area == 'intake' else 'ready', steps, status=KEY_READY)
    assert not page['posts'] and len(page['gets']) >= 2


@pytest.mark.parametrize('area', ['intake','instruct'])
def test_key_setup_recovers_from_a_prior_missing_key_attempt_on_same_call(area):
    view = sample('needs-papers' if area == 'intake' else 'ready')
    stage_name, model_name = ('intake','spark') if area == 'intake' else ('voice','voice')
    reason = "No Meta Model API key: enter yours in the app, or put MODEL_API_KEY in .env (see .env.example)."
    view['availability'][model_name] = {'available':False,'reason':reason}
    view['stages'] = [{'stage':stage_name,'status':'unavailable','notes':reason}]
    launch_status = {**KEY_READY, 'availability':{model_name:{'available':True,'reason':None}}}
    steps = [] if area == 'intake' else [{'selector':'[data-live-audio]','files':[wav_file()],'event':'change'}]
    steps += [click('open-key-panel'),click('read-generated' if area == 'intake' else 'transcribe')]
    page = render(area, view=view, steps=steps, status=KEY_ABSENT, key_panel_status=launch_status)
    assert len(page['posts']) == 1


def test_voice_key_setup_does_not_clear_runtime_spend_limit_failure():
    view = sample('ready')
    view['stages'] = [{'stage':'voice','status':'unavailable','notes':'The launch spend cap has been reached.'}]
    page = render('instruct', view=view, status={**KEY_READY,'availability':{'voice':{'available':True}}},
                  steps=[{'selector':'[data-live-audio]','files':[wav_file()],'event':'change'}, click('transcribe')])
    assert not page['posts'] and 'The launch spend cap has been reached' in words(page)


def test_answers_generated_skip_and_local_reader_do_not_require_paid_consent():
    page = render('intake','needs-answers',[field('[data-question-id="q1"]','21000'),field('[data-question-id="q2"]','no'),click('submit-answers')],status=KEY_ABSENT)
    assert page['posts'][0]['path'].endswith('/answers')
    page = render('intake','needs-papers',[click('use-generated-load-list')],status=KEY_ABSENT)
    assert page['posts'][0]['body'] == {'use_generated_load_list':True}
    page = render('instruct',steps=[field('[data-live-sentence]','Keep bay 7 clear.'),click('read')],status=KEY_ABSENT)
    assert page['posts'][0]['path'].endswith('/read')


def generator_review_call():
    """Synthetic policy questions using the frozen intake's public projection."""
    view=sample('needs-answers')
    intake=view['papers']['intake']
    intake['rows']=[{'id':'SYNX-EQUIPMENT','iso_type':'22U1'},
                    {'id':'SYNX-OOG','iso_type':'42G1'},
                    {'id':'SYNX-MASS','iso_type':'22G1'}]
    intake['questions']=[
        {'id':'equipment','box':'SYNX-EQUIPMENT','field':'iso_type','booking_ref':'B2',
         'text':'SYNX-EQUIPMENT: 22U1 needs equipment review; this intake supports general purpose and reefer groups.'},
        {'id':'oog','box':'SYNX-OOG','field':'oog','booking_ref':'B2',
         'text':'SYNX-OOG: listed OOG cargo requires verified dimensions; an absent model reading cannot declare it in gauge.'},
        {'id':'mass','box':'SYNX-MASS','field':'gross_mass_kg','booking_ref':'B2',
         'text':'SYNX-MASS needs a verified gross mass.'}]
    intake['bookings']=[]
    view['load_list']=None
    return view


def choose_generator_box(box,checked=True):
    return {'selector':f'[data-live-generator-box="{box}"]','checked':checked,'event':'change'}


def mixed_generator_load_list(view):
    updated=copy.deepcopy(view)
    updated['state']='ready'
    updated['papers']['intake'].update(complete=True,questions=[],boxes=3)
    rows=[dict(row,origin='generator, not read by a model' if row['id']=='SYNX-EQUIPMENT' else 'muse_spark')
          for row in updated['papers']['intake']['rows']]
    updated['load_list']={'rows':rows,'boxes':3,'origin':'Muse Spark, with 1 boxes from the generator (not read by a model)'}
    return updated


def test_intake_generator_record_options_only_for_policy_questions():
    page=render('intake',view=generator_review_call())
    assert words(page).count("Use the generator's record for this box (not read by a model)")==2
    assert 'data-live-generator-box="SYNX-EQUIPMENT"' in page['html']
    assert 'data-live-generator-box="SYNX-OOG"' in page['html']
    assert 'data-live-generator-box="SYNX-MASS"' not in page['html']
    assert control(page,'use-generator-records')['disabled']
    assert not events(page,'loadlist:ready')


def test_intake_generator_records_posts_selected_boxes_without_paid_consent():
    view=generator_review_call()
    page=render('intake',view=view,status=KEY_ABSENT,steps=[choose_generator_box('SYNX-EQUIPMENT'),
        choose_generator_box('SYNX-OOG'),click('use-generator-records')])
    assert page['posts']==[{'path':f"calls/{view['id']}/papers",'body':{
        'use_generator_records_for':['SYNX-EQUIPMENT','SYNX-OOG']}}]
    assert 'Use generator records for 2 boxes' in words(page)


def test_intake_generator_records_empty_selection_does_not_post():
    page=render('intake',view=generator_review_call(),steps=[choose_generator_box('SYNX-EQUIPMENT'),
        choose_generator_box('SYNX-EQUIPMENT',False),click('use-generator-records')])
    assert not page['posts'] and control(page,'use-generator-records')['disabled']


def test_intake_generator_records_require_every_open_question_for_the_box_to_qualify():
    view=generator_review_call()
    view['papers']['intake']['questions'].append({'id':'equipment-mass','box':'SYNX-EQUIPMENT',
        'field':'gross_mass_kg','text':'Confirm this box has verified gross mass.'})
    page=render('intake',view=view)
    assert 'data-live-generator-box="SYNX-EQUIPMENT"' not in page['html']
    assert 'data-live-generator-box="SYNX-OOG"' in page['html']


@pytest.mark.parametrize('change',[
    {'field':'gross_mass_kg'},
    {'text':'SYNX-EQUIPMENT requires verified dimensions.'},
    {'text':'SYNX-EQUIPMENT: 22U1 needs equipment review; this intake supports general purpose and reefer groups. Confirm mass first.'},
    {'text':'SYNX-EQUIPMENT: 45G1 needs equipment review; this intake supports general purpose and reefer groups.'},
    {'box':None},
    {'reason':'conflicting_values'},
])
def test_intake_generator_records_do_not_match_other_or_lookalike_questions(change):
    view=generator_review_call()
    view['papers']['intake']['questions'][0].update(change)
    page=render('intake',view=view)
    assert 'data-live-generator-box="SYNX-EQUIPMENT"' not in page['html']


@pytest.mark.parametrize('rows', [[],[{'id':'SYNX-EQUIPMENT','iso_type':'22U1'}]*2])
def test_intake_generator_record_choice_requires_one_matching_intake_row(rows):
    view=generator_review_call();view['papers']['intake']['rows']=rows
    assert 'data-live-generator-box="SYNX-EQUIPMENT"' not in render('intake',view=view)['html']


def test_intake_generator_record_selection_survives_poll_and_is_pruned_after_new_blocker():
    view=generator_review_call()
    page=render('intake',view=view,steps=[choose_generator_box('SYNX-EQUIPMENT'),{'call':view}])
    assert 'Use generator records for 1 box' in words(page)
    assert not control(page,'use-generator-records')['disabled']
    blocked=copy.deepcopy(view)
    blocked['papers']['intake']['questions'].append({'id':'new-mass','box':'SYNX-EQUIPMENT',
        'field':'gross_mass_kg','text':'Verify mass.'})
    page=render('intake',view=view,steps=[choose_generator_box('SYNX-EQUIPMENT'),{'call':blocked},click('use-generator-records')])
    assert not page['posts'] and control(page,'use-generator-records')['disabled']


def test_intake_generator_record_selection_is_per_call():
    view=generator_review_call();other=copy.deepcopy(view);other['id']='c20261103-151501-abcdef'
    page=render('intake',view=view,steps=[choose_generator_box('SYNX-EQUIPMENT'),{'call':other},click('use-generator-records')])
    assert not page['posts'] and control(page,'use-generator-records')['disabled']


def test_intake_generator_records_deduplicate_a_box_with_two_policy_questions():
    view=generator_review_call()
    view['papers']['intake']['questions'].append({'id':'equipment-oog','box':'SYNX-EQUIPMENT','field':'oog',
        'text':'SYNX-EQUIPMENT: listed OOG cargo requires verified dimensions; an absent model reading cannot declare it in gauge.'})
    page=render('intake',view=view,steps=[choose_generator_box('SYNX-EQUIPMENT'),click('use-generator-records')])
    assert page['posts'][0]['body']=={'use_generator_records_for':['SYNX-EQUIPMENT']}
    assert page['html'].count('data-live-generator-box="SYNX-EQUIPMENT"')==1


def test_intake_mixed_rows_and_status_use_call_view_provenance():
    view=generator_review_call();updated=mixed_generator_load_list(view)
    page=render('intake',view=view,steps=[choose_generator_box('SYNX-EQUIPMENT'),click('use-generator-records',updated),click('loadlist')])
    assert updated['load_list']['origin'] in page['statuses'][-1]
    assert updated['load_list']['origin'] in page['list']['meta']['note']
    rows=dict(re.findall(r'<tr data-load-row="([^"]+)">(.*?)</tr>',page['html'],re.S))
    assert 'generator, not read by a model' in rows['SYNX-EQUIPMENT']
    assert 'Muse Spark' in rows['SYNX-MASS'] and 'Muse Spark' in rows['SYNX-OOG']
    assert '3 boxes read by Muse Spark' not in words(page)
    assert events(page,'loadlist:ready')==[updated['load_list']['rows']]


def test_intake_generator_load_list_rows_say_not_read_by_a_model():
    view=sample('ready');view['papers']['intake']={'skipped':True}
    view['load_list']['origin']='generator'
    for row in view['load_list']['rows']:row['origin']='generator, not read by a model'
    page=render('intake',view=view,steps=[click('loadlist')])
    assert all('generator, not read by a model' in body for body in re.findall(r'<tr data-load-row="[^"]+">(.*?)</tr>',page['html'],re.S))


@pytest.mark.parametrize('merged_booking',[False,True])
def test_intake_real_b2_fixture_generator_choice_and_mixed_load_list(merged_booking):
    fixture=json.loads((HERE/'fixtures/planner-live/intake-generator-record-b2.json').read_text())
    view=sample('needs-answers');view['papers']['intake']=copy.deepcopy(fixture['intake'])
    view['load_list']=None
    if merged_booking:
        booking_ref=fixture['source']['booking_ref']
        view['papers']['intake']['bookings']=[{'id':'b0002','booking_ref':booking_ref}]
        for question in view['papers']['intake']['questions']:
            question['id']=f"{booking_ref}::b0002::{question['id']}"
    selected=fixture['expected']['eligible_box_ids'][0]
    updated=copy.deepcopy(view);updated['state']='ready'
    updated['papers']['intake'].update(complete=True,questions=[],generator_record_ids=[selected])
    rows=[dict(fixture['generator_record'] if row['id']==selected else row,
        origin='generator, not read by a model' if row['id']==selected else 'muse_spark')
        for row in fixture['intake']['rows']]
    updated['load_list']={'rows':rows,'boxes':len(rows),'origin':fixture['expected']['call_load_list_origin']}
    page=render('intake',view=view,steps=[choose_generator_box(selected),click('use-generator-records',updated),click('loadlist')])
    assert page['posts']==[{'path':f"calls/{view['id']}/papers",'body':fixture['expected']['papers_body']}]
    assert fixture['expected']['call_load_list_origin'] in page['statuses'][-1]
    assert events(page,'loadlist:ready')==[rows]
    rendered_rows=dict(re.findall(r'<tr data-load-row="([^"]+)">(.*?)</tr>',page['html'],re.S))
    assert 'generator, not read by a model' in rendered_rows[selected]
    assert all('Muse Spark' in rendered_rows[box] for box in fixture['expected']['remaining_spark_box_ids'])
    assert '6 boxes read by Muse Spark' not in words(page)


def test_intake_b2_fixture_matches_optional_local_synthetic_booking_record():
    """Read local development evidence without changing it; CI may omit the call."""
    fixture=json.loads((HERE/'fixtures/planner-live/intake-generator-record-b2.json').read_text())
    source=HERE.parents[1]/fixture['source']['record_path']
    if not source.is_file():
        pytest.skip('Optional local synthetic B2 booking record is absent')
    original=source.read_bytes();record=json.loads(original)
    assert hashlib.sha256(original).hexdigest()==fixture['source']['record_sha256']
    assert {key:record[key] for key in fixture['source']['retained_intake_fields']}==fixture['intake']
    assert record['raw']['questions']==fixture['source']['raw_questions']
    view=sample('needs-answers');view['papers']['intake']=copy.deepcopy(fixture['intake']);view['load_list']=None
    page=render('intake',view=view,steps=[choose_generator_box('QZXU0000117'),click('use-generator-records')])
    assert page['posts'][0]['body']==fixture['expected']['papers_body']
    assert source.read_bytes()==original


# Instructions cases.
@pytest.mark.parametrize('name', ['ready', 'voice-transcript'])
def test_instruct_only_received_transcript_is_shown(name):
    page = render('instruct', name)
    if name == 'voice-transcript':
        assert 'Keep bay 7 clear.' in page['html'] or 'Keep bay 7 clear.' in str(page['controls'])
        assert 'Editable transcript · Recorded in this browser' in words(page)
    else:
        assert 'Keep bay 7 clear.' not in page['html']
    assert not page['posts']


@pytest.mark.parametrize('name,value,origin', [('ready','Keep bay 7 clear.','typed'),
                                              ('voice-transcript',None,'voice'),
                                              ('voice-transcript','Keep bay 8 clear.','planner_edit')])
def test_instruct_read_sentence_origin(name, value, origin):
    steps = [] if value is None else [field('[data-live-sentence]', value)]
    page = render('instruct', name, [*steps, click('read')])
    assert page['posts'][0]['body'] == {'sentence': value or 'Keep bay 7 clear.', 'origin': origin}
    assert page['posts'][0]['path'].endswith('/read')


def test_instruct_reading_card_has_live_evidence_and_confirm_hashes():
    view = sample('constraint-ready'); confirmed = copy.deepcopy(view); confirmed['readings'][0]['confirmed'] = True
    page = render('instruct', 'constraint-ready', [click('confirm', confirmed)])
    text = words(page)
    assert 'Nothing loads into bay 07.' in text and 'eeeeeeeeeeee' in text
    assert '4.20 s' in text and '6.70 s' in text
    assert 'built to refuse anything else; it can slip, so the planner confirms' in text
    assert 'Look inside' not in text
    reading = view['readings'][0]
    assert page['posts'][0]['body'] == {'reading': reading['id'], 'sentence_sha256': reading['sentence_sha256'], 'rule_sha256': reading['rule_sha256']}
    assert events(page, 'rule:confirmed') == confirmed['readings']


def test_instruct_edit_hides_stale_rule_and_confirmation():
    page = render('instruct', 'constraint-ready', [field('[data-live-sentence]', 'Keep bay 8 clear.')])
    assert 'Confirm this reading' not in words(page)
    assert not events(page, 'rule:confirmed')
    assert events(page, 'rule:changed')


def test_instruct_server_must_record_exact_confirmation():
    page = render('instruct', 'constraint-ready', [click('confirm')])
    assert not events(page, 'rule:confirmed')
    assert 'The server did not record this confirmation.' in words(page)


def test_instruct_restores_existing_confirmation_once_and_preserves_local_edit():
    view = sample('constraint-ready'); view['readings'][0]['confirmed'] = True
    page = render('instruct', view=view, steps=[field('[data-live-sentence]', 'Keep bay 8 clear.'), {'call':view}])
    assert len(events(page, 'rule:confirmed')) == 1
    assert events(page, 'rule:changed')[-1] == {'sentence':'Keep bay 8 clear.'}
    assert 'Confirm this reading' not in words(page)


def test_instruct_refusal_cannot_be_confirmed():
    page = render('instruct', 'refused')
    assert 'StowLM Reader refused to make a rule: This sentence is outside the four supported instruction kinds. Nothing was planned from this sentence.' in words(page)
    assert not any(c.get('action') == 'confirm' for c in page['controls'])
    assert not events(page, 'rule:confirmed')


def test_instruct_reader_unavailable_and_no_instruction_choice():
    page = render('instruct', 'reader-unavailable', [field('[data-live-sentence]', 'Keep bay 7 clear.'), click('read')])
    assert "StowLM Reader unavailable: mlx_lm isn't installed in this environment. Nothing was replayed in its place." in words(page)
    assert control(page, 'read')['disabled'] and not page['posts']
    page = render('instruct', 'reader-unavailable', [click('no-instruction')])
    assert events(page, 'rule:confirmed') == [None]
    assert 'No instruction: the reader had nothing to do' in page['statuses']


def test_instruct_look_inside_only_uses_received_live_trace():
    view = sample('constraint-ready'); view['readings'][0]['trace'] = {'tokens': ['<synthetic-token>']}
    page = render('instruct', view=view, steps=[click('inside')])
    assert 'Look inside' in words(page)
    assert '&lt;synthetic-token&gt;' in page['sheets'][0]['html']
    assert 'Only evidence carried by this reading.' in page['sheets'][0]['html']


def wav_file(seconds=0.1):
    file = io.BytesIO()
    with wave.open(file, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
        wav.writeframes(b'\0\0' * int(seconds * 16000))
    return {'name': 'synthetic.wav', 'type': 'audio/wav', 'bytes': list(file.getvalue()), 'size': len(file.getvalue())}


def test_instruct_uploaded_audio_posts_origin_and_received_transcript():
    file = wav_file()
    page = render('instruct', steps=[{'selector': '[data-live-audio]', 'files': [file], 'event': 'change'},
                                    click('transcribe', sample('voice-transcript'))])
    assert 'Uploaded audio' in words(page)
    assert page['posts'][0]['path'].endswith('/audio')
    body = page['posts'][0]['body']
    assert body['origin'] == 'upload' and body['media_type'] == 'audio/wav' and body['data_b64'].startswith('UklGR')
    assert 'Editable transcript' in words(page)


def test_instruct_voice_unavailable_never_posts_audio():
    view = sample('ready'); view['availability']['voice'] = {'available':False, 'reason':"MODEL_API_KEY isn't set in this launch"}
    page = render('instruct', view=view, steps=[{'selector':'[data-live-audio]', 'files':[wav_file()], 'event':'change'}, click('transcribe')])
    assert "Muse Voice Transcribe unavailable: MODEL_API_KEY isn't set in this launch. Nothing was replayed in its place." in words(page)
    assert not page['posts']


def test_instruct_working_voice_streams_no_invented_transcript():
    view = sample('ready'); view['stages'] = [{'stage':'voice','status':'running'}]
    page = render('instruct', view=view)
    assert 'Muse Voice Transcribe is working. No answer has been received yet.' in words(page)
    assert not any(c['value'] for c in page['controls'] if c['tag'] == 'TEXTAREA')
    assert control(page, 'read')['disabled']
    assert not events(page, 'rule:read')


def test_instruct_invalid_audio_does_not_transcribe():
    page = render('instruct', steps=[{'selector': '[data-live-audio]', 'files': [{'bytes':[1,2,3]}], 'event': 'change'}, click('transcribe')])
    assert 'Choose a WAV audio file.' in words(page) and not page['posts']


@pytest.mark.parametrize('sample_count,seconds', [(4800, 0.1), (48000 * 61, 60)])
def test_instruct_browser_recording_encodes_pcm16_mono_16khz_and_caps_duration(sample_count, seconds):
    page = render('instruct', browser_audio=True, steps=[click('record'), {'audio_samples': sample_count},
                  click('stop-recording'), click('transcribe')])
    assert 'Recorded in this browser' in words(page)
    body = page['posts'][0]['body']
    assert body['origin'] == 'browser' and body['name'] == 'browser-recording.wav'
    with wave.open(io.BytesIO(base64.b64decode(body['data_b64'])), 'rb') as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        assert wav.getnframes() == int(16000 * seconds)
    assert page['audioState']['stopped'] == 1 and page['audioState']['closed'] == 1


def test_instruct_recording_without_browser_support_offers_honest_alternatives():
    page = render('instruct', steps=[click('record')])
    assert 'Browser recording is unavailable here. Upload a WAV or type an instruction.' in words(page)
    assert not page['posts']


def test_instruct_changing_call_stops_microphone_and_discards_recorded_audio():
    next_call = sample('ready'); next_call['id'] = 'c20261103-151501-abcdef'
    page = render('instruct', browser_audio=True, steps=[click('record'), {'audio_samples':4800}, {'call':next_call}, click('transcribe')])
    assert page['audioState']['stopped'] == 1
    assert page['audioState']['closed'] == 1 and not page['posts']


def test_instruct_new_call_resets_sentence_and_reading():
    next_call = sample('ready'); next_call['id'] = 'c20261103-151501-abcdef'
    page = render('instruct', 'constraint-ready', [{'call': next_call}])
    assert 'Nothing loads into bay 07.' not in words(page)
    assert control(page, 'read')['disabled']


# Round-2 instruction review.
def test_instruct_invalid_answer_is_not_reported_as_a_model_refusal():
    view=sample('constraint-ready');view['readings'][0].update(status='invalid',refusal=None,rule_text=None)
    page=render('instruct',view=view)
    assert "The StowLM Reader's answer didn't validate against the rule schema, so no rule was made. Nothing was planned from this sentence." in words(page)
    assert 'refused to make a rule' not in words(page)
    assert 'Not a valid rule' in words(page)
    assert not any(c.get('action')=='confirm' for c in page['controls'])


@pytest.mark.parametrize('name,label',[('constraint-ready','Ready to confirm'),('refused','Refused')])
def test_instruct_reading_badges_are_plain_words(name,label):
    page=render('instruct',name)
    assert re.search(rf'class="pl-instruct-badge"[^>]*>{re.escape(label)}<',page['html'])
    assert '>constraint_ready<' not in page['html']


def test_instruct_editing_typed_reading_remains_typed():
    page=render('instruct','constraint-ready',[field('[data-live-sentence]','Keep bay 8 clear.'),click('read')])
    assert page['posts'][0]['body']['origin']=='typed'


def test_instruct_four_supported_kinds_are_named_exactly():
    page=render('instruct')
    assert "The StowLM Reader reads four kinds of instruction: close a bay, cap the tiers, keep dangerous goods apart, one port per hatch. It's built to refuse anything else; it can slip, so the planner confirms." in words(page)


def test_instruct_non_adopted_adapter_has_its_own_identity():
    view=sample('constraint-ready');view['availability']['reader'].update(adopted=False,adapter_sha256='e'*64)
    page=render('instruct',view=view)
    assert f"Read by: a Muse Glimmer 30B LoRA adapter that is not the adopted StowLM Reader (adapter {'e'*64})" in words(page)
    assert 'StowLM Reader · fine-tuned Muse Glimmer 30B' not in words(page)


@pytest.mark.parametrize('size',['tiny','small'])
def test_instruct_confirmed_small_section_bay_closure_explains_section_artifact(size):
    view=sample('constraint-ready');view['case']['size_class']=size
    before=render('instruct',view=view)
    assert 'an artefact of the section size' not in words(before)
    view['readings'][0]['confirmed']=True
    page=render('instruct',view=view)
    assert 'On tiny and small sections a bay closure usually leaves no room for the load: an artefact of the section size, not a crane limit.' in words(page)


def test_instruct_confirmed_medium_bay_closure_does_not_get_small_section_hint():
    view=sample('constraint-ready');view['case']['size_class']='medium';view['readings'][0]['confirmed']=True
    assert 'an artefact of the section size' not in words(render('instruct',view=view))


def test_instruct_generated_voice_is_a_planner_set_upload_property():
    transcript=sample('voice-transcript');transcript['voice'].update(origin='upload',generated_voice=True)
    page=render('instruct',steps=[{'selector':'[data-live-audio]','files':[wav_file()],'event':'change'},
        {'selector':'[data-live-generated-voice]','checked':True,'event':'change'},click('transcribe',transcript)])
    assert page['posts'][0]['body']['origin']=='upload'
    assert page['posts'][0]['body']['generated_voice'] is True
    assert 'Generated voice' in words(page) and 'Uploaded audio' in words(page)


def test_instruct_generated_voice_label_is_restored_from_call_view():
    view=sample('voice-transcript');view['voice'].update(origin='upload',generated_voice=True)
    page=render('instruct',view=view)
    assert 'Generated voice' in words(page)
    assert not page['posts']


def test_instruct_browser_recordings_do_not_claim_generated_voice():
    page=render('instruct',browser_audio=True,steps=[click('record'),{'audio_samples':4800},click('stop-recording'),click('transcribe')])
    assert page['posts'][0]['body']['generated_voice'] is False
    assert 'Recorded in this browser' in words(page)


def finished_instruction_call(attempt=1):
    view=sample('constraint-ready')
    view['state']='done'
    view['readings'][0]['confirmed']=True
    view['run']={'status':'done','attempt':attempt,'instruction':'confirmed'}
    return view


def instruction_event(name,view,**payload):
    return {'emit':{'name':name,'value':{'call':view['id'],'attempt':view['run']['attempt'],**payload}}}


def new_instruction_reading(view,sentence='Keep bay 8 clear.',origin='typed'):
    updated=copy.deepcopy(view)
    reading=copy.deepcopy(view['readings'][0])
    reading.update(id='rnextsynthetic',sentence=sentence,origin=origin,confirmed=False,
                   sentence_sha256='c'*64,rule_sha256='d'*64,rule_text='Nothing loads into bay 08.',
                   answer={'constraint':{'type':'close_bay','bay':8}})
    updated['readings'].append(reading)
    confirmed=copy.deepcopy(updated)
    for item in confirmed['readings']:item['confirmed']=item['id']==reading['id']
    return updated,confirmed


def test_instruct_done_call_can_read_and_confirm_a_new_typed_sentence():
    view=finished_instruction_call();updated,confirmed=new_instruction_reading(view)
    page=render('instruct',view=view,steps=[
        field('[data-live-sentence]','Keep bay 8 clear.'),click('read',updated),click('confirm',confirmed)])
    assert [post['path'].rsplit('/',1)[-1] for post in page['posts']]==['read','confirm']
    assert page['posts'][0]['body']=={'sentence':'Keep bay 8 clear.','origin':'typed'}
    assert page['posts'][1]['body']=={'reading':'rnextsynthetic','sentence_sha256':'c'*64,'rule_sha256':'d'*64}
    assert events(page,'rule:changed')
    assert events(page,'rule:confirmed')[-1]['id']=='rnextsynthetic'


def test_instruct_run_finished_adopts_latest_call_and_unlocks_without_reconfirming_old_reading():
    view=finished_instruction_call();planning=copy.deepcopy(view)
    planning['state']='planning';planning['run']['status']='running'
    page=render('instruct',view=planning,steps=[{'call_only':view},instruction_event('run:finished',view,instruction='confirmed')])
    assert not control(page,'read')['disabled']
    assert not control(page,'no-instruction')['disabled']
    assert len(events(page,'rule:confirmed'))==1  # initial restored confirmation only
    assert not page['posts']
    assert 'Read the cargo into a load list before reading an instruction.' not in words(page)


def test_instruct_choose_new_instruction_clears_old_proposal_and_survives_same_call_poll():
    view=finished_instruction_call()
    page=render('instruct',view=view,steps=[instruction_event('plan:again',view,choose='instruction'),{'call':view}])
    assert any(c['tag']=='TEXTAREA' and c['value']=='' for c in page['controls'])
    assert 'Nothing loads into bay 07.' not in words(page)
    assert events(page,'rule:changed')
    assert len(events(page,'rule:confirmed'))==1
    assert not control(page,'no-instruction')['disabled'] and not page['posts']


def test_instruct_done_call_can_choose_no_instruction_again_after_run_finished():
    view=finished_instruction_call();view['readings']=[];view['run']['instruction']='none'
    page=render('instruct',view=view,steps=[click('no-instruction'),instruction_event('run:finished',view,instruction='none'),
        instruction_event('plan:again',view,choose='instruction'),click('no-instruction')])
    assert events(page,'rule:confirmed')==[None,None]
    assert events(page,'rule:changed')
    assert page['statuses'][-1]=='No instruction: the reader had nothing to do'
    assert not page['posts']


def test_instruct_done_call_voice_path_transcribes_reads_and_confirms():
    view=finished_instruction_call()
    transcript=copy.deepcopy(view)
    transcript['voice']={'transcript':'Keep bay 8 clear.','origin':'upload','audio_sha256':'f'*64,'generated_voice':False}
    updated,confirmed=new_instruction_reading(transcript,origin='voice')
    page=render('instruct',view=view,steps=[instruction_event('plan:again',view,choose='instruction'),
        {'selector':'[data-live-audio]','files':[wav_file()],'event':'change'},click('transcribe',transcript),
        click('read',updated),click('confirm',confirmed)])
    assert [post['path'].rsplit('/',1)[-1] for post in page['posts']]==['audio','read','confirm']
    assert page['posts'][1]['body']=={'sentence':'Keep bay 8 clear.','origin':'voice'}
    assert events(page,'rule:confirmed')[-1]['id']=='rnextsynthetic'


def test_instruct_done_call_browser_recording_stays_available():
    view=finished_instruction_call()
    page=render('instruct',view=view,browser_audio=True,steps=[instruction_event('plan:again',view,choose='instruction'),
        click('record'),{'audio_samples':4800},click('stop-recording'),click('transcribe')])
    assert page['posts'][0]['body']['origin']=='browser'
    assert page['audioState']['stopped']==1 and page['audioState']['closed']==1


def test_instruct_attempt_events_from_other_calls_do_not_clear_current_sentence():
    view=finished_instruction_call();other=copy.deepcopy(view);other['id']='c20261103-151501-abcdef'
    page=render('instruct',view=view,steps=[field('[data-live-sentence]','Keep bay 8 clear.'),
        instruction_event('plan:again',other,choose='instruction'),instruction_event('run:finished',other,instruction='none')])
    assert any(c['tag']=='TEXTAREA' and c['value']=='Keep bay 8 clear.' for c in page['controls'])
    assert not page['posts']


def test_instruct_old_attempt_events_do_not_clear_new_attempt_draft():
    view=finished_instruction_call(attempt=2);old=finished_instruction_call(attempt=1)
    page=render('instruct',view=view,steps=[field('[data-live-sentence]','Keep bay 8 clear.'),
        instruction_event('plan:again',old,choose='instruction'),instruction_event('run:finished',old,instruction='confirmed')])
    assert any(c['tag']=='TEXTAREA' and c['value']=='Keep bay 8 clear.' for c in page['controls'])
    assert not page['posts']


def test_instruct_run_finished_does_not_unlock_a_running_new_attempt():
    view=finished_instruction_call(attempt=2);view['state']='planning';view['run']['status']='running'
    old=finished_instruction_call(attempt=1)
    page=render('instruct',view=view,steps=[instruction_event('run:finished',old,instruction='confirmed'),
        instruction_event('plan:again',view,choose='instruction'),click('read'),click('no-instruction')])
    assert control(page,'read')['disabled'] and control(page,'no-instruction')['disabled']
    assert not page['posts'] and len(events(page,'rule:confirmed'))==1


def test_instruct_done_call_local_reader_and_no_instruction_do_not_require_paid_consent():
    view=finished_instruction_call()
    page=render('instruct',view=view,status=KEY_ABSENT,steps=[instruction_event('plan:again',view,choose='instruction'),
        field('[data-live-sentence]','Keep bay 8 clear.'),click('read'),click('no-instruction')])
    assert page['posts'][0]['path'].endswith('/read')
    assert events(page,'rule:confirmed')[-1] is None
