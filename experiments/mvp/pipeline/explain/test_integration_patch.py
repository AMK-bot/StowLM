"""Check the pending handoff or its integrated app, without app imports/writes."""
import ast
import builtins
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[4]
PATCH = Path(__file__).with_name('integration.patch')


def apply_patch_to_copies(patch_text, originals):
    """Strict textual unified-patch application with exact context matching."""
    lines = patch_text.splitlines(keepends=True)
    output = {}
    i = 0
    while i < len(lines):
        if not lines[i].startswith('--- a/'):
            raise ValueError('Expected original-file header')
        old_name = lines[i][6:].strip()
        i += 1
        if lines[i] != '+++ b/' + old_name + '\n':
            raise ValueError('Unexpected target file')
        i += 1
        old_lines = originals[old_name].splitlines(keepends=True)
        cursor, result = 0, []
        while i < len(lines) and lines[i].startswith('@@ '):
            match = re.fullmatch(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@.*\n', lines[i])
            if not match:
                raise ValueError('Malformed hunk')
            declared_start = int(match[1]) - 1
            before, after = [], []
            i += 1
            while i < len(lines) and lines[i][:1] in (' ', '+', '-') and not lines[i].startswith('--- a/'):
                line = lines[i]
                if line[0] in (' ', '-'):
                    before.append(line[1:])
                if line[0] in (' ', '+'):
                    after.append(line[1:])
                i += 1
            if len(before) != int(match[2] or 1) or len(after) != int(match[4] or 1):
                raise ValueError('Hunk count mismatch')
            if declared_start >= cursor and old_lines[declared_start:declared_start + len(before)] == before:
                start = declared_start
            else:
                # Other owners may add unrelated lines above the handoff. Like
                # git apply, accept an offset only with exact unchanged context.
                matches = [at for at in range(cursor, len(old_lines) - len(before) + 1)
                           if old_lines[at:at + len(before)] == before]
                if len(matches) != 1:
                    raise ValueError(f'Patch context changed or ambiguous: {old_name}:{declared_start + 1}')
                start = matches[0]
            result.extend(old_lines[cursor:start])
            result.extend(after)
            cursor = start + len(before)
        result.extend(old_lines[cursor:])
        output[old_name] = ''.join(result)
    return output


def app_sources_for_checks(patch, originals):
    # The owner can refactor an applied handoff, so reverse-patch context is
    # insufficient. Check its entry points here and its behavior below.
    integrated = {
        'app/v6.py': 'query.get("slot", [None])[0]',
        'app/v6_sandbox.py': 'target_slot: str | None = None',
        'app/v6_sandbox_worker.py': 'explain_move',
        'app/static/stage-v6breakit.js': 'whyTarget',
    }
    if all(marker in originals.get(name, '') for name, marker in integrated.items()):
        return dict(originals)
    return apply_patch_to_copies(patch, originals)


@pytest.fixture
def patched():
    patch = PATCH.read_text()
    names = re.findall(r'^--- a/(.+)$', patch, re.M)
    originals = {name: (ROOT / name).read_text() for name in names}
    return app_sources_for_checks(patch, originals)


def test_patch_offsets_require_exact_unique_context():
    patch = '--- a/sample.py\n+++ b/sample.py\n@@ -1,2 +1,2 @@\n a\n-b\n+c\n'
    assert apply_patch_to_copies(patch, {'sample.py': '# unrelated owner change\na\nb\n'}) == {
        'sample.py': '# unrelated owner change\na\nc\n'}
    with pytest.raises(ValueError, match='changed or ambiguous'):
        apply_patch_to_copies(patch, {'sample.py': '# changed context\na\nd\n'})


def test_integrated_app_is_checked_without_reapplying_handoff():
    sources = {
        'app/v6.py': 'query.get("slot", [None])[0]',
        'app/v6_sandbox.py': 'target_slot: str | None = None',
        'app/v6_sandbox_worker.py': 'explain_move',
        'app/static/stage-v6breakit.js': 'whyTarget',
    }
    assert app_sources_for_checks('', sources) == sources
    sources.pop('app/v6_sandbox_worker.py')
    with pytest.raises(ValueError, match='changed or ambiguous'):
        app_sources_for_checks('--- a/app/v6.py\n+++ b/app/v6.py\n@@ -1 +1 @@\n-old\n+new\n', sources)


def _functions(source, names, namespace):
    tree = ast.parse(source)
    selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert len(selected) == len(names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), '<patched-app-functions>', 'exec'), namespace)
    return namespace


def test_patch_applies_current_app_and_python_compiles_without_app_writes(patched):
    assert set(patched) == {'app/v6.py', 'app/v6_sandbox.py', 'app/v6_sandbox_worker.py',
                            'app/static/stage-v6breakit.js'}
    for name, source in patched.items():
        if name.endswith('.py'):
            compile(source, name, 'exec')


def test_patched_javascript_parses_with_node_only(patched, tmp_path):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is not installed; Python patch checks still run')
    file = tmp_path / 'stage-v6breakit.js'
    file.write_text(patched['app/static/stage-v6breakit.js'])
    result = subprocess.run([node, '--check', str(file)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_target_poll_window_progress_and_error_remain_visible(patched):
    js = patched['app/static/stage-v6breakit.js']
    assert 'target ? 240 : 80' in js
    assert 'target ? 750 : 250' in js
    assert 'full check can take a few minutes' in js and 'why-progress' in js
    assert 'still running' in js
    assert 'data-explain-slot' in js and 'whyTarget' in js
    assert 'Move to ${esc(a.target_slot)} checked by sandbox' not in js


def test_adapter_preserves_full_evidence_refs_and_truthful_move_status(patched):
    env = _functions(patched['app/v6_sandbox.py'], {'explain_view', '_brief'},
                     {'json': json, 'TEMPLATE_LABEL': 'Template facts', 'THAI_NOTE': 'No native review',
                      '_dict': lambda value: value if isinstance(value, dict) else {},
                      'checked_on': lambda value: None, '_NOTES': {},
                      'SPARK_SUMMARY': 'Spark summary', 'RECORDED_LABEL': 'Recorded answer'})
    digest = 'a' * 64
    link = {'source': 'sandbox', 'path': '', 'sha256': digest, 'href': f'sandbox:{digest}#',
            'value': {'screen': 'full', 'legal': False, 'gauges': {'input_error': True}}}
    render_checks = {'thai_sense': {'status': 'flagged', 'issues': [{'id': 'where', 'reason': 'Awkward'}]},
                     'prior_language_checks': [{'status': 'flagged', 'issues': [{'id': 'weight', 'reason': 'Earlier flag'}]}]}
    explanation = {'mode': 'live', 'text': 'Template facts\nInvalid request, not checked',
                   'statements': [{'id': 'what_if', 'text': 'Not checked', 'evidence': [link],
                                   'render_checks': render_checks}]}
    view = env['explain_view']('BOX01', 'th', explanation)
    evidence = view['statements'][0]['evidence'][0]
    assert evidence['href'] == link['href'] and evidence['sha256'] == digest
    assert evidence['value_json'] == link['value']
    assert view['statements'][0]['render_checks'] == render_checks
    assert view['move_check'] == 'not_checked'
    assert view['label'] == 'Template facts'
    explanation['statements'][0]['evidence'][0]['value']['gauges'] = {}
    assert env['explain_view']('BOX01', 'en', explanation)['move_check'] == 'full'
    explanation['statements'][0]['evidence'][0]['value']['screen'] = 'fast'
    assert env['explain_view']('BOX01', 'en', explanation)['move_check'] == 'fast'
    explanation['statements'].insert(0, {'id': 'summary', 'kind': 'model_summary',
        'model_label': 'Recorded Spark summary', 'text': 'Model framing', 'evidence': []})
    assert env['explain_view']('BOX01', 'en', explanation)['label'] == 'Recorded Spark summary'
    js = patched['app/static/stage-v6breakit.js']
    assert 'data-evidence-link' in js and 'e.href' in js and 'e.sha256' in js
    assert 'value_json' in js and 'preventDefault()' in js


def test_language_feedback_renders_current_and_prior_flags_as_escaped_checks(patched):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is not installed')
    js = patched['app/static/stage-v6breakit.js']
    start = js.index('      function languageFeedback(')
    stop = js.index('      function showWhy(a) {', start)
    helper = js[start:stop]
    argument = '{lang: "th", statements: rows}' if 'function languageFeedback(a)' in js else 'rows'
    script = ("const fs = require('node:fs'); const rows = JSON.parse(fs.readFileSync(0, 'utf8'));\n"
              "const esc = (value) => String(value == null ? '' : value).replace(/[&<>\"']/g, "
              "(c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','\\\"':'&quot;',\"'\":'&#39;'}[c]));\n"
              + helper + '\nprocess.stdout.write(languageFeedback(' + argument + '));')
    history_hash = 'b' * 64
    rows = [{'render_checks': {'thai_sense': {'status': 'flagged', 'issues': [
        {'id': 'summary', 'reason': '<script>untrusted wording</script>'}]},
        'prior_language_checks': [{'status': 'flagged', 'issues': [
            {'id': 'weight', 'reason': 'ลงน้ำหนัก retained wording flag'}],
            'previous_payload_sha256': history_hash}]}}]
    result = subprocess.run([node, '-e', script], input=json.dumps(rows), capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'Thai sense check: flagged' in result.stdout
    assert 'model language feedback, not proof' in result.stdout
    if argument == 'rows':
        assert 'No native Thai reader has reviewed this text' in result.stdout
    else:
        # The integrated view renders the server's warning beside the facts.
        assert 'a.thai_note' in js
        assert 'No native Thai reader has reviewed' in patched['app/v6_sandbox.py']
    assert 'Prior check: flagged' in result.stdout and history_hash in result.stdout
    assert 'ลงน้ำหนัก retained wording flag' in result.stdout
    assert '&lt;script&gt;untrusted wording&lt;/script&gt;' in result.stdout
    assert '<script>' not in result.stdout
    rows[0]['render_checks']['thai_sense'] = {'status': 'not_run', 'issues': []}
    missing = subprocess.run([node, '-e', script], input=json.dumps(rows), capture_output=True,
                             text=True, timeout=15)
    assert missing.returncode == 0, missing.stderr
    assert 'No completed model language check is available for this view' in missing.stdout


def test_worker_reuses_actual_move_verdict_and_forwards_new_what_if(patched):
    calls = []
    class Value:
        def __init__(self, value):
            self.value = value
        def model_dump(self, **kwargs):
            return self.value
    verdict = Value({'screen': 'full', 'legal': False, 'gauges': {'input_error': True}})
    def try_move(instance, plan, box, slot):
        calls.append(('move', box, slot))
        return verdict
    def explain_move(instance, plan, report, box, slot, previous, **kwargs):
        assert previous is verdict
        calls.append(('reuse', box, slot))
        return Value({'statements': [], 'mode': 'live'})
    def explain_box(instance, plan, report, box, **kwargs):
        calls.append(('explain', box, kwargs.get('what_if')))
        return Value({'statements': [], 'mode': 'live'})
    sandbox_module = SimpleNamespace(
        try_move=try_move, fast_screen=lambda *a: pytest.fail('No fast lane expected'),
        explain_verdict=lambda v: Value({'statements': []}))
    # Isolate this extracted worker's imports rather than mutating namespace
    # package attributes shared by every test collected in the folder.
    modules = {'experiments.mvp.pipeline': SimpleNamespace(sandbox=sandbox_module),
               'experiments.mvp.pipeline.explain': SimpleNamespace(explain_box=explain_box, explain_move=explain_move),
               'experiments.mvp.pipeline.explain.api': SimpleNamespace(explain_move=explain_move)}
    def worker_import(name, globals=None, locals=None, fromlist=(), level=0):
        assert level == 0 and name in modules, f'Unexpected worker import: {name}'
        return modules[name]
    worker = patched['app/v6_sandbox_worker.py']
    names = {'answer', 'explained'} if 'def explained(' in worker else {'answer'}
    env = _functions(worker, names,
                     {'time': SimpleNamespace(perf_counter=lambda: 0.),
                      'recorded_move': lambda *args: None,
                      '__builtins__': {**vars(builtins), '__import__': worker_import}})
    world = {'instance': {}, 'plan': {}, 'report': {}}
    answer = env['answer']
    answer(world, 'full', {'op': 'try', 'box': 'BOX01', 'slot': 'bad-slot'})
    answer(world, 'full', {'op': 'explain', 'box': 'BOX01', 'lang': 'th', 'slot': 'bad-slot'})
    answer(world, 'full', {'op': 'explain', 'box': 'BOX01', 'lang': 'th', 'slot': '0030182'})
    assert calls == [('move', 'BOX01', 'bad-slot'), ('reuse', 'BOX01', 'bad-slot'),
                     ('explain', 'BOX01', '0030182')]


def test_worker_inputs_prefers_bound_source_report_and_preserves_legacy_fallback(patched, tmp_path):
    class InvalidReport(Exception):
        pass
    class Model:
        @staticmethod
        def model_validate(value):
            if value is None:
                raise InvalidReport()
            return value
    legacy = {'baseline_report': {'origin': 'legacy', 'valid': True}}
    matching = []
    def matches(report, instance, plan):
        matching.append((report, instance, plan))
        return report['valid']
    modules = {
        'pydantic': SimpleNamespace(ValidationError=InvalidReport),
        'app.v6_sandbox': SimpleNamespace(read_demo=lambda: legacy, first_case=lambda demo: None,
                                        source_path=lambda demo: tmp_path / 'source.json'),
        'stowage.model': SimpleNamespace(CheckReport=Model, Instance=Model, MovePlan=Model),
        'stowage.checker': SimpleNamespace(report_matches_inputs=matches)}
    def worker_import(name, globals=None, locals=None, fromlist=(), level=0):
        assert level == 0 and name in modules, f'Unexpected worker import: {name}'
        return modules[name]
    (tmp_path / 'v6_hero.json').write_text(json.dumps({'sandbox_source': 'source.json'}))
    source = {'instance': {'id': 'hero'}, 'plan': {'id': 'hero-plan'},
              'report': {'origin': 'combined-hero', 'valid': True}}
    def write_source():
        (tmp_path / 'source.json').write_text(json.dumps(source))
    write_source()
    env = _functions(patched['app/v6_sandbox_worker.py'], {'inputs'}, {
        'Path': Path, 'json': json, 'ROOT': tmp_path, '__file__': str(tmp_path / 'worker.py'),
        '__builtins__': {**vars(builtins), '__import__': worker_import}})
    assert env['inputs']()['report'] == source['report']
    assert matching[-1] == (source['report'], source['instance'], source['plan'])
    source.pop('report')
    write_source()
    assert env['inputs']()['report'] == legacy['baseline_report']
    source['report'] = None
    write_source()
    assert env['inputs']()['report'] is None  # explicit missing evidence never borrows the old report
    source['report'] = {'origin': 'wrong-plan', 'valid': False}
    write_source()
    try:
        invalid = env['inputs']()
    except ValueError as error:
        assert 'does not match' in str(error)
    else:
        assert invalid['report'] is None  # integrated worker disables explanations, retains drags
    assert matching[-1] == (source['report'], source['instance'], source['plan'])


def test_handoff_is_hash_bound_and_does_not_claim_ui_is_integrated():
    from experiments.mvp.pipeline.contract.schema import sha256_json
    value = json.loads(Path(__file__).with_name('app_handoff.json').read_text())
    digest = value.pop('sha256')
    assert digest == sha256_json(value)
    assert value['app_already_uses_this_hero'] is False
    assert value['status'] == 'awaiting_app_owner_application_and_hero_selection'
    assert value['recording_snapshot']['recheck_before_apply'] is True
    patch = value['handoff_artifacts']['integration_patch']
    assert patch['sha256'] == hashlib.sha256((ROOT / patch['path']).read_bytes()).hexdigest()
    combined = value['handoff_artifacts']['combined_hero_inputs']
    assert combined['sha256'] == hashlib.sha256((ROOT / combined['path']).read_bytes()).hexdigest()
    step = next(s for s in value['owner_steps'] if s['step'] == 3)
    assert step['sandbox_source'] == combined['path']
    assert step['field_pointers']['sandbox_report'] == '/report'
    assert 'App owner' in value['open_issue']
