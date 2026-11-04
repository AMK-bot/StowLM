"""Save thirty current production-generator pairs, resumably, without solving.

Single CPU; peak expected <1 GiB, no GPU. Five deterministic seeds per
(generator, ship), distributed tiny/small/medium/tiny/small. Empty plans retain
all unmet load-list failures; full physics still judges their actual background
cargo and wet tanks at three states. No booking, physics or rule is relaxed.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import os
from pathlib import Path
from time import perf_counter

for variable in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[variable] = '1'
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('perf1028_corpus_generate_helper', HERE/'corpus.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def main():
    from stowage.generate.random_yard import generate_random_instance
    from stowage.generate.instances import generate_simulated_instance
    from stowage.model import MovePlan
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=30)
    args = parser.parse_args()
    result_path = HERE/'corpus-generation.json'
    existing = json.loads(result_path.read_text()) if result_path.exists() else []
    rows = {row['id']: row for row in existing}
    made = 0
    for ship_index, ship in enumerate(('feeder_500','feeder_1000','feeder_1700')):
        for generator in ('random_yard','simulated_yard'):
            for index, rung in enumerate(('tiny','small','medium','tiny','small')):
                case_id=f'current-{generator}-{ship}-{rung}-{index}'
                folder=HERE/'corpus-current'/case_id
                if case_id in rows:
                    continue
                if made >= args.limit:
                    return
                name=c.canonical_ship(ship)
                started=perf_counter()
                seed=1028000+ship_index*100+index*3+(generator=='simulated_yard')
                generate=(generate_random_instance if generator=='random_yard' else generate_simulated_instance)
                instance=generate(rung,seed=seed,vessel_name=name,max_attempts=50)
                plan=MovePlan(id=instance.id+'.perf-empty',instance_id=instance.id,call_index=instance.call_index)
                folder.mkdir(parents=True,exist_ok=True)
                row=dict(id=case_id,kind='saved',ship=ship,generator=generator,rung=rung,
                    current_generated=True,seed=seed,plan_strategy='empty: retain unmet load-list failures',
                    generation_s=perf_counter()-started)
                for kind,value in (('instance',instance),('plan',plan)):
                    path=folder/(kind+'.json.gz')
                    payload=gzip.compress(value.model_dump_json().encode(),mtime=0)
                    path.write_bytes(payload)
                    row[kind]=str(path.relative_to(c.ROOT));row[kind+'_sha256']=c.digest(payload)
                rows[case_id]=row
                result_path.write_text(json.dumps(list(rows.values()),indent=2)+'\n')
                manifest=json.loads(c.MANIFEST.read_text())
                manifest['cases']=[item for item in manifest['cases'] if item['id']!=case_id]+[row]
                c.MANIFEST.write_text(json.dumps(manifest,indent=2)+'\n')
                print(json.dumps(dict(id=case_id,generation_s=row['generation_s'])),flush=True)
                made+=1


if __name__=='__main__':
    main()
