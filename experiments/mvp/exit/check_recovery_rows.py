"""CPU diagnostic: checked saved trajectory -> continuation-only admitted row.

This verifies serialization/loss admission, not solver recovery yield.
"""
from dataclasses import asdict
import hashlib
from pathlib import Path
from time import monotonic,process_time

from llm.exit.runner import read_json,write_json,saved_gold_records
from llm.exit.recovery import RecoveryResult,build_recovery_example,admit_cached_example
from llm.exit.data import build_training_dataset
from llm.exit.runtime import _recovery_dataset
from llm.stowlm import train as trainer
from llm.stowlm.format import load_tokenizer
from llm.stowlm.sft_data import plan_actions
from stowage.io import load_instance
from stowage.model import MovePlan


def main():
    root=Path('experiments/mvp/exit')
    out=root/'recovery-cpu-dataset-smoke';out.mkdir(exist_ok=True)
    record=read_json(root/'micro-tiny/winner.record.json')
    instance=load_instance(record['instance_path'])
    plan=MovePlan.model_validate(record['plan'])
    prefix=tuple(plan_actions(instance,plan)[:1])
    result=RecoveryResult(True,plan,prefix,dict(verification=dict(report=record['report'])))
    started=monotonic();cpu=process_time()
    row=build_recovery_example(instance,result,input_path=record['instance_path'])
    payload=asdict(row)
    tokenizer=load_tokenizer()
    admitted=admit_cached_example(instance,plan,record['report'],payload,tokenizer=tokenizer)
    recovery_record=out/'recovery.record.json'
    write_json(recovery_record,{**record,'exit_recovery':dict(admitted_example=payload)})
    report=build_training_dataset([root/'micro-tiny/winner.record.json'],[],out/'data',
        recovery_records=[recovery_record],validation_records=saved_gold_records('experiments/mvp/gold','val')[:1])
    cfg=trainer.TrainConfig(data_dir=str(out/'data'),adapter_dir=str(out/'not-a-trained-adapter'))
    with _recovery_dataset(trainer):
        data=trainer.admit_dataset(cfg)
        dataset=trainer.TokenDataset(data.train,cfg.max_seq_length)
        offsets=[dataset[i][1] for i in range(len(data.train))]
    evidence=dict(mode='CPU_row_admission_only_no_solver_or_GPU',wall_s=monotonic()-started,
        cpu_s=process_time()-cpu,full_prompt_len=row.prompt_len,
        continuation_loss_start=row.meta['exit_recovery']['loss_start'],trainer_offsets=offsets,
        train_rows=len(data.train),loss_val_rows=len(data.val),dataset=report)
    write_json(out/'summary.json',evidence)
    print({k:v for k,v in evidence.items() if k!='dataset'})


if __name__=='__main__':
    main()
