"""Existing offline budget120 retry: same samples/seeds, fresh full observations.

Failed original shards and command records remain immutable. The extra budget
only permits the unchanged public reference checker to finish its work.
"""
from concurrent.futures import ThreadPoolExecutor,as_completed
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

import campaign

RUN=Path(__file__).resolve().parent
CENTRAL=RUN.parent
OWNER=CENTRAL.parent
OUT=RUN/"ordinary"
LOGS=RUN/"recovery-logs"
sys.path.insert(0,str(CENTRAL))
from compute import run


def populations():
    result=[]
    for ship in campaign.SHIPS:
        for path in sorted((OWNER/"production"/ship).glob("*.json")):
            if path.name.startswith(("train-","holdout-","enrichment")):
                result.append((ship,path.name))
    return result


def original_statuses():
    names=["raw-"+ship for ship in campaign.SHIPS]
    names += [ship+"-"+Path(name).stem for ship,name in populations()]
    names += ["boundary-"+ship for ship in campaign.SHIPS]
    names += ["targeted-"+kind for kind in ("min","grid","pairs-3823","pairs-3824")]
    assert len(names)==46
    while True:
        campaign.check_abort()
        records={}
        for name in names:
            path=RUN/"campaign-logs"/(name+".execution.json")
            if path.exists():
                record=json.loads(path.read_text())
                if "passed" in record: records[name]=record
        if len(records)==46:return records
        time.sleep(2)


def worker(ship,name):
    campaign.check_abort()
    assert os.environ.get("STOWAGE_STABILITY_TIME_BUDGET_S")=="120"
    driver=campaign.load_driver()
    prepared=driver.prepare(ship,OUT)
    previous=json.loads((OWNER/"production"/ship/name).read_text())
    shard=RUN/"retry-budget120-shards"/ship/Path(name).stem
    record=driver.physical_case(ship,previous["level"],previous["phase"],shard,prepared,
        samples=previous["samples"],seed=previous["seed"],public=True)
    assert record["physical_inputs_unchanged"] and record["public_crosscheck_completed"]
    assert all(record[key]==previous[key] for key in ("phase","level","samples","seed"))
    # Only the promoter's slot is published, and only after a fresh full run.
    destination=OUT/ship/name
    if destination.exists():raise ValueError("never overwrite canonical physical evidence: "+str(destination))
    driver.write(destination,record)


def retry(ship,name):
    args=[Path(__file__),"worker",ship,name]
    result=run("seawater-retry120-"+ship+"-"+Path(name).stem,args,
        env_extra={"STOWAGE_STABILITY_TIME_BUDGET_S":"120"})
    campaign.write(LOGS/(ship+"-"+Path(name).stem+".execution.json"),result)


def main():
    LOGS.mkdir(exist_ok=True)
    if len(sys.argv)>1 and sys.argv[1]=="worker":worker(sys.argv[2],sys.argv[3]);return
    started=time.monotonic()
    campaign.write(LOGS/"controller.json",dict(pid=os.getpid(),started_unix=time.time(),
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),budget_seconds=120))
    statuses=original_statuses()
    population_names={ship+"-"+Path(name).stem:(ship,name) for ship,name in populations()}
    failed=[]
    for name,record in statuses.items():
        if record["passed"]:continue
        if name not in population_names:raise ValueError("nonphysical failure requires separate finding: "+name)
        ship,filename=population_names[name]
        original=RUN/"ordinary-shards"/ship/Path(filename).stem/ship/("holdout-" if filename.startswith("enrichment") else "")
        old=json.loads((OWNER/"production"/ship/filename).read_text())
        original=RUN/"ordinary-shards"/ship/Path(filename).stem/ship/f"{old['phase']}-{old['level']}.json"
        observation=json.loads(original.read_text())
        error=observation.get("public_checker_comparison",{}).get("stability_error","")
        if not error or "time budget exceeded" not in error:
            raise ValueError("failed case is not the authorized public checker timeout: "+name)
        if (OUT/ship/filename).exists():raise ValueError("failed case already has canonical output")
        failed.append((ship,filename))
    campaign.write(LOGS/"retries.json",dict(budget_seconds=120,cases=failed,
        design="same original populations/seeds; full fresh remeasurement; failed evidence retained"))
    with ThreadPoolExecutor(max_workers=6) as pool:
        for future in as_completed([pool.submit(retry,ship,name) for ship,name in failed]):future.result()
    for ship,name in populations():
        old=json.loads((OWNER/"production"/ship/name).read_text())
        record=json.loads((OUT/ship/name).read_text())
        assert record["public_crosscheck_completed"] and record["physical_inputs_unchanged"]
        assert all(old[key]==record[key] for key in ("phase","level","samples","seed"))
    for kind in ("min","grid","pairs-3823","pairs-3824"):
        source=RUN/"targeted"/kind/"feeder_1000/train-light.json"
        destination=OUT/"feeder_1000"/("targeted-"+kind+"-light.json")
        if destination.exists():
            if destination.read_bytes()!=source.read_bytes():raise ValueError("conflicting targeted evidence")
        else:shutil.copyfile(source,destination)
    for ship in campaign.SHIPS:
        run("seawater-retry120-equivalence-"+ship,[RUN/"campaign.py","equivalence-worker",ship])
    result=dict(passed=True,retry_budget_seconds=120,retried_cases=failed,
        seconds=time.monotonic()-started,original_failures_preserved=True)
    campaign.write(LOGS/"complete.json",result)
    campaign.write(RUN/"ordinary-logs/campaign.execution.json",result)


if __name__=="__main__":main()
