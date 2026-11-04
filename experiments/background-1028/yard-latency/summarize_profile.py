"""Summarize read-only production-gate timing records (no physics executes)."""
from argparse import ArgumentParser
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import median


def summary(directory):
    events=[json.loads(line) for line in (directory/'events.jsonl').read_text().splitlines()]
    result=json.loads((directory/'result.json').read_text()) if (directory/'result.json').exists() else {}
    sums=defaultdict(float); counts=Counter(); maxima=defaultdict(float)
    for e in events:
        if e['event']=='part_end':
            name=e['part'];sums[name]+=e['elapsed_s'];counts[name]+=1;maxima[name]=max(maxima[name],e['elapsed_s'])
    checks=[e for e in events if e['event']=='check_end']
    completed_checks=[e for e in checks if e['passed'] is not None]
    keytimes=[e['elapsed_s'] for e in events if e['event']=='cache_key_end']
    equations=[e for e in events if e['event']=='equilibrium_end']
    eqs=defaultdict(list)
    for e in equations:eqs[f"{e['part']}/{e['geometry']}"] .append(e['elapsed_s'])
    candidates=[e for e in events if e['event']=='candidate_end']
    phase_sums=defaultdict(float);phase_counts=Counter()
    for e in events:
        if e['event']=='condition_part' and e['elapsed_s'] is not None:
            key=f"{e['condition_phase']}/{e['part_name']}"
            phase_sums[key]+=e['elapsed_s'];phase_counts[key]+=1
    other_timers={}
    for name in ('finisher','completion','c2'):
        values=[e['elapsed_s'] for e in events if e['event']==name+'_end']
        other_timers[name]=dict(calls=len(values),total_s=sum(values),maximum_s=max(values,default=None),statuses=dict(Counter(e.get('status') for e in events if e['event']==name+'_end')))
    start=result.get('source_start',{});end=result.get('source_end',{})
    return dict(directory=str(directory),elapsed_s=result.get('elapsed_s',events[-1]['t_s']),
        result={k:result[k] for k in ('accepted','passed','error','error_type','pid') if k in result},
        full_checks=dict(started=sum(e['event']=='check_start' for e in events),completed=sum(e['passed'] is not None for e in checks),interrupted=sum(e['passed'] is None for e in checks),
            passed=sum(e['passed'] is True for e in checks),median_s=median(e['elapsed_s'] for e in completed_checks) if completed_checks else None,
            maximum_s=max((e['elapsed_s'] for e in completed_checks),default=None),
            total_s=sum(e['elapsed_s'] for e in checks)),
        cache_key=dict(calls=len(keytimes),total_s=sum(keytimes),median_s=median(keytimes) if keytimes else None),
        parts={name:dict(calls=counts[name],total_s=sums[name],maximum_s=maxima[name]) for name in counts},
        uncached_equilibrium={name:dict(calls=len(values),total_s=sum(values),maximum_s=max(values)) for name,values in eqs.items()},
        condition_parts={name:dict(calls=phase_counts[name],total_s=value) for name,value in phase_sums.items()},
        other_timers=other_timers,candidate_observer_count=len(candidates),last_audit=result.get('audit') or (candidates[-1]['audit'] if candidates else None),
        source_changes=[name for name in start.keys()|end.keys() if start.get(name)!=end.get(name)])

if __name__=='__main__':
    parser=ArgumentParser(description=__doc__);parser.add_argument('directories',nargs='+',type=Path);parser.add_argument('--out',type=Path)
    args=parser.parse_args();data=[summary(directory) for directory in args.directories]
    text=json.dumps(data,indent=2,sort_keys=True)+'\n'
    if args.out:args.out.write_text(text)
    else:print(text)
