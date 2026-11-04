"""Separate shot 10: a scripted planner can clarify a withheld model answer.

No benchmark case is rerun. Spark chooses tools and writes all prose; exact
reader rows and the planner's confirmation remain real recorded host events.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import re

from experiments.mvp.pipeline.contract.schema import canonical_json, sha256_json
from .hero_campaign import HERE, save
from .native_runtime import native_caches

OUT = HERE/'fixtures/hero-shot10'


def ranking_delivered(row):
    text = (row.get('turn') or {}).get('say','')
    return (not (row.get('gate') or {}).get('reasons')
            and all(value in text for value in ('dg-clearance','bay-closure','70.0','82.0'))
            and bool(re.search(r'\b(?:recommend|prefer|cheaper|fewer|lower)\b',text,re.I)))


def run():
    from llm.spark.client import SparkClient
    from experiments.mvp.pipeline.lattice import canonical_rule_set, gate_plan
    from . import hero_run, hero_verified
    from .agent import Copilot
    from .audit_spend import audit
    from .tools import ToolSet

    hero_verified.OUT = OUT
    prepared = hero_verified.prepared()
    hero_run.OUT = OUT
    marker = json.loads((OUT/'run-authorization.json').read_text())
    loaded = hero_run.validate_freeze(marker)
    hero_run.validate_native(prepared)
    with (OUT/'run-started.json').open('x') as stream:
        json.dump(dict(created_utc=datetime.now(timezone.utc).isoformat(),label='Separate once-only recorded conversation; not a fresh scenario rerun.'),stream)
    save(OUT/'loaded-code-attestation.json',dict(loaded=loaded))
    client = SparkClient(max_retries=0,timeout=60)
    with client.ledger.locked():
        records = [r for r in client.ledger._records() if str(r.get('purpose','')).startswith('pipeline-t1.6-copilot')]
        actual,held = client.ledger._totals(records)
    session = hero_run.Session(client,prior_job_usd=actual+held)
    reader = hero_run.Reader(session)
    reader.purpose = 'pipeline-t1.6-copilot/shot10/verified/final-reader'
    model = hero_run.Model(session,'pipeline-t1.6-copilot/shot10/verified/final-conversation')
    rows,checked = prepared['rows'],prepared['checked']
    key = lambda prefix:next(k for k in rows if k.startswith(prefix))
    base,dg,bay,refusal = (key(prefix) for prefix in ('e47b','9381','72cc','3398'))
    alternatives = [dict(id=name,sentences=rows[k]['sentences'],canonical_rules=rows[k]['canonical_rules'])
                    for name,k in (('dg-clearance',dg),('bay-closure',bay))]
    gates = {(r['gate']['instance_sha256'],r['gate']['plan_sha256'],
              canonical_json(canonical_rule_set([item['envelope'] for item in r['gate']['rules']]))):r['gate']
             for r in checked.values() if r.get('gate')}
    def release(instance,plan,rules):
        identity=(sha256_json(instance.model_dump(mode='json')),sha256_json(plan.model_dump(mode='json')),
                  canonical_json(canonical_rule_set(rules)))
        candidate=gates.get(identity)
        return hero_run.tag_recorded_release(candidate) if candidate is not None and hero_run.gate_is_current(candidate) else gate_plan(instance,plan,rules)
    tools = ToolSet(prepared['instance'],parser=reader,plan=checked[base]['plan'],
        lattice=prepared['lattice_lookup'],lattice_store_dir=prepared['store'],
        catalogue_alternatives=alternatives,release_gate=release)
    copilot = Copilot(tools,model,max_steps=10)
    turns=[]
    def turn(message,**request):
        value=hero_run._turn(copilot,model,message,hero_run._request(tools,**request))
        turns.append(value)
        save(OUT/'shot10-progress.json',dict(turns=turns))
        return value
    first=turn('Read my selected inspection request. If it is impossible, explain the arithmetic and compare the checked alternatives, naming the cards and recommending one with reasons and lift measures before I choose.',
        selected='\n'.join(rows[refusal]['sentences']),limited=('read_instruction','list_limits'))
    clarifications=[
        'I can see the tool cards. Please distinguish a checked what-if candidate from whether I have chosen or applied it. Compare dg-clearance and bay-closure, give their checked status and lift measures, and recommend a card with reasons.',
        'Please use the exact card names when explaining their checker status. Which checked candidate do you recommend for fewer weighted lifts, and why? I have not chosen a replacement yet.'
    ]
    for message in clarifications:
        if ranking_delivered(turns[-1]):
            break
        turn(message,limited=('list_limits',))
    choice=rows[dg]['sentences'][0]
    turn('I choose dg-clearance. Read my selected sentence and show every row so I can confirm it.',
         selected=choice,limited=('read_instruction','list_limits'))
    pending=tools.context().get('current_reading') or {}
    parsed=[r.get('parsed') for r in pending.get('rows',[]) if r.get('role')=='voyage' and r.get('status')=='accepted']
    if pending.get('sentence')==choice and canonical_rule_set(parsed)==canonical_rule_set(rows[dg]['canonical_rules']):
        confirmation=tools.confirm_reading(pending.get('reading_id'))
    else:
        confirmation=dict(status='unavailable',reason='Selected reader rows did not match; no confirmation supplied.')
    if confirmation.get('status')=='confirmed':
        turn('I confirmed those displayed rows through the planner. Find the stored plan, check it and explain the operational change.',
             limited=('plan_lookup','check_plan','list_limits'))
    shot=dict(schema_version='stowlm.copilot-shot10/1',synthetic=True,
        label='Recorded real-tool conversation; scripted planner events and clarification after withheld prose; Spark chooses actions and writes all prose.',
        reader='Unadapted cloud Spark reader; not the adopted local fine-tune.',
        snapshot=prepared['snapshot'],turns=turns,host_confirmation=confirmation,
        model_calls=model.calls,ranking_delivered=any(ranking_delivered(t) for t in turns))
    save(OUT/'shot10.json',shot)
    observed=[t['turn']['plan_sha256'] for t in turns if t.get('turn') and t['turn'].get('plan_sha256')]
    choices={a['id']:a for t in turns for e in t['trace'] if e.get('role')=='tool'
             for a in e['content'].get('alternatives',[]) if a.get('checked') is True}
    save(OUT/'shot10-facts.json',dict(schema_version='stowlm.copilot-shot10-facts/1',synthetic=True,
        conversation=str((OUT/'shot10.json').relative_to(HERE/'fixtures')),reader=shot['reader'],instance_sha256=prepared['snapshot']['hero']['instance_sha256'],
        proof=checked[refusal]['proof'],base_lifts=checked[base]['lifts'],selected_lifts=checked[dg]['lifts'],
        selected_plan_sha256=checked[dg]['gate']['plan_sha256'],confirmed_instruction=choice,
        alternatives=[dict(id=name,label='checked',lifts=a.get('lifts'),plan_sha256=a.get('plan_sha256'),
                           evidence=a.get('stored_evidence'),sentences=a.get('sentences')) for name,a in choices.items()],
        observed_acceptance=observed,ranking_delivered=shot['ranking_delivered'],
        frames_checked=checked[dg]['gate']['gauges']['n_frames'],what_moved=checked[dg]['gate']['what_moved'],
        facts_label='Actual observed tool cards and returned checked hashes; numerical gates reused from campaign preparation, not rerun per turn. Weighted nonproductive lifts include projected later-port lifts.'))
    if tools._active_job:
        hero_run.drain_job(tools,OUT/'background-terminal.json')
    save(OUT/'finished.json',dict(created_utc=datetime.now(timezone.utc).isoformat(),spend=audit()))
    print('Separate hero shot10 completed',flush=True)


if __name__=='__main__':
    with native_caches():
        run()
