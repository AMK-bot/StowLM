"""Encode saved actual ACTIONS, without rerunning feasibility or any checker."""
import argparse
import ast
import hashlib
import json
import resource
import time
from pathlib import Path
from worker import HERE,ROOT,sha,save


def main(result_path):
    began=time.perf_counter();cpu=time.process_time();original_sha=sha(result_path);result=json.loads(result_path.read_text());case=result['job']['case']
    frozen=json.loads((HERE/'runtime-freeze-v2.json').read_text());tokenizer_path=ROOT/'models/glimmer-30b-4bit/tokenizer.json';archive=json.loads((HERE/'runtime-v2/archive-manifest.json').read_text())['files']
    assert sha(tokenizer_path)==archive['models/glimmer-30b-4bit/tokenizer.json']
    for name in ('src/llm/stowlm/format.py','src/stowage/actions/space.py'):
        assert sha(ROOT/name)==frozen['lookahead_runtime_sources'][name]
    assert sha(case['instance']['path'])==case['instance']['sha256']
    from stowage.io import load_instance
    from stowage.actions.space import Place,Discharge,Relocate,RestowOff
    from llm.stowlm.format import ActionCodec,load_tokenizer
    instance=load_instance(case['instance']['path']);codec=ActionCodec(load_tokenizer(tokenizer_path),[box.id for box in instance.containers]);classes={cls.__name__:cls for cls in (Place,Discharge,Relocate,RestowOff)}
    rows=[]
    for rollout in result.get('rollouts',[]):
        steps=[]
        for step in rollout['steps']:
            node=ast.parse(step['action'],mode='eval').body
            assert isinstance(node,ast.Call)and isinstance(node.func,ast.Name)and node.func.id in classes and not node.args
            assert all(keyword.arg is not None for keyword in node.keywords)
            action=classes[node.func.id](**{keyword.arg:ast.literal_eval(keyword.value)for keyword in node.keywords})
            tokens=list(codec.encode_action(action));steps.append(dict(action=step['action'],token_count=len(tokens),token_ids=tokens,full_query_ms=step['query_ms']))
        rows.append(dict(repetition=rollout['repetition'],seed=rollout['seed'],status=rollout['status'],steps=steps,tokens=sum(s['token_count']for s in steps),action_queries_ms=sum(s['full_query_ms']for s in steps),terminal_empty_query_ms=rollout.get('query_empty_ms',0.)))
    tokens=sum(row['tokens']for row in rows);queries=sum(row['action_queries_ms']for row in rows);actions=sum(len(row['steps'])for row in rows)
    assert sha(result_path)==original_sha
    output=dict(result=str(result_path),result_sha256=original_sha,instance_sha256=case['instance']['sha256'],tokenizer_sha256=sha(tokenizer_path),codec_sha256=sha(ROOT/'src/llm/stowlm/format.py'),analysis_sha256=sha(__file__),lookahead_config=result['job'].get('lookahead_config'),tokens=tokens,actions=actions,full_action_query_ms=queries,full_query_ms_per_action=queries/actions if actions else None,amortized_full_query_ms_per_actual_action_token=queries/tokens if tokens else None,terminal_empty_query_ms=sum(row['terminal_empty_query_ms']for row in rows),rollouts=rows,analysis_wall_s=time.perf_counter()-began,analysis_cpu_s=time.process_time()-cpu,analysis_lifetime_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,policy='Actual saved actions encoded offline with frozen real Glimmer codec. No feasibility/checker replay. EOS excluded. Full query cost, not isolated new-layer overhead or model-generation latency. This analysis CPU is separate from original rollout timing.')
    target=result_path.with_name('token-accounting.json');save(target,output);print(json.dumps({k:output[k]for k in ('tokens','actions','full_query_ms_per_action','amortized_full_query_ms_per_actual_action_token','analysis_cpu_s','analysis_wall_s')}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('result',type=Path);args=parser.parse_args();main(args.result)
