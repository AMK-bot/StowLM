"""Audit qualified old-epoch witnesses separately from excluded drift attempts."""
import json
import hashlib
from functools import lru_cache
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]


@lru_cache(None)
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text())


def main():
    freeze=read(HERE/'runtime-freeze-v2.json');expected=freeze['lookahead_runtime_sources'];selection=read(HERE/'archive-continuation-selection.json');witnesses=read(HERE/'primary-v2-witnesses.json')['cases'];contracts=read(HERE/'contract-witnesses-v2.json')['cases'];rows=[];pending=[]
    for kind,mapping in (('primary',witnesses),('contract',contracts)):
        for case,name in mapping.items():
            path=Path(name)
            if not path.exists()or not read(path).get('completed'):
                pending.append(dict(kind=kind,case=case,result=name));continue
            result=read(path);before=read(path.parent/'source-before.json');after=read(path.parent/'source-after.json')
            rows.append(dict(kind=kind,case=case,result=name,result_sha256=sha(path),source_epoch=result.get('source_epoch_before'),source_exact=before==after==expected,
                             worker_exact=result['worker_sha256']==freeze['paired_worker_sha256'if kind=='primary'else'worker_sha256'],
                             helper_exact=result.get('helper_sha256',freeze['worker_sha256'])==freeze['worker_sha256'],
                             tokenizer_exact=result.get('tokenizer_sha256')==read(HERE/'runtime-v2/archive-manifest.json')['files']['models/glimmer-30b-4bit/tokenizer.json'],
                             rollouts=len(result.get('rollouts',[])),error=result.get('error'),contract_passed=result.get('contract_passed')))
    excluded=[]
    for item in selection['selected']:
        result=read(item['original_result']);excluded.append(dict(case=item['case'],result=item['original_result'],result_sha256=sha(item['original_result']),reason=item['reason'],raw_rollouts=len(result.get('rollouts',[])),source_valid=result.get('source_valid'),error=result.get('error'),cpu_s=result.get('cpu_s'),wall_s=result.get('wall_s')))
    controllers=[]
    for folder in ('pilot-v2-contracts','pilot-v2-paired','formal-v2-contracts','formal-v2-paired/shard0','formal-v2-paired/shard1','formal-v2-archive/shard0','formal-v2-archive/shard1'):
        path=HERE/folder/'controller.json'
        if path.exists():controllers.append(dict(path=str(path),**read(path)))
    active=[dict(controller=c['pid'],child=j['pid'],case=j['case'])for c in controllers for j in c['jobs']if j.get('pid')and not j.get('reaped')]
    archive_manifest=read(HERE/'runtime-v2/archive-manifest.json')['files'];archive_bad=[name for name,digest in archive_manifest.items()if sha(HERE/'runtime-v2'/name)!=digest]
    foreign_drift=[name for name,digest in expected.items()if not (ROOT/name).exists()or sha(ROOT/name)!=digest]
    raw_children=[dict(controller=c['pid'],pid=j['pid'],case=j['case'],exit_code=j.get('exit_code'),reaped=j.get('reaped',False))for c in controllers for j in c['jobs']if j.get('pid')]
    jobs_by_result={j['result']:j for c in controllers for j in c['jobs']if j.get('pid')}
    for row in rows:
        job=jobs_by_result[row['result']]
        row.update(pid=job['pid'],exit_code=job.get('exit_code'),reaped=job.get('reaped',False),
                   source_same_frozen_epoch=row['source_exact'],worker_hash_matches=row['worker_exact'],
                   helper_hash_matches=row['helper_exact'],tokenizer_hash_matches=row['tokenizer_exact'])
    result=dict(final=not pending and len(controllers)==7 and all(c['completed']for c in controllers)and not active,accepted_epoch=freeze['epoch'],accepted=rows,pending=pending,
                excluded_original_attempts=excluded,excluded_raw_rollouts=sum(row['raw_rollouts']for row in excluded),
                active_owned_children=active,raw_owned_children=raw_children,archive_hash_mismatches=archive_bad,
                current_live_drift=foreign_drift,live_drift_policy='Explicitly foreign version changes; no live-source compatibility claim. All accepted witnesses must match the full old source map with no exclusions.',
                all_accepted_exact=all(row['source_exact']and row['worker_exact']and row['helper_exact']and row['tokenizer_exact']and not row['error']for row in rows),
                all_raw_children_reaped_exit0=all(row['reaped']and row['exit_code']==0 for row in raw_children),
                continuation_launchers=[read(HERE/f'formal-v2-archive/shard{index}/archive-launcher.json')for index in(0,1)])
    result.update(completed_results=rows,controllers=controllers,current_runtime_drift=foreign_drift)
    (HERE/'audit-v2-combined.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n');print(json.dumps({key:result[key]for key in('final','all_accepted_exact','excluded_raw_rollouts','archive_hash_mismatches','current_live_drift','active_owned_children')}))


if __name__=='__main__':main()
