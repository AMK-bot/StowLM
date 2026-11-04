"""Fresh reviewer density contrast and unchanged controlled/face diagnostics.

Only diagnostic storage paths are redirected when executing the immutable
controlled/face producers. The freshwater adapter selects the physical input
profile; it never modifies an exact root, acceptance test, margin, or seed.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import time

RUN=Path(__file__).resolve().parent
OWNER=RUN.parent.parent
BASELINE=OWNER/"production-1028b"
HISTORICAL=OWNER.parent/"fix-t3-linear"
ROOT=RUN.parents[4]


def pin(path):
    path=Path(path).resolve()
    return dict(path=str(path.relative_to(ROOT)),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(value,indent=2,allow_nan=False)+"\n")
    temporary.replace(path)


def replay_review(density,baseline_prepared):
    import numpy as np
    from stowage.linear.physics import exact_condition,_linear_arrays,_prepared_params_for
    from stowage.linear.reusable import assemble_instance,coefficient_family
    from stowage.linear.family_calibration import _validated_errors,_validate_calibration_certificate,domain_for
    from stowage.linear.cache import family_calibration_key,response_calibration_identity
    from stowage.model import FixedLoad,ShipState,Instance
    if density==1000:
        from profile_run import load_driver,adapter_pin
        driver=load_driver()
        profile=dict(water_density_kg_m3=1000.,adapter=adapter_pin())
        prepared=RUN/"ordinary"
        output=RUN/"claude-review-v2"
    else:
        spec=importlib.util.spec_from_file_location("baseline_review_driver",OWNER/"driver.py")
        driver=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(driver)
        profile=dict(water_density_kg_m3=1025.,fixture="unchanged original reviewer density")
        prepared=baseline_prepared
        output=RUN/"claude-review-density-1025"
    started=time.monotonic()
    source_path=HISTORICAL/"claude-edge-seed51/state-011.json"
    original_fixture=HISTORICAL/"claude-review/feeder_500/frozen-instance.json"
    source_pin,fixture_pin=pin(source_path),pin(original_fixture)
    original=json.loads(source_path.read_text())
    fixture=json.loads(original_fixture.read_text())
    assert fixture["port"]["water_density_kg_m3"]==1025.
    fixture["port"]["water_density_kg_m3"]=float(density)
    fixture["yard"]["stacks"]=[]
    fixture["yard"]["quay_buffer"]=[]
    fixture_path=output/"feeder_500/source-fixture.json"
    if fixture_path.exists():
        if json.loads(fixture_path.read_text())!=fixture: raise ValueError("conflicting immutable reviewer fixture")
    else:
        write(fixture_path,fixture)
    instance=Instance.model_validate(fixture)
    _,parameters=_prepared_params_for(instance)
    family=coefficient_family(instance,parameters)
    before=driver.snapshot("feeder_500",parameters)
    raw=json.loads((prepared/"feeder_500/raw.json").read_text())
    assert before==raw["physical_inputs"]
    diagnostics=raw["diagnostics"]
    assert diagnostics["coefficient_family_cache_key"]==family.cache_key
    assert diagnostics["response_calibration_identity"]==response_calibration_identity(family)
    key=family_calibration_key(family,before["reference"],domain_for(family,instance),samples=192)
    assert key==raw["key"]
    errors=_validated_errors(raw["errors"],len(family.template.station_x_m))
    _validate_calibration_certificate(diagnostics["calibration_certificate"],key,errors,diagnostics)
    tanks=sorted(instance.vessel.tanks,key=lambda tank:tank.id)
    assert [tank.id for tank in tanks]==original["tank_ids"]
    values=np.asarray(original["all_tank_masses_kg"])
    point={key:original["point_load"][key] for key in ("x_m","y_m","z_m","mass_kg")}
    onboard=ShipState(vessel_id=instance.vessel.id,tank_masses_kg={tank.id:float(value)
        for tank,value in zip(tanks,values,strict=True) if not tank.adjustable})
    state=instance.model_copy(update={"containers":[],"load_list":[],"discharge_list":[],
        "onboard":onboard,"vessel":instance.vessel.model_copy(update={"fixed_loads":[FixedLoad(**point)]})})
    model=assemble_instance(family,state,family.template.band,errors=errors,
        diagnostics={"calibration_synthetic":True,"family_domain":diagnostics["family_domain"]})
    cells=np.zeros(len(model.cell_ids));ballast=values*model.tank_adjustable
    exact=exact_condition(model,state,cells,ballast,cell_vcg_m=model.cell_vcg_m)
    replay=exact_condition(model,state,cells.copy(),ballast.copy(),cell_vcg_m=model.cell_vcg_m.copy())
    affine=_linear_arrays(model,cells,ballast,instance=state,solver=True)
    differences,replay_differences={},{}
    for name in ("gm_m","trim_m","list_rad","draught_m","sf_n","bm_nm"):
        differences[name]=float(np.max(np.abs(np.asarray(exact[name])-original["exact"][name])))
        replay_differences[name]=float(np.max(np.abs(np.asarray(exact[name])-replay[name])))
        np.testing.assert_allclose(exact[name],replay[name],rtol=1e-9,atol=1e-5)
    domain,band=diagnostics["family_domain"],model.band
    admitted=bool(domain["fixed_x_min_m"]<=point["x_m"]<=domain["fixed_x_max_m"]
        and abs(point["y_m"])<=domain["fixed_y_abs_max_m"]
        and domain["kg_min_m"]<=point["z_m"]<=domain["kg_max_m"]
        and band.displacement_min_kg<=exact["displacement_kg"]<=band.displacement_max_kg
        and band.draught_min_m<=exact["draught_m"]<=band.draught_max_m
        and abs(exact["trim_m"])<=band.max_abs_trim_m and abs(exact["list_rad"])<=band.max_abs_heel_rad)
    if not admitted and density != 1000:
        raise ValueError("unchanged reviewer point is outside its original admitted family domain")
    if density == 1000:
        from necessary_adapter import validate_exclusion
        from types import SimpleNamespace
        assert validate_exclusion(SimpleNamespace(read=lambda path:json.loads(path.read_text())), RUN/"edges-v3/feeder_500", raw, family.template)
        assert not admitted, "known freshwater reviewer exclusion unexpectedly changed"
    row=dict(state_id=f"review-density-{density}-"+digest(point)[:16],admitted=admitted,qualified_domain=admitted,
        fixed_point=point,mass_kg=float(exact["displacement_kg"]),
        affine_restoring_kgm=float(exact["displacement_kg"]*affine["gm_m"]),
        transverse_moment_kgm=float(model.const_moment_y_kgm+model.tank_tcg_m@ballast),
        cell_masses_kg=cells.tolist(),tank_masses_kg=ballast.tolist(),all_tank_masses_kg=values.tolist(),
        cell_vcg_m=model.cell_vcg_m.tolist())
    for name in ("gm_m","trim_m","list_rad","draught_m","sf_n","bm_nm"):
        row["exact_"+name]=np.asarray(exact[name]).tolist()
        row["affine_"+name]=np.asarray(affine[name]).tolist()
    source_script=pin(__file__)
    config=dict(ship="feeder_500",level="family",phase="train",seed=54939,samples=1,
        construction="fresh exact replay of immutable reviewer point/tanks at explicit water density",
        source_observation=source_pin,affine_feasibility_filter=False,required_strata=["review-regression"],
        frozen_full_instance=pin(fixture_path),original_full_instance=fixture_pin,
        qualification_fixture_profile=profile,
        fixture_transformation="set explicit port water density; clear orphaned yard stacks/quay buffer after original synthetic fixture removed decision containers; no point/tank changes")
    identity=dict(physical_inputs=before,config=config,builder_version="1.0",source_sha256=source_script["sha256"])
    after=driver.snapshot("feeder_500",family.parameters)
    assert before==after and pin(source_path)==source_pin and pin(original_fixture)==fixture_pin
    record=dict(format=1,kind="necessary-domain-edges-v1",ship="feeder_500",level="family",phase="train",
        role="historical-out-of-domain-diagnostic",seed=54939,complete=True,samples=0,attempts=1,states=[row],list_row_samples=[],
        physical_inputs_start=before,physical_inputs_end=after,physical_inputs_unchanged=True,band=asdict(band),
        domain=domain,station_x_m=model.station_x_m.tolist(),calibration=diagnostics,
        sampler_builder_version="1.0",sampler_source_sha256=source_script["sha256"],sampler_source=source_script,
        sampler_config=config,sampler_inputs_hash=digest(identity),qualification_fixture_profile=profile,
        reference_transfer_source=pin(prepared/"feeder_500/train-light.json"),
        coverage=dict(required_strata=["review-regression"],strata={"review-regression":dict(requested=1,evaluated=1,retained=1)}),
        original_exact_replay_max_differences=differences,independent_same_density_replay_max_differences=replay_differences,
        old_reserves=original["old_reserves"],original_probe_pin=source_pin,seconds=time.monotonic()-started,
        warning="Training regression density contrast only; original1025 equality is not a freshwater requirement; no independent holdout/full-plan claim")
    path=output/"feeder_500/necessary-review.json"
    if path.exists(): raise ValueError("preserve existing reviewer observation")
    write(path,driver.serializable(record))
    write(output/"feeder_500/frozen-instance.json",state.model_dump(mode="json"))
    write(output/"feeder_500/frozen-parameters.json",driver.serializable(family.parameters))
    write(output/"feeder_500/frozen-numeric-model.json",driver.serializable(asdict(model)))
    print(json.dumps(dict(density=density,admitted=admitted,seconds=record["seconds"],
        exclusion="unchanged historical training point outside freshwater displacement band",
        cross_density_max_differences=differences)),flush=True)


def original_diagnostic(name):
    import numpy as np
    from profile_run import install,adapter_pin
    install()
    source=RUN/(name+".py")
    original=source.read_text()
    marker="HERE=Path(__file__).resolve().parent"
    if original.count(marker)!=1: raise ValueError("diagnostic storage redirect marker changed")
    redirected=original
    before=pin(source)
    namespace={"__file__":str(source),"__name__":"freshwater_"+name.replace("-","_")}
    exec(compile(redirected,str(source),"exec"),namespace)
    assert pin(source)==before
    relative=Path("claude-edge-cause.json") if name=="claude-edge-cause" else Path(name+"-artifacts")/"summary.json"
    fresh_path,historical_path=RUN/relative,BASELINE/relative
    fresh,historical=json.loads(fresh_path.read_text()),json.loads(historical_path.read_text())
    if len(fresh["states"])!=len(historical["states"]): raise ValueError("diagnostic request population changed")
    contrasts=[]
    for index,(new,old) in enumerate(zip(fresh["states"],historical["states"],strict=True)):
        deltas={}
        for quantity in ("gm_m","trim_m","list_rad","draught_m","sf_n","bm_nm"):
            key="exact_"+quantity
            if key in new and key in old:
                delta=np.asarray(new[key])-np.asarray(old[key])
                deltas[quantity]=dict(minimum=float(np.min(delta)),maximum=float(np.max(delta)),max_absolute=float(np.max(np.abs(delta))))
        contrasts.append(dict(index=index,fresh_state_id=new["state_id"],historical_state_id=old["state_id"],
            fresh_admitted=new["admitted"],historical_admitted=old["admitted"],exact_cross_density_deltas=deltas,
            request_fields_differing=sorted(key for key in set(new["request"])|set(old["request"])
                if new["request"].get(key)!=old["request"].get(key))))
    write(RUN/"diagnostic-logs"/(name+".profile.json"),dict(
        producer=before,controller=pin(__file__),qualification_fixture_profile=dict(water_density_kg_m3=1000.,adapter=adapter_pin()),
        nonnumeric_transformation="redirect HERE storage root only; original requests/physics/acceptance tests unchanged",
        fresh_observation=pin(fresh_path),historical_observation=pin(historical_path),
        compared_historical_density=1025.,historical_numbers_reused=False,cross_density_contrasts=contrasts,
        interpretation="same diagnostic algorithm/prescribed population; declared physical-profile changes may change realized request vectors and admission"))


    # Bind the unchanged captured requests as diagnostic-only edge observations.
    # This does not fit reserves or create additional independent holdouts.
    ship = "feeder_500" if name == "claude-edge-cause" else "feeder_1700"
    raw = json.loads((RUN/"ordinary"/ship/"raw.json").read_text())
    family = namespace["family"]
    admitted = [row for row in fresh["states"] if row["admitted"]]
    diagnostic = dict(format=1, kind="necessary-domain-edges-v1", ship=ship,
        phase="diagnostic", role="prescribed diagnostic; never independent holdout or calibration enrichment",
        seed=0, complete=True, samples=len(admitted), attempts=len(fresh["states"]),
        states=fresh["states"], list_row_samples=admitted,
        physical_inputs_start=fresh["physical_inputs_start"], physical_inputs_end=fresh["physical_inputs_end"],
        physical_inputs_unchanged=fresh["physical_inputs_start"] == fresh["physical_inputs_end"],
        calibration=raw["diagnostics"], station_x_m=family.template.station_x_m.tolist(),
        reference_transfer_source=pin(RUN/"ordinary"/ship/"train-light.json"),
        qualification_fixture_profile=dict(water_density_kg_m3=1000., adapter=adapter_pin()),
        diagnostic_capture=pin(fresh_path), diagnostic_producer=before, diagnostic_controller=pin(__file__),
        coverage=dict(required_strata=["prescribed-diagnostic"], strata={"prescribed-diagnostic":
            dict(requested=len(fresh["states"]), evaluated=len(fresh["states"]), retained=len(admitted))}))
    write(RUN/"diagnostic-edges"/(name+".json"), diagnostic)


def wait_ready(prepared,ship="feeder_500"):
    from ordinary_campaign import abort_check
    start=time.monotonic()
    while not all((prepared/ship/name).exists() for name in ("raw.json","train-light.json")):
        if time.monotonic()-start>7200: raise TimeoutError("fresh diagnostic preparation unavailable")
        abort_check()
        time.sleep(3)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase",choices=("review","pilots","all"),default="all")
    parser.add_argument("--baseline-prepared",type=Path,default=BASELINE/"ordinary")
    parser.add_argument("--worker",choices=("review-1000","review-1025","claude-edge-cause","claude-face-pilot","claude-face-budget-pilot"))
    args=parser.parse_args()
    if args.worker:
        if args.worker.startswith("review-"):
            from necessary_adapter import main as exclusion_certificate
            exclusion_certificate()
            replay_review(int(args.worker.split("-")[1]),args.baseline_prepared)
        else: original_diagnostic(args.worker)
        return
    from ordinary_campaign import run
    if args.phase in ("review","all"):
        wait_ready(RUN/"ordinary")
        run("diagnostic-review-1000",[Path(__file__),"--worker","review-1000"])
    if args.phase in ("pilots","all"):
        wait_ready(RUN/"ordinary")
        for name in ("claude-edge-cause","claude-face-pilot","claude-face-budget-pilot"):
            wait_ready(RUN/"ordinary", "feeder_500" if name=="claude-edge-cause" else "feeder_1700")
            run("diagnostic-"+name,[Path(__file__),"--worker",name])


if __name__=="__main__": main()
