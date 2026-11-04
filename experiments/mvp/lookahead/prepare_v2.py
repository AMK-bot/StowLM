"""Prepare separate fixed-input v2 jobs; never starts a worker or freezes live code."""
import json
from pathlib import Path
from worker import HERE, save


def main():
    cases = []
    for path in sorted((HERE/'formal-paired').glob('shard*/manifest.json')):
        for job in json.loads(path.read_text())['jobs']:
            result = json.loads((Path(job['directory'])/'result.json').read_text())
            assert result['completed'] and result['source_valid']
            cases.append((result['wall_s'],job))
    assert len(cases) == 35
    # Offset shard1 by the measured contract-control wall so the later start
    # does not needlessly leave one CPU idle at the campaign tail.
    contract_control = json.loads((HERE/'formal-contracts/controller.json').read_text())['wall_s']
    scores=[0.,contract_control]; shards=[[],[]]
    for weight, original in sorted(cases,key=lambda item:(-item[0],item[1]['case']['case'])):
        index=min(range(2),key=lambda i:scores[i]);job=dict(original)
        job.update(directory=str(HERE/f'formal-v2-paired/shard{index}'/job['case']['case']),freeze_manifest=str(HERE/'runtime-freeze-v2.json'),lookahead_config=dict(support_conditioned=True))
        shards[index].append(job);scores[index]+=weight
    for index,jobs in enumerate(shards):
        directory=HERE/f'formal-v2-paired/shard{index}';directory.mkdir(parents=True,exist_ok=True)
        save(directory/'manifest.json',dict(directory=str(directory),worker=str(HERE/'paired_v2.py'),jobs=jobs,watchdog_s=650,
                                            v1_weight_s=sum(next(w for w,j in cases if j['case']['case']==job['case']['case'])for job in jobs),
                                            source_policy='Separate v2 all-new paired run, same fixed35inputs and same input/rep seeds. No v1 baseline reuse.'))
    inventory=json.loads((HERE/'inventory.json').read_text());directory=HERE/'formal-v2-contracts';directory.mkdir(exist_ok=True)
    jobs=[dict(mode='contract',arm='lookahead',case=case,directory=str(directory/case['case']),freeze_manifest=str(HERE/'runtime-freeze-v2.json'),duty_fraction=.9,lookahead_config=dict(support_conditioned=True))for case in inventory['contracts']]
    save(directory/'manifest.json',dict(directory=str(directory),worker=str(HERE/'worker.py'),jobs=jobs,watchdog_s=650))
    pilot_contracts=[next(job for job in jobs if job['case']['rung']==rung)for rung in ('tiny','small')]
    pilot_directory=HERE/'pilot-v2-contracts';pilot_directory.mkdir(exist_ok=True)
    save(pilot_directory/'manifest.json',dict(directory=str(pilot_directory),worker=str(HERE/'worker.py'),jobs=pilot_contracts,watchdog_s=650))
    pilot_pair=next(job for shard in shards for job in shard if job['case']['case']=='test15-28100010')
    pilot_directory=HERE/'pilot-v2-paired';pilot_directory.mkdir(exist_ok=True)
    pilot_pair=dict(pilot_pair,rollouts=1,directory=str(pilot_directory/pilot_pair['case']['case']))
    save(pilot_directory/'manifest.json',dict(directory=str(pilot_directory),worker=str(HERE/'paired_v2.py'),jobs=[pilot_pair],watchdog_s=180))
    save(HERE/'contract-witnesses-v2.json',dict(cases={job['case']['case']:str(Path(job['directory'])/'result.json')for job in jobs},policy='All100 rerun under v2; no v1 pilot/control result reuse.'))
    save(HERE/'campaign-v2.json',dict(required_rollouts=1400,contracts=100,paired_instances=35,rollouts_per_arm=20,
                                    schedule='pairedshard0 +contracts;thenpairedshard1transferscontractslot',
                                    balancing=dict(v1_worker_wall_s=sum(w for w,_ in cases),measured_contract_offset_s=contract_control,
                                                   projected_shard_finish_weights_s=scores),
                                    admitted=False,source_frozen=False,
                                    seed_policy='Unchanged lookahead-v1:input_sha:repetition seed namespace intentionally retained for controlled v1-v2 comparison.'))
    print(json.dumps(dict(prepared=True,shards=[len(s)for s in shards],v1_weight_s=scores,not_launched=True)))


if __name__ == '__main__':
    main()
