"""Freeze newer, tare-valid accepted D58 medium backgrounds and witnesses.

No fresh generator/finisher acceptance is claimed. Saved physical models are
replayed through current production stability definitions in the new probes.
"""
from followup2_common import ROOT, OUT, save, sha, source_hashes
from feeder_envelope import manual_instance
from stowage.model import Instance, MovePlan
from stowage.vessel.fleet import load_ship_params
from stowage.checker.full import departure_condition
from stowage.generate.background_acceptance import background_instance

before=source_hashes()
for name in ('feeder_500','feeder_800','feeder_1700'):
    save(OUT/f'{name}-params.json',load_ship_params(name))
    for label in (('C1','C2') if name=='feeder_500' else ('C1',)):
        instance,_,recipe=manual_instance(name,label)
        save(OUT/f'{name}-{label}-input.json',dict(instance=instance.model_dump(mode='json'),recipe=recipe))
    stem=ROOT/f'experiments/background-1028/review-fixes/retry-random-{name}-seed0'
    ipath=stem.with_suffix('.instance.json')
    ppath=stem.with_suffix('.witness.plan.json')
    rpath=stem.with_suffix('.witness.report.json')
    original=Instance.model_validate_json(ipath.read_text());instance=background_instance(original)
    plan=MovePlan.model_validate_json(ppath.read_text());assert plan.moves==[]
    departure=departure_condition(instance,plan)
    instance=instance.model_copy(update={'onboard':departure})
    # Never substitute the booked initial ballast for the accepted empty-region witness.
    save(OUT/f'{name}-D58-input.json',dict(instance=instance.model_dump(mode='json'),recipe=dict(
        source_instance=str(ipath.relative_to(ROOT)),source_plan=str(ppath.relative_to(ROOT)),
        source_report=str(rpath.relative_to(ROOT)),source_instance_sha256=sha(ipath),
        source_plan_sha256=sha(ppath),source_report_sha256=sha(rpath),
        background_set='D58 review-fix medium seed0, saved accepted witness; not current regeneration',empty_plan=True)))
    print(name,len(instance.vessel.fixed_loads),sum(departure.tank_masses_kg.values()),flush=True)
after=source_hashes();save(OUT/'input-freeze-source-audit.json',dict(before=before,after=after,stable=before==after))
assert before==after
