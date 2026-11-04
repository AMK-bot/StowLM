"""CPU-only measured preference preparation on saved checked training plans."""
from copy import deepcopy
from pathlib import Path
from time import monotonic,process_time
import argparse
import json

from llm.exit.preferences import prepare_training_pairs,write_pairs
from llm.exit.runner import read_json,write_json
from llm.exit.selection import Candidate,decoder_factory,score_candidate
from llm.exit.identity import identity_guard
from llm.stowlm.sft_data import codec_for,plan_actions
from llm.stowlm.format import load_tokenizer
from stowage.io import load_instance
from stowage.model import MovePlan
from stowage.actions import get_action_space
from stowage.feasibility import make_feasibility_engine,FeasibilityConfig


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--no-search',action='store_true')
    parser.add_argument('--maps',type=int,default=10)
    args=parser.parse_args()
    root=Path('experiments/mvp/exit')
    out=root/'preference-cpu-benchmark';out.mkdir(exist_ok=True)
    started=monotonic();identity_guard();warm=monotonic()-started
    tokenizer=load_tokenizer()
    rows=[]
    for source in sorted((root/'cpu-dry-run').glob('*/selection.json'))[:args.maps]:
        selection=deepcopy(read_json(source))
        instance=load_instance(selection['instance_path'])
        record=read_json(selection['winner_record'])
        plan=MovePlan.model_validate(record['plan'])
        actions=plan_actions(instance,plan)
        candidate=None
        search_started=monotonic()
        def decoder():
            engine=make_feasibility_engine(instance,get_action_space(),
                config=FeasibilityConfig(extra={'global_screen':0}))
            return decoder_factory()(instance,engine,codec_for(instance,tokenizer),None,1)[0]
        alternatives=[] if args.no_search else decoder().engine.legal_actions()
        for action in list(alternatives)[:32]:
            if monotonic()-search_started>3:
                break
            if not actions or action==actions[0]:
                continue
            try:
                trial=decoder()
                for decision in [action,*actions[1:]]:
                    trial.engine.push(decision)
                if not trial.engine.is_complete():
                    continue
                worse=trial.engine.to_plan()
                score=score_candidate(instance,Candidate(worse,'cpu-negative'))
                if score>selection['weighted_moves']:
                    candidate=(worse,score)
                    break
            except (ValueError,RuntimeError):
                continue
        if candidate:
            negative_path=out/(source.parent.name+'-negative.json')
            write_json(negative_path,candidate[0].model_dump(mode='json'))
            selection['candidates'].append(dict(candidate_id='cpu-negative',source='cpu-probe',
                plan_path=str(negative_path),metadata=dict(status='complete',weighted_moves=candidate[1])))
        phase=monotonic();cpu=process_time()
        prepared=prepare_training_pairs(selection,max_pairs=1)
        elapsed=monotonic()-phase
        write_pairs(out/(source.parent.name+'-pairs.jsonl'),prepared['pairs'])
        rows.append(dict(instance_id=instance.id,rung=instance.size_class,wall_s=elapsed,
            cpu_s=process_time()-cpu,pairs=len(prepared['pairs']),negative_constructed=candidate is not None,
            refusals=prepared['refusals']))
    report=dict(mode='CPU_only_no_reference_or_GPU_training',index_warmup_s=warm,
        negative_search=not args.no_search,wall_s=monotonic()-started,
        maps=len(rows),pairs=sum(r['pairs'] for r in rows),
        preparation_wall_s=sum(r['wall_s'] for r in rows),
        preparation_cpu_s=sum(r['cpu_s'] for r in rows),rows=rows)
    write_json(out/'summary.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='rows'}))


if __name__=='__main__':
    main()
