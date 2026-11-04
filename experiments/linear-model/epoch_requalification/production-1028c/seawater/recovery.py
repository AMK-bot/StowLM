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
    campaign.check_abort()
    started=time.monotonic()
    campaign.write(LOGS/"controller.json",dict(pid=os.getpid(),started_unix=time.time(),
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),budget_seconds=120))
    missing=[]
    for ship,name in populations():
        old=json.loads((OWNER/"production"/ship/name).read_text())
        path=OUT/ship/name
        if not path.exists():missing.append((ship,name));continue
        record=json.loads(path.read_text())
        if not (record["physical_inputs_unchanged"] and record["public_crosscheck_completed"]
                and all(old[key]==record[key] for key in ("phase","level","samples","seed"))):
            raise ValueError("preserve conflicting canonical physical evidence: "+str(path))
    campaign.write(LOGS/"retries.json",dict(budget_seconds=120,cases=missing,
        design="same original populations/seeds; full fresh remeasurement; all failed/aborted evidence retained"))
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures=[pool.submit(retry,ship,name) for ship,name in missing]
        for future in as_completed(futures):future.result()
    for ship in campaign.SHIPS:
        boundary=OUT/ship/"boundary.json"
        certificate=OUT/ship/"boundary-certificate.json"
        if boundary.exists() and certificate.exists():
            evidence=json.loads(boundary.read_text())
            assert evidence["passed"] and len(evidence["calibration"])==64 and len(evidence["holdout"])==64
            continue
        if boundary.exists():raise ValueError("boundary evidence without certificate needs explicit review")
        run("seawater-retry120-boundary-"+ship,[OWNER/"boundary.py","--ship",ship,
            "--output",OUT,"--samples","64","--holdout-samples","64"],
            env_extra={"STOWAGE_STABILITY_TIME_BUDGET_S":"120"})
    sources=[]
    for kind,seed,samples,objective,pairs in (
        ("min",3821,64,"min",()),("grid",3822,128,"grid",()),
        ("pairs-3823",3823,256,"grid",("DB-M","HEEL","WING-A")),
        ("pairs-3824",3824,256,"grid",("DB-M","HEEL","WING-A"))):
        source=RUN/"targeted"/kind/"feeder_1000/train-light.json"
        valid=False
        if source.exists():
            record=json.loads(source.read_text())
            valid=(record.get("public_crosscheck_completed") is True
                and record.get("physical_inputs_unchanged") is True
                and record.get("seed")==seed and record.get("samples")==samples
                and record.get("sampler_source_sha256")==hashlib.sha256((OWNER/"targeted.py").read_bytes()).hexdigest())
        if not valid:
            shard=RUN/"targeted-retry-budget120"/kind
            arguments=[OWNER/"targeted.py","--ship","feeder_1000","--level","light",
                "--output",shard,"--prepared",OUT,"--samples",samples,"--seed",seed,
                "--mass-objective",objective,"--budget-seconds","1200"]
            if pairs:arguments += ["--tank-pairs",*pairs]
            run("seawater-retry120-targeted-"+kind,arguments,
                env_extra={"STOWAGE_STABILITY_TIME_BUDGET_S":"120"})
            source=shard/"feeder_1000/train-light.json"
        destination=OUT/"feeder_1000"/("targeted-"+kind+"-light.json")
        if destination.exists():
            if destination.read_bytes()!=source.read_bytes():raise ValueError("conflicting targeted evidence")
        else:shutil.copyfile(source,destination)
        sources.append(dict(kind=kind,source=str(source),sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
    for ship,name in populations():
        old=json.loads((OWNER/"production"/ship/name).read_text())
        record=json.loads((OUT/ship/name).read_text())
        assert record["public_crosscheck_completed"] and record["physical_inputs_unchanged"]
        assert all(old[key]==record[key] for key in ("phase","level","samples","seed"))
    for ship in campaign.SHIPS:
        run("seawater-retry120-equivalence-"+ship,[RUN/"campaign.py","equivalence-worker",ship])
    result=dict(passed=True,retry_budget_seconds=120,retried_cases=missing,targeted_sources=sources,
        seconds=time.monotonic()-started,original_failures_preserved=True,
        recovery_reason="fresh-process valid catalogue; missing/aborted cases plus original public checker timeouts")
    campaign.write(LOGS/"complete.json",result)
    campaign.write(RUN/"ordinary-logs/campaign.execution.json",result)


if __name__=="__main__":main()
