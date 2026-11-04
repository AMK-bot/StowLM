"""Explicit real-binding background probe, with a SCRIPTED model and reader fixture.

Run in its own process: python -m experiments.mvp.pipeline.copilot.binding_background_probe
No Spark call is made. This is a stage ablation, not production search or a
passing-plan demonstration. Only a newly completed full certificate can pass.
The observation deadline records an interruption; an indivisible native call
can continue afterwards. Cache paths remain scoped until that worker finishes.
The host may stop only this process's recorded PID if native work remains stuck.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict, fields
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from threading import Event
from time import monotonic

from experiments.mvp.pipeline.contract.schema import sha256_file
from .agency_fixtures import FixtureReader
from .agent import Copilot
from .authorization import ActionGrant, TurnRequest
from .native_runtime import native_caches
from .tools import ToolSet

HERE = Path(__file__).resolve().parent
SENTENCE = 'Across the vessel, use at most 2 occupied tiers per deck stack.'
RULE = {'schema_version': 'stowlm.reader/2', 'constraint': {
    'type': 'max_tiers', 'bay': None, 'zone': 'deck', 'max_tiers': 2}}


class ScriptedModel:
    mode = 'recorded'

    def select(self, tool, arguments):
        self.action = {'tool': tool, 'arguments': deepcopy(arguments)}

    def complete(self, prompt):
        if self.action is not None:
            action, self.action = self.action, None
            return json.dumps(action)
        return json.dumps({'say': 'The tool evidence is recorded for planner review.'})


def make_tools():
    """Defaults are real native services; only language interpretation is a fixture."""
    os.environ.setdefault('STOWAGE_VESSEL_CACHE_DIR', str(HERE / 'cache' / 'binding-background-probe'))
    from engine.config import Config
    from stowage.model import Instance, MovePlan
    source = HERE / 'fixtures' / 'agency-native'
    instance_path, plan_path = source / 'instance.json', source / 'baseline-plan.json'
    instance = Instance.model_validate(json.loads(instance_path.read_text()))
    plan = MovePlan.model_validate(json.loads(plan_path.read_text()))
    if len(instance.load_list) != 13 or plan.instance_id != instance.id:
        raise ValueError('Expected the prepared synthetic thirteen-box baseline')
    # Waive every optional stage explicitly. In particular, bound=True would
    # retain its own large floor even with starts/search/finish disabled.
    defaults = Config()
    config = Config(**{field.name: False for field in fields(defaults)
                       if isinstance(getattr(defaults, field.name), bool)})
    tools = ToolSet(instance, plan=plan, parser=FixtureReader({SENTENCE: RULE}),
                    planning_options=dict(budget_s=1., finalize_s=0., seed=0, config=config))
    return tools, dict(instance_file=str(instance_path.relative_to(HERE)),
                       instance_sha256=sha256_file(instance_path),
                       baseline_file=str(plan_path.relative_to(HERE)),
                       baseline_sha256=sha256_file(plan_path),
                       load_boxes=len(instance.load_list), engine_config=asdict(config))


def _write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def run_probe(*, out_dir=None, deadline_s=900., poll_interval_s=15., session_label=None,
              tools=None, source=None, cache_scope=native_caches, clock=monotonic,
              pause=None, announce=print):
    """Record responsive turns and real worker progress; never make a paid call.

    ``tools/cache_scope/clock/pause`` are host-only, cheap offline test seams.
    The deadline bounds observation, not native work. After it, publication is
    cancelled and this process waits without restoring native cache globals.
    """
    for name, value in (('deadline_s', deadline_s), ('poll_interval_s', poll_interval_s)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(name + ' must be positive and finite')
    if poll_interval_s > 45:
        raise ValueError('Progress waits must not exceed forty-five seconds')
    destination = Path(out_dir or HERE / 'fixtures' / 'binding-background-probe')
    destination.mkdir(parents=True, exist_ok=True)
    result_path, marker_path = destination / 'result.json', destination / 'started.json'
    started = clock()
    marker = dict(pid=os.getpid(), session_label=session_label,
                  started_utc=datetime.now(timezone.utc).isoformat(),
                  observation_deadline_s=deadline_s,
                  model='scripted JSON actions; no Spark decisions or paid calls',
                  reader='Exact-sentence FixtureReader; not the adopted fine-tuned reader')
    with marker_path.open('x') as out:
        json.dump(marker, out, indent=2)
    record = dict(schema_version='stowlm.copilot-binding-background-probe/1',
                  label='Scripted conversation; real binding stage ablation, not production search.',
                  **marker, status='started', turns=[], progress=[], accepted_plan=False,
                  measured_model_cost_usd='0', native_completion_proved=False)
    model = ScriptedModel()
    wait = pause or Event().wait
    job_id = None

    def save():
        record['wall_s'] = max(0., clock() - started)
        _write(result_path, record)

    def say(value):
        announce(json.dumps(value, sort_keys=True))

    def turn(tool, arguments, message):
        model.select(tool, arguments)
        before = clock()
        answer = copilot.turn(message, request=TurnRequest(
            selected_sentence=SENTENCE if tool == 'read_instruction' else None,
            actions=(ActionGrant(tool, deepcopy(arguments)),)))
        row = dict(message=message, wall_s=max(0., clock() - before),
                   turn=answer.model_dump(mode='json'), trace=deepcopy(copilot.last_trace))
        record['turns'].append(row)
        save()
        say(dict(event='conversation_returned', tool=tool, wall_s=row['wall_s'],
                 accepted_plan=answer.plan_sha256 is not None))
        return answer

    def interrupt(reason):
        record.update(status='interrupted', interruption_reason=reason,
                      accepted_plan=False, native_completion_proved=False)
        if job_id is not None:
            tools.job_manager.cancel(job_id)
        save()
        say(dict(event='interrupted', reason=reason, pid=os.getpid(),
                 note='Native calls are indivisible; cache scope stays active until worker termination.'))

    with cache_scope():
        try:
            if tools is None:
                tools, source = make_tools()
            record['source'] = source or {'label': 'Injected offline test services; no native execution claim'}
            record['native_service_defaults'] = tools.checker is None and tools.replanner is None and tools.job_manager is None
            copilot = Copilot(tools, model, max_steps=2)
            proposal = turn('read_instruction', {'sentence': SENTENCE},
                            'Read the selected instruction and show its proposed interpretation.')
            if proposal.proposed_reading is None:
                record.update(status='reader_not_confirmable')
                save()
                return record
            reading_id = proposal.proposed_reading['reading_id']
            confirmation = tools.confirm_reading(reading_id)
            record['host_confirmation'] = confirmation
            if confirmation.get('planner_confirmed') is not True:
                record.update(status='reader_not_confirmed')
                save()
                return record
            submitted = turn('replan', {'reading_id': reading_id},
                             'The host confirmed the reading. Investigate a replacement through replan.')
            job_id = tools.context().get('active_job_id')
            if job_id is None:
                record.update(status='not_started', submission=submitted.model_dump(mode='json'))
                save()
                return record
            record['job_id'] = job_id
            immediate = turn('wait_replan', {'job_id': job_id}, 'Report the current background replan status.')
            immediate_result = next((call['result'] for call in immediate.tool_calls
                                     if call['name'] == 'wait_replan'), {})
            if immediate_result.get('status') not in ('running', 'queued', 'pending'):
                completed = immediate_result.get('job_status') == 'completed'
                record.update(status='completed' if completed else 'native_error',
                              native_completion_proved=completed,
                              accepted_plan=immediate.plan_sha256 is not None,
                              terminal_result=immediate_result)
                save()
                return record
            while True:
                progress = tools.job_manager.poll(job_id)
                record['progress'].append(progress)
                save()
                say(dict(event='native_progress', status=progress['status'], phase=progress.get('phase'),
                         elapsed_s=progress.get('elapsed_s'), wall_s=record['wall_s']))
                if progress['status'] != 'running':
                    break
                remaining = deadline_s - (clock() - started)
                if remaining <= 0:
                    interrupt('Observation deadline reached without a native terminal result; this is not a proven failure.')
                    break
                wait(min(poll_interval_s, remaining))
            if record['status'] != 'interrupted':
                final = turn('wait_replan', {'job_id': job_id}, 'Inspect the terminal background job evidence.')
                result = next((call['result'] for call in final.tool_calls if call['name'] == 'wait_replan'), {})
                record.update(status='completed' if progress['status'] == 'completed' else 'native_error',
                              native_completion_proved=progress['status'] == 'completed',
                              accepted_plan=final.plan_sha256 is not None, terminal_result=result)
                save()
        except KeyboardInterrupt:
            interrupt('Host interrupted observation; no native completion is claimed.')
        except Exception as error:
            record.update(status='probe_error', error_type=type(error).__name__, accepted_plan=False)
            save()
            raise
        finally:
            # Cancellation marks publication stale but cannot stop native code.
            # Keep the process-global cache scope intact until actual execution
            # ends; the host can stop this recorded process if it remains stuck.
            while job_id is not None and tools.job_manager.poll(job_id).get('native_running'):
                save()
                say(dict(event='awaiting_native_termination', pid=os.getpid(), wall_s=record['wall_s']))
                wait(poll_interval_s)
            save()
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path)
    parser.add_argument('--deadline-s', type=float, default=900.)
    parser.add_argument('--poll-interval-s', type=float, default=15.)
    parser.add_argument('--session-label')
    args = parser.parse_args()
    record = run_probe(**vars(args))
    print(json.dumps({key: record[key] for key in ('status', 'pid', 'wall_s', 'native_completion_proved', 'accepted_plan')}, indent=2))


if __name__ == '__main__':
    main()
