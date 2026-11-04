"""Independent fresh-process replay/full check of selected concrete plans."""
from pathlib import Path
import argparse,hashlib,json
from stowage.io import load_instance
from stowage.model.plan import MovePlan
from stowage.moves import apply_plan
from stowage.checker import check
from catalog import ensure_safe

HERE = Path(__file__).resolve().parent


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--runs',nargs='*',default=[])
    args=ap.parse_args()
    wanted={r['evidence'] for r in json.loads((HERE/'comparison.json').read_text())['primary'] if r['passed']}
    for run in args.runs:
        for p in Path(run).glob('*.json'):
            if p.name!='summary.json' and json.loads(p.read_text()).get('passed'):wanted.add(str(p))
    seen=set();out=[]
    for path in sorted(wanted):
        row=json.loads(Path(path).read_text())
        key=json.dumps(row['plan'],sort_keys=True)
        if key in seen:continue
        seen.add(key)
        ensure_safe(row['input'])
        assert hashlib.sha256(Path(row['input']).read_bytes()).hexdigest()==row['input_sha256']
        i=load_instance(row['input']);plan=MovePlan.model_validate(row['plan'])
        replay=apply_plan(i,plan);r=check(i,plan)
        assert replay.legal and r.passed
        assert r.count.loads==len(i.load_list)
        assert r.count.weighted_nonproductive==row['lifts']
        item=dict(instance_id=i.id,evidence=path,replay_legal=True,passed=True,
                  lifts=row['lifts'],loads=r.count.loads,violations=len(r.violations))
        out.append(item);print(json.dumps(item),flush=True)
        (HERE/'audit.json').write_text(json.dumps(out,indent=2)+'\n')


if __name__=='__main__':main()
