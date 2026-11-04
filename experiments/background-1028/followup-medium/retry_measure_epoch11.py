"""Observe unchanged production retry trajectories separately from raw draws.

Normal cargo profiles, C1 and exact D58 gate remain enabled. Existing producers
may resample individually impossible DG shipments; no shipment fact is supplied
by this observer. max_attempts=200 is an upper bound, not a relaxed screen.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import sys
import time

import measure as audit_measure
from stowage.generate import background_acceptance as gate
from stowage.generate import instances as simulated
from stowage.generate import random_yard as random
from stowage.checker import check
from stowage.model import BallastCondition, MovePlan
from stowage.vessel.fleet_conditions import departure_tank_masses

HERE = Path(__file__).resolve().parent


def sources():
    _, manifest = audit_measure.sources()
    manifest['sources'][str(Path(__file__).relative_to(audit_measure.ROOT))] = sha256(Path(__file__).read_bytes()).hexdigest()
    key = sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    target = HERE/'sources'/f'{key}.json'
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(manifest, sort_keys=True, indent=2)+'\n')
    return key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=HERE/'retry-trajectories.jsonl')
    parser.add_argument('--generators', nargs='+', choices=['random','simulated'], default=['random','simulated'])
    parser.add_argument('--vessels', nargs='+', default=['feeder_500','feeder_800','feeder_1700'])
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--max-attempts', type=int, default=200)
    parser.add_argument('--deadline-utc', default='2026-10-28T18:15:00+00:00')
    args = parser.parse_args()
    deadline = datetime.fromisoformat(args.deadline_utc).timestamp()
    original = gate.evaluate_background
    calls = []
    def observed(instance, **kwargs):
        if time.time() >= deadline:
            raise TimeoutError('retry experiment deadline reached at candidate boundary')
        result = original(instance, **kwargs)
        calls.append(dict(result=asdict(result), mass_sizing_audit=audit_measure.mass_audit(instance,result)))
        return result
    gate.evaluate_background = observed
    completed = {(row['generator'],row['vessel'],row['seed']) for row in (
        json.loads(line) for line in args.out.read_text().splitlines())} if args.out.exists() else set()
    print(json.dumps(dict(pid=os.getpid(),started_utc=datetime.now(timezone.utc).isoformat())),flush=True)
    for vessel in args.vessels:
        for generator in args.generators:
            if (generator,vessel,args.seed) in completed:
                continue
            if time.time() >= deadline:
                return
            calls.clear()
            report = random.RandomGenerationReport() if generator=='random' else simulated.GenerationReport()
            row = dict(generator=generator,vessel=vessel,rung='medium',seed=args.seed,
                       measurement='production_retry_trajectory',max_attempts=args.max_attempts,
                       source_start=sources(),pid=os.getpid(),screens=dict(C1=True,C2=False,background_finisher_full_checker=True))
            started=time.perf_counter()
            try:
                function = random.generate_random_instance if generator=='random' else simulated.generate_simulated_instance
                instance = function('medium',seed=args.seed,vessel_name=vessel,
                                    max_attempts=args.max_attempts,report=report)
                row.update(accepted=True,emitted_instance_id=instance.id,
                    emitted_mass_sizing_audit=audit_measure.mass_audit(instance),
                    emitted_booking_tanks_preserved=instance.onboard.tank_masses_kg==departure_tank_masses(instance.vessel))
                prefix=HERE/f'retry-{generator}-{vessel}-seed{args.seed}'
                prefix.with_suffix('.instance.json').write_text(instance.model_dump_json())
                assert calls and calls[-1]['result']['accepted']
                witness=calls[-1]['result']['witness_tank_masses_kg']
                adjustable={tank.id for tank in instance.vessel.tanks if tank.adjustable}
                background=gate.background_instance(instance)
                plan=MovePlan(id='retry-empty-witness',instance_id=background.id,
                    call_index=background.call_index,moves=[],ballast=BallastCondition(source='finisher',
                    tank_masses_kg={key:value for key,value in witness.items() if key in adjustable}))
                checked=check(background,plan)
                prefix.with_suffix('.witness.plan.json').write_text(plan.model_dump_json())
                prefix.with_suffix('.witness.report.json').write_text(checked.model_dump_json())
                row.update(emitted_background_replay_passed=checked.passed,
                    emitted_background_all_parts_ran=all(p.ran and not p.error and not p.violations for p in checked.parts.values()))
                assert checked.passed,checked.violations
                assert row['emitted_booking_tanks_preserved']
                assert row['emitted_mass_sizing_audit']['full_booking_initial_mass_legal']
            except Exception as exc:
                row.update(accepted=False,error=f'{type(exc).__name__}: {exc}')
            usage=resource.getrusage(resource.RUSAGE_SELF)
            row.update(wall_s=time.perf_counter()-started,report=asdict(report),gate_calls=list(calls),
                gate_seconds=sum(call['result']['elapsed_s'] for call in calls),
                source_end=sources(),peak_self_RSS_MiB=usage.ru_maxrss/(1024**2 if sys.platform=='darwin' else 1024))
            with args.out.open('a') as stream:
                stream.write(json.dumps(row,sort_keys=True,default=str)+'\n')
                stream.flush()
            print(generator,vessel,'accept' if row['accepted'] else 'reject',
                  f"{row['wall_s']:.2f}s",'attempts',report.attempts,
                  'gate_s',f"{row['gate_seconds']:.2f}",row.get('error',''),flush=True)


if __name__=='__main__':
    main()
