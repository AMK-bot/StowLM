"""Render only full-checked D185 paired results; exact two-sided sign tests."""
from __future__ import annotations
import json
from math import comb
import os
from pathlib import Path
import statistics
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def read(path):
    return json.loads(path.read_text())


def write_atomic(path, value):
    temporary=path.with_name(path.name+f'.tmp-{os.getpid()}')
    temporary.write_text(value)
    temporary.replace(path)


def sign_test(wins, losses):
    n = wins + losses
    return min(1., 2 * sum(comb(n,k) for k in range(min(wins,losses)+1)) / 2**n) if n else 1.


def checked(record):
    if record.get('passed'):
        assert record['checker_passed'] is True and record['checker_attempted'] is True
        assert record['finalize_status'] == 'ok' and not record['layout_violations']
        assert (ROOT/record['finished_path']).is_file() and (ROOT/record['report_path']).is_file()
        return record['moves']
    return None


def stats(rows, variant):
    paired = [r['selected'][variant] for r in rows if r.get('complete')]
    baseline = [checked(p['default']) for p in paired]
    planner = [checked(p['planner']) for p in paired]
    mutual = [(a,b) for a,b in zip(baseline,planner) if a is not None and b is not None]
    rule_costs = [checked(p['rule']) for p in paired]
    rule_mutual = [(a,b) for a,b in zip(baseline,rule_costs) if a is not None and b is not None]
    rule_wins = sum(b<a for a,b in rule_mutual)
    rule_losses = sum(b>a for a,b in rule_mutual)
    fewer = sum(b<a for a,b in mutual)
    more = sum(b>a for a,b in mutual)
    equal = len(mutual)-fewer-more
    rescue = sum(a is None and b is not None for a,b in zip(baseline,planner))
    lost = sum(a is not None and b is None for a,b in zip(baseline,planner))
    mean_b = statistics.mean(a for a,b in mutual) if mutual else None
    mean_p = statistics.mean(b for a,b in mutual) if mutual else None
    return dict(complete_maps=len(paired), default_passes=sum(a is not None for a in baseline),
                planner_passes=sum(b is not None for b in planner), mutual=len(mutual),
                default_mean=mean_b, planner_mean=mean_p,
                improvement_pct=100*(mean_b-mean_p)/mean_b if mean_b else None,
                fewer=fewer, equal=equal, more=more, moves_sign_p=sign_test(fewer,more),
                rescues=rescue, lost=lost, pass_sign_p=sign_test(rescue,lost),
                rule_selected=sum(p['planner'].get('passed') and p['planner']['arm']=='rule' for p in paired),
                rule_passes=sum(p['rule'].get('passed', False) for p in paired),
                rule_mutual=len(rule_mutual), rule_fewer=rule_wins,
                rule_equal=len(rule_mutual)-rule_wins-rule_losses, rule_more=rule_losses,
                rule_moves_sign_p=sign_test(rule_wins,rule_losses),
                rule_mutual_default_mean=statistics.mean(a for a,b in rule_mutual) if rule_mutual else None,
                rule_mutual_rule_mean=statistics.mean(b for a,b in rule_mutual) if rule_mutual else None,
                default_kept=sum(p['planner'].get('passed') and p['planner']['arm']=='default' for p in paired),
                neither_passed=sum(not p['planner'].get('passed') for p in paired))


def fmt(check):
    value = checked(check)
    return str(value) if value is not None else '—'


def stages(arm, variant):
    if not arm:
        return 'pending'
    c = arm.get(variant, {})
    return '/'.join((arm.get('status','pending'), c.get('finalize_status','pending'),
                     'pass' if c.get('checker_passed') else 'fail' if c.get('checker_attempted') else '—'))


def render():
    lines = ['# BLOCKRULE — D185 software baseline', '',
             f'Updated {time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())}. CPU only; no model training, GPU, test, showcase or git operations.', '',
             'Port blocking is evaluated against default two-step. A rule plan replaces default only after full checking and a strict move improvement, or when default fails. Ties retain default. Compaction is offered to both arms and retained only when checked and better. This is a baseline change, not evidence for an LLM.', '',
             'Moves mean checker `weighted_nonproductive`, including later-call work. All move means use mutual passing maps. The final capacity policy and its pre-validation timestamp are in `capacity-policy.json`.', '',
             'See [COHORT.md](COHORT.md), [DIAGNOSIS.md](DIAGNOSIS.md), and [LEDGER.md](LEDGER.md). `runs/*/run.json` pins inputs, source and budgets. Raw/compacted planner results, every finisher result and checker reports are saved per map. Only passing full checks have reported move counts.', '',
             'Two-sided exact sign tests exclude move ties; the pass test uses discordant pairs. No multiplicity correction. Selection guarantees no loss of passes or checked cost against the measured default by design, so selected-planner tests are descriptive. Rule-alone pairs are also shown; they exclude maps where either arm fails.', '']
    machine = {}
    for name in ('small-train','medium-train','small-val'):
        out = HERE/'runs'/name
        lines += [f'## {name}', '']
        if not (out/'run.json').exists():
            lines += ['Not started. Validation runs once after the train freeze.', '']
            continue
        run = read(out/'run.json')
        rows = [read(out/'maps'/j['map_id']/'summary.json') for j in run['jobs'] if (out/'maps'/j['map_id']/'summary.json').exists()]
        lines += [f'{sum(r.get("complete",False) for r in rows)}/{len(run["jobs"])} maps complete. Config: `{json.dumps(run["config"], sort_keys=True)}`.', '']
        if run.get('capacity_epoch'):
            lines += ['Final train epoch: capacity-safe boundary sharing on four medium masks. Default results, all small masks and eight unchanged medium masks inherit byte-identical physical artifacts with a repeated layout audit; these are reused full-check certificates, not new solver/checker calls. `inheritance.json` and per-record lineage link the immutable initial runs. Changed medium masks receive new solves, finishers and full checks.', '']
        machine[name] = {}
        for variant in ('raw','compact'):
            s = stats(rows, variant)
            machine[name][variant] = s
            text = f'{variant}: checked passes default {s["default_passes"]}, planner {s["planner_passes"]}; {s["mutual"]} mutual.'
            if s['mutual']:
                text += f' Mean {s["default_mean"]:.3f} → {s["planner_mean"]:.3f} (−{s["improvement_pct"]:.2f}%); fewer/equal/more {s["fewer"]}/{s["equal"]}/{s["more"]}, sign p={s["moves_sign_p"]:.6g}.'
            text += f' Rescue/lost {s["rescues"]}/{s["lost"]}, pass p={s["pass_sign_p"]:.6g}. Rule alone passes {s["rule_passes"]}; rule selected {s["rule_selected"]}; default kept {s["default_kept"]}; neither {s["neither_passed"]}.'
            lines += [text, '']
            if s['rule_mutual']:
                lines += [f'Rule alone ({variant}): {s["rule_mutual"]} mutual, default {s["rule_mutual_default_mean"]:.3f} → rule {s["rule_mutual_rule_mean"]:.3f}; fewer/equal/more {s["rule_fewer"]}/{s["rule_equal"]}/{s["rule_more"]}, sign p={s["rule_moves_sign_p"]:.6g}.', '']
        lines += ['Stages are solve / finisher / full checker for raw plans. Compact columns use the better passing original or compacted result on each arm.', '',
                  '| Map | Loads | Default stages | Rule stages | Raw default | Raw rule | Raw selected | Compact default | Compact rule | Compact selected |',
                  '|---|---:|---|---|---:|---:|---|---:|---:|---|']
        keyed = {r['job']['map_id']:r for r in rows}
        for job in run['jobs']:
            r = keyed.get(job['map_id'], {})
            arms, selected = r.get('arms',{}), r.get('selected',{})
            raw, compact = selected.get('raw',{}), selected.get('compact',{})
            pick = lambda p: (fmt(p.get('planner',{})) + ' ' + p.get('planner',{}).get('arm','')) if p else 'pending'
            lines.append('| ' + ' | '.join([job['map_id'],str(job.get('n_loads','—')),
                stages(arms.get('default'),'raw'), stages(arms.get('rule'),'raw'),
                fmt(raw.get('default',{})),fmt(raw.get('rule',{})),pick(raw),
                fmt(compact.get('default',{})),fmt(compact.get('rule',{})),pick(compact)]) + ' |')
        lines += ['', 'All per-map solver metrics, finisher causes, compact checker outcomes and artifact paths remain in the map `summary.json` and `checks/`.', '']
    write_atomic(HERE/'BLOCKRULE.md','\n'.join(lines)+'\n')
    write_atomic(HERE/'summary.json',json.dumps(machine, indent=2)+'\n')
    print(json.dumps(machine, indent=2))


if __name__ == '__main__':
    render()
