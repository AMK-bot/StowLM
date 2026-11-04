"""Actual ToolSet with explicitly injected, cheap development services.

Reader answers and checker responses are fixtures, not model/engine accuracy.
The saved full report binds the unchanged synthetic instance and plan. Changed
plans get no pass from that report. Real native scenarios use separate defaults.
"""
from copy import deepcopy
import json
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

from experiments.mvp.pipeline.contract.schema import sha256_json
from experiments.mvp.reader.schema import validate_answer, context_from_instance
from .tools import ToolSet

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'fixtures' / 'agency-native'


class FixtureReader:
    def __init__(self, answers):
        self.answers = deepcopy(answers)

    def parse(self, sentence, context):
        raw = self.answers.get(sentence, {'schema_version': 'stowlm.reader/2', 'refusal': {
            'refused': True, 'code': 'not_an_instruction', 'reason': 'Fixture has no answer for that exact sentence.'}})
        return validate_answer(json.dumps(raw), context), dict(parser='copilot-injected-reader-fixture',
            adapter_sha256=None, label='Explicit reader fixture; no adopted-reader accuracy claim')


class FixtureJobs:
    def __init__(self, *, pending=False):
        self.jobs, self.next_id, self.pending = {}, 0, pending

    def start(self, operation, *, identity=None, context=None):
        key = f'fixture-job-{self.next_id}'
        self.next_id += 1
        self.jobs[key] = dict(identity=identity, started=monotonic(), operation=operation)
        if not self.pending:
            self.jobs[key]['payload'] = operation()
        return self.poll(key, identity=identity)

    def poll(self, key, *, identity=None):
        if key not in self.jobs:
            return dict(status='not_found', job_id=key, passed=False, text='Fixture job is unavailable.')
        row = self.jobs[key]
        elapsed = round(monotonic()-row['started'], 3)
        return dict(status='running' if self.pending else 'completed', job_id=key,
            elapsed_s=elapsed, elapsed_label='measured', passed=False,
            text=f'Injected service job is {"running" if self.pending else "completed"}. Elapsed {elapsed} seconds.')

    def result(self, key, *, identity=None):
        return self.jobs[key]['payload']

    def cancel(self, key):
        self.jobs.pop(key, None)

    def forget(self, key):
        return self.jobs.pop(key, None) is not None


def source_models():
    from stowage.model import Instance, MovePlan, CheckReport
    return (Instance.model_validate(json.loads((SOURCE/'instance.json').read_text())),
            MovePlan.model_validate(json.loads((SOURCE/'baseline-plan.json').read_text())),
            CheckReport.model_validate(json.loads((SOURCE/'baseline-report.json').read_text())))


def fixture_envelope(kind):
    if kind == 'out_of_envelope':
        return lambda instance: dict(in_envelope=False, reasons=[{'message': 'Injected envelope fixture: this voyage is outside the supported ship envelope.'}], numbers={})
    return None


def make_tools(case):
    """Return real ToolSet plus auditable fixture services and host references."""
    from stowage.checker import report_matches_inputs
    instance, plan, report = source_models()
    setup, kind = case.get('setup', 'plain'), case.get('kind', '')
    sentence = case.get('reader_sentence', case.get('message', ''))
    if not sentence and case.get('turns'):
        sentence = case['turns'][0]['message']
    seed = 'In bay 2, use at most 2 occupied tiers per deck stack.'
    accepted = {'schema_version': 'stowlm.reader/2', 'constraint': {
        'type': 'max_tiers', 'bay': 2, 'zone': 'deck', 'max_tiers': 2}}
    answer = deepcopy(accepted)
    tags = set(case.get('tags', []))
    if kind in ('loosening', 'injection', 'ambiguous', 'unsupported') or 'loosening' in tags:
        code = 'loosening' if kind == 'loosening' or 'loosening' in tags else ('ambiguous' if kind == 'ambiguous' else 'unsupported')
        answer = {'schema_version': 'stowlm.reader/2', 'refusal': {
            'refused': True, 'code': code, 'reason': 'Fixture refusal reason is never display authority.'}}
    elif kind == 'impossible_bay' or 'impossible_arithmetic' in tags:
        answer = {'schema_version': 'stowlm.reader/2', 'constraint': {'type': 'close_bay', 'bay': 2}}
    elif kind == 'impossible_tiers' or setup == 'impossible_tier_with_checked_alternatives':
        answer = {'schema_version': 'stowlm.reader/2', 'constraint': {
            'type': 'max_tiers', 'bay': None, 'zone': 'deck', 'max_tiers': 0}}
    answers = {sentence: answer, seed: accepted}
    for step in case.get('turns', []):
        answers.setdefault(step['message'], answer)
    calls = []
    failed = setup == 'fast_screen_plan' or kind in ('unchecked', 'fast_screen', 'failed_check')
    def checker(current_instance, current_plan):
        calls.append(dict(service='injected_checker', input_sha256=sha256_json(current_plan.model_dump(mode='json') if hasattr(current_plan,'model_dump') else current_plan)))
        if not failed and report_matches_inputs(report, current_instance, current_plan):
            return report.model_copy(deep=True)
        return {'passed': False, 'count': None, 'violations': [],
                'fixture_label': 'Injected checker failure; no unchanged saved report can certify this candidate.'}
    def lattice(instance_hash, rules):
        calls.append(dict(service='injected_lattice', rules=deepcopy(rules)))
        if setup == 'impossible_instruction_no_checked_alternatives':
            return None
        return dict(plan=plan.model_dump(mode='json'), computed_utc='2026-11-02T00:13:35+00:00',
                    label='Injected stored candidate; unchanged saved native-report fixture only')
    def sandbox(*args):
        calls.append(dict(service='injected_sandbox'))
        return dict(legal=False, screen='fast', broken=[], extra_lifts=None,
                    fixture_label='Injected fast-only sandbox service; no full verdict or plan acceptance')
    jobs = FixtureJobs(pending=setup == 'running_job')
    toolset = ToolSet(instance, parser=FixtureReader(answers), plan=plan,
        checker=checker, lattice=lattice, sandbox=sandbox, job_manager=jobs,
        envelope=fixture_envelope('out_of_envelope' if setup=='out_of_envelope' or kind=='out_of_envelope' else ''),
        replanner=lambda *args, **kw: (SimpleNamespace(plan=plan), {'status':'checked_plan', 'passed':True}),
        ladder=lambda *args, **kw: (SimpleNamespace(plan=plan), {'status':'relaxed_plan', 'passed':False}))
    if setup in ('confirmed_reading','unconfirmed_reading','running_job'):
        read = toolset.call('read_instruction', {'sentence': seed})
        rid = read['proposed_reading']['reading_id']
        if setup != 'unconfirmed_reading':
            toolset.confirm_reading(rid)
        if setup == 'running_job':
            toolset.call('replan', {'reading_id': rid})
    if setup.startswith('impossible_'):
        text = 'Use zero occupied deck tiers.' if setup == 'impossible_tier_with_checked_alternatives' else 'Keep bay 2 clear.'
        raw = ({'schema_version':'stowlm.reader/2','constraint': {'type':'max_tiers','bay':None,'zone':'deck','max_tiers':0}}
               if setup == 'impossible_tier_with_checked_alternatives' else
               {'schema_version':'stowlm.reader/2','constraint':{'type':'close_bay','bay':2}})
        toolset.parser.answers[text] = raw
        toolset.call('read_instruction', {'sentence': text})
    if case.get('document'):
        original_context = toolset.context
        def context_with_document():
            return dict(original_context(), document={'label': 'Synthetic document fixture', 'text': case['document']})
        toolset.context = context_with_document
    context = toolset.context()
    references = dict(rules=context['confirmed_rules'])
    if setup != 'no_selection':
        references['box'] = 'QZXU0000009'
    if setup != 'no_selection' and setup != 'selected_box':
        references['slot'] = '0030482'
    rid = context.get('confirmed_reading_id') or context.get('pending_reading_id')
    if rid:
        references['reading_id'] = rid
    if context.get('active_job_id'):
        references['job_id'] = context['active_job_id']
    return toolset, dict(services=calls, references=references,
        label='Real ToolSet control flow with injected reader/checker/lattice/sandbox/job fixtures; not engine or adopted-reader accuracy.')
