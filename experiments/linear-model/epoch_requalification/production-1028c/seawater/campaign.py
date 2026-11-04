"""Fresh seawater qualification, using only the central admitted compute pool."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time

RUN=Path(__file__).resolve().parent
CENTRAL=RUN.parent
OWNER=CENTRAL.parent
ROOT=OWNER.parents[2]
ORIGINAL=OWNER/"production-1028b"
HISTORICAL=OWNER.parent/"fix-t3-linear"
SHIPS=("feeder_500","feeder_1000","feeder_1700")
OUT=RUN/"ordinary"
LOGS=RUN/"campaign-logs"


def check_abort():
    if (CENTRAL/"ABORT").exists(): raise RuntimeError("central ABORT marker: no additional launches")


def load_driver():
    spec=importlib.util.spec_from_file_location("seawater_owner_driver",OWNER/"driver.py")
    driver=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    return driver


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(value,indent=2)+"\n")
    temporary.replace(path)


def job(name,args):
    check_abort()
    sys.path.insert(0,str(CENTRAL))
    from compute import run
    LOGS.mkdir(exist_ok=True)
    path=LOGS/(name+".execution.json")
    if path.exists():
        prior=json.loads(path.read_text())
        if prior.get("passed") is True: return
        raise ValueError("preserve incomplete/failed stage: "+str(path))
    began=time.monotonic()
    record=dict(name=name,arguments=list(map(str,args)),started_unix=time.time(),controller_pid=os.getpid())
    write(path,record)
    try:
        run("seawater-"+name,args)
    except BaseException as error:
        record.update(passed=False,seconds=time.monotonic()-began,error=repr(error))
        write(path,record)
        raise
    record.update(passed=True,seconds=time.monotonic()-began)
    write(path,record)
    print(json.dumps(record),flush=True)


def wait_for(*paths):
    began=time.monotonic()
    while not all(path.exists() for path in paths):
        check_abort()
        if time.monotonic()-began>7200: raise TimeoutError("required fresh evidence unavailable")
        time.sleep(2)
    check_abort()


def physical_worker(ship,name):
    check_abort()
    driver=load_driver()
    prepared=driver.prepare(ship,OUT)
    previous=json.loads((OWNER/"production"/ship/name).read_text())
    shard=RUN/"ordinary-shards"/ship/name.removesuffix(".json")
    record=driver.physical_case(ship,previous["level"],previous["phase"],shard,prepared,
        samples=previous["samples"],seed=previous["seed"],public=True)
    driver.write(OUT/ship/name,record)


def equivalence_worker(ship):
    check_abort()
    spec=importlib.util.spec_from_file_location("seawater_promoter",OWNER/"promote.py")
    promoter=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(promoter)
    raw=json.loads((OUT/ship/"raw.json").read_text())
    promoter.verify_equivalence(ship,OUT/ship,raw)


def ordinary():
    began=time.monotonic()
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures=[pool.submit(job,"raw-"+ship,[OWNER/"driver.py","--ship",ship,
            "--output",OUT,"--phase","raw","--calibration-samples","192"]) for ship in SHIPS]
        for future in as_completed(futures): future.result()
        write(LOGS/"raw-complete.json",dict(seconds=time.monotonic()-began))
        populations=[]
        for ship in SHIPS:
            for path in sorted((OWNER/"production"/ship).glob("*.json")):
                if path.name.startswith(("train-","holdout-","enrichment")):
                    populations.append((ship,path))
        populations.sort(key=lambda row:(row[1].name!="train-light.json",row[0],row[1].name))
        jobs=[pool.submit(job,ship+"-"+path.stem,[Path(__file__),"physical-worker",ship,path.name])
            for ship,path in populations]
        for ship in SHIPS:
            jobs.append(pool.submit(job,"boundary-"+ship,[OWNER/"boundary.py","--ship",ship,
                "--output",OUT,"--samples","64","--holdout-samples","64"]))
        for kind,seed,samples,objective,pairs in (
            ("min",3821,64,"min",()),("grid",3822,128,"grid",()),
            ("pairs-3823",3823,256,"grid",("DB-M","HEEL","WING-A")),
            ("pairs-3824",3824,256,"grid",("DB-M","HEEL","WING-A"))):
            arguments=[OWNER/"targeted.py","--ship","feeder_1000","--level","light",
                "--output",RUN/"targeted"/kind,"--prepared",OUT,"--samples",samples,
                "--seed",seed,"--mass-objective",objective,"--budget-seconds","1200"]
            if pairs: arguments += ["--tank-pairs",*pairs]
            jobs.append(pool.submit(job,"targeted-"+kind,arguments))
        for future in as_completed(jobs): future.result()
    for kind in ("min","grid","pairs-3823","pairs-3824"):
        source=RUN/"targeted"/kind/"feeder_1000/train-light.json"
        shutil.copyfile(source,OUT/"feeder_1000"/("targeted-"+kind+"-light.json"))
    for ship in SHIPS:
        job("equivalence-"+ship,[Path(__file__),"equivalence-worker",ship])
    write(RUN/"ordinary-logs/campaign.execution.json",dict(passed=True,seconds=time.monotonic()-began))


def ship_edges(ship):
    for version in (1,2):
        script=OWNER/("edge_grid.py" if version==1 else "edge_grid_v2.py")
        for phase in ("train","holdout"):
            wait_for(OUT/ship/(phase+"-light.json"))
            job(f"{ship}-v{version}-{phase}",[script,"--ship",ship,"--phase",phase,
                "--prepared",OUT,"--output",RUN/f"edges-v{version}"])
    job(ship+"-v3-lattice",[OWNER/"edge_grid_v3.py","--ship",ship,"--phase","lattice",
        "--prepared",OUT,"--output",RUN/"edges-v3","--shard-count","1"])
    job(ship+"-v3-finalize",[OWNER/"edge_grid_v3.py","--ship",ship,"--phase","finalize",
        "--prepared",OUT,"--output",RUN/"edges-v3","--shard-count","1",
        "--prior-training-root",RUN/"edges-v2"])


def edges():
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(ship_edges,ship) for ship in SHIPS]): future.result()
    write(RUN/"edges-execution.json",dict(passed=True))


def diagnostic_worker(name):
    """Redirect artifact locations only; execute unchanged numerical producer."""
    check_abort()
    source=ORIGINAL/(name+".py")
    original=source.read_text()
    redirected=original.replace("HERE = Path(__file__).resolve().parent","HERE = Path("+repr(str(RUN))+")")
    redirected=redirected.replace("HERE=Path(__file__).resolve().parent","HERE=Path("+repr(str(RUN))+")")
    redirected=redirected.replace("ROOT = HERE.parents[3]","ROOT = Path("+repr(str(ROOT))+")")
    redirected=redirected.replace("ROOT=HERE.parents[3]","ROOT=Path("+repr(str(ROOT))+")")
    for filename in ("driver.py","edge_grid.py"):
        for expression in ("HERE.parent/'"+filename+"'",'HERE.parent/"'+filename+'"'):
            redirected=redirected.replace(expression,"Path("+repr(str(OWNER/filename))+")")
    if name=="claude-freeze-review-v2":
        sys.argv=[str(source),"--source-state",str(HISTORICAL/"claude-edge-seed51/state-011.json"),
            "--frozen-instance",str(HISTORICAL/"claude-review/feeder_500/frozen-instance.json"),
            "--prepared",str(OUT),"--output",str(RUN/"claude-review-v2")]
    exec(compile(redirected,str(source),"exec"),dict(__file__=str(source),__name__="seawater_diagnostic"))


def diagnostics():
    wait_for(OUT/"feeder_500/raw.json",OUT/"feeder_500/train-light.json")
    job("diagnostic-review",[Path(__file__),"diagnostic-worker","claude-freeze-review-v2"])
    source=RUN/"claude-review-v2/feeder_500/necessary-review.json"
    target=RUN/"edges-v3/feeder_500/necessary-review.json"
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists() and target.read_bytes()!=source.read_bytes(): raise ValueError("conflicting reviewer artifact")
    if not target.exists(): shutil.copyfile(source,target)
    wait_for(OUT/"feeder_1700/raw.json")
    for name in ("claude-edge-cause","claude-face-pilot","claude-face-budget-pilot"):
        job("diagnostic-"+name,[Path(__file__),"diagnostic-worker",name])
    write(RUN/"diagnostics-execution.json",dict(passed=True,water_density_kg_m3=1025.,
        transformation="storage/import paths redirected only; original immutable producer numerical design unchanged"))


def main():
    LOGS.mkdir(exist_ok=True)
    phase=sys.argv[1] if len(sys.argv)>1 else "all"
    if phase=="physical-worker": physical_worker(sys.argv[2],sys.argv[3]);return
    if phase=="equivalence-worker": equivalence_worker(sys.argv[2]);return
    if phase=="diagnostic-worker": diagnostic_worker(sys.argv[2]);return
    if phase not in ("all","ordinary","edges","diagnostics","equivalence"): raise ValueError("unknown phase")
    write(LOGS/("controller-"+phase+".json"),dict(pid=os.getpid(),phase=phase))
    began=time.monotonic()
    if phase=="all":
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures=[pool.submit(function) for function in (ordinary,edges,diagnostics)]
            for future in futures: future.result()
    elif phase=="equivalence":
        for ship in SHIPS: job("equivalence-"+ship,[Path(__file__),"equivalence-worker",ship])
    else: {"ordinary":ordinary,"edges":edges,"diagnostics":diagnostics}[phase]()
    write(RUN/(phase+"-execution.json"),dict(passed=True,seconds=time.monotonic()-began))


if __name__=="__main__": main()
