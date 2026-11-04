"""Select all drift-affected v2 cases deterministically; never overwrite attempts."""
import json
from pathlib import Path
from worker import HERE,save


def main():
    old={r['case']:r for r in json.loads((HERE/'summary-v1.json').read_text())['formal_cases']};selected=[];retained=[]
    for manifest in sorted((HERE/'formal-v2-paired').glob('shard*/manifest.json')):
        for job in json.loads(manifest.read_text())['jobs']:
            path=Path(job['directory'])/'result.json';result=json.loads(path.read_text())
            reason='source_invalid_inflight'if not result.get('source_valid')else('preflight_or_worker_error'if result.get('error')else None)
            if reason:selected.append((old[job['case']['case']]['wall_s'],job,str(path),reason))
            else:
                assert result.get('completed')and len(result['rollouts'])==40
                retained.append(dict(case=job['case']['case'],result=str(path),policy='Retained whole completed source-valid old-epoch case, irrespective of outcome.'))
    assert len(selected)==14 and len(retained)==21
    scores=[0.,0.];shards=[[],[]];selection=[]
    for weight,original,path,reason in sorted(selected,key=lambda row:(-row[0],row[1]['case']['case'])):
        index=min(range(2),key=lambda i:scores[i]);job=dict(original);job['directory']=str(HERE/f'formal-v2-archive/shard{index}'/job['case']['case']);shards[index].append(job);scores[index]+=weight
        selection.append(dict(case=job['case']['case'],original_result=path,replacement_result=str(Path(job['directory'])/'result.json'),reason=reason,v1_case_wall_s=weight,shard=index))
    for index,jobs in enumerate(shards):
        directory=HERE/f'formal-v2-archive/shard{index}';directory.mkdir(parents=True,exist_ok=True)
        save(directory/'manifest.json',dict(directory=str(directory),worker=str(HERE/'runtime-v2/experiments/mvp/lookahead/paired_v2.py'),jobs=jobs,watchdog_s=650,estimated_v1_weight_s=scores[index],estimate_s=scores[index]*1.6,policy='All14 affected cases rerun whole under exact archived source; no outcome selection.'))
    save(HERE/'archive-continuation-selection.json',dict(selected=selection,retained=retained,v1_total_weight_s=sum(scores),shard_v1_weights_s=scores,estimated_wall_factor=1.6,estimated_cores=2,estimated_ram_gb=4,gpu=False))
    print(json.dumps(dict(cases=len(selected),retained=len(retained),shard_cases=[len(jobs)for jobs in shards],v1_weights_s=scores,estimated_shard_wall_s=[weight*1.6 for weight in scores])))


if __name__=='__main__':main()
