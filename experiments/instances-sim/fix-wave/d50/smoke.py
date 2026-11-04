"""D50 real-hull input/empty-plan checker smoke; one instance per family."""
from argparse import ArgumentParser
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from random import Random
from time import perf_counter

from stowage.checker import check, report_matches_inputs
from stowage.generate import instances
from stowage.io import load_instance, save_instance, save_plan
from stowage.model import MovePlan, CheckReport
from stowage.params import load_param_file
from stowage.ports.profiles import list_ports, load_port
from stowage.vessel.fleet_conditions import CONDITION_VERSION

OUTPUT = Path(__file__).resolve().parent
VESSELS = ('feeder_500', 'feeder_800', 'feeder_1700')
SIZES = ('tiny', 'small', 'medium')


def write(path, payload):
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + '\n')


def fingerprint():
    files = {*Path('src/stowage').rglob('*.py'), *Path('params').rglob('*.toml')}
    files.update(Path('src/stowage').rglob('*.toml'))
    return {str(path): sha256(path.read_bytes()).hexdigest() for path in sorted(files)}


def selected_route(config):
    # Reproduce the generator's declared route stream, without drawing cargo or
    # changing its route policy. Smoke selection is explicit in each audit.
    controls = dict(size=config['size'], seed=config['seed'], noise=repr(float(config['noise'])),
                    vessel=config['vessel_name'], port=config['port_name'], n_boxes=None,
                    without_dg=True, dense_20ft=False, tiny_dg_enabled=True)
    identity = sha256(json.dumps([instances._STREAM_NAMESPACE, controls], sort_keys=True,
                                separators=(',', ':')).encode()).hexdigest()
    params = load_param_file('cargo', 'feeder_export')
    return Random(instances._stream_seed(identity, 'route')).choices(params['rotations'],
        weights=[r['share']['value'] for r in params['rotations']], k=1)[0]


def choose_config(vessel, size):
    profiles = {load_port(name).locode for name in list_ports()}
    preflight = []
    for seed in (1, 0, *range(2, 21)):
        config = dict(size=size, vessel_name=vessel, port_name='laem_chabang_like',
                      seed=seed, noise=.2, without_dg=True, max_attempts=3)
        route = selected_route(config)
        locode = route['port_calls'][1]
        preflight.append(dict(seed=seed, route=route['id'], next_locode=locode,
                              profile_available=locode in profiles))
        if locode in profiles:
            return config, preflight
    raise ValueError(f'no supported next-port smoke specification in bounded seed search: {vessel}/{size}')


def part_summary(part):
    return dict(ran=part.ran, error=part.error,
                hard_rule_ids=sorted({v.rule_id for v in part.violations if v.severity == 'hard'}))


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=9)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.limit <= 9:
        parser.error('limit must be 1..9')
    records = []
    started = perf_counter()
    epoch = fingerprint()
    for vessel, size in [(v, s) for v in VESSELS for s in SIZES][:args.limit]:
        config, preflight = choose_config(vessel, size)
        stem = f'{vessel}-{size}'
        audit_path = OUTPUT / f'{stem}.audit.json'
        if fingerprint() != epoch:
            raise RuntimeError('source changed between shards; restart instead of mixing epochs')
        if args.resume and audit_path.exists():
            record = json.loads(audit_path.read_text())
            if record['config'] != config or record['sources'] != epoch or record['source_drift']:
                raise ValueError(f'resume source/config mismatch: {audit_path}')
            for key, digest_key in (('instance', 'instance_sha256'), ('plan', 'plan_sha256'),
                                    ('report', 'report_sha256')):
                if key in record and sha256((OUTPUT/record[key]).read_bytes()).hexdigest() != record[digest_key]:
                    raise ValueError(f'resume checksum mismatch: {audit_path}, {key}')
            records.append(record)
            print(f'{vessel}/{size}: resumed {record["status"]}', flush=True)
            continue
        report = instances.GenerationReport()
        shard_start = perf_counter()
        record = dict(config=config, seed_selection=preflight, sources=epoch,
                      started_at=datetime.now(timezone.utc).isoformat(), c2_screened=False)
        try:
            instance = instances.generate_simulated_instance(**config, report=report)
        except ValueError as error:
            if not str(error).startswith(('screening exhausted', 'voyage next-call environment unavailable')):
                raise
            record.update(status='generation_rejected', generation_error=str(error), voyage_ran=False)
        else:
            assert instance.voyage is not None and instance.vessel.hull is not None
            assert instance.rotation.service == selected_route(config)['id']
            path = save_instance(instance, OUTPUT/f'{stem}.json')
            assert load_instance(path) == instance
            plan = MovePlan(id=f'{instance.id}.empty-d50-smoke', instance_id=instance.id,
                            call_index=instance.call_index, provenance=instance.provenance.model_copy(deep=True))
            plan_path = save_plan(plan, OUTPUT/f'{stem}.plan.json')
            checking = perf_counter()
            checked = check(instance, plan)
            checker_s = perf_counter()-checking
            assert report_matches_inputs(checked, instance, plan)
            report_path = OUTPUT/f'{stem}.report.json'
            report_path.write_text(checked.model_dump_json(indent=2)+'\n')
            assert CheckReport.model_validate_json(report_path.read_text()) == checked
            conditions = {f'condition:{identity.id}:{name}': part_summary(
                checked.parts[f'condition:{identity.id}:{name}'])
                for identity in (instance.voyage.departure, instance.voyage.arrival)
                for name in ('stability', 'strength')}
            record.update(status='checked', instance=path.name,
                instance_sha256=sha256(path.read_bytes()).hexdigest(), plan=plan_path.name,
                plan_sha256=sha256(plan_path.read_bytes()).hexdigest(), report=report_path.name,
                report_sha256=sha256(report_path.read_bytes()).hexdigest(),
                n_boxes=len(instance.load_list), json_roundtrip=True, report_roundtrip=True,
                plan_kind='empty diagnostic; no cargo loaded, no ballast repair',
                full_checker_passed=checked.passed, shut_out_count=len(checked.shut_out),
                voyage=part_summary(checked.parts['voyage']), voyage_ran=checked.parts['voyage'].ran,
                conditions=conditions, hard_rule_ids=sorted({v.rule_id for v in checked.violations if v.severity=='hard'}),
                checker_s=checker_s)
        after = fingerprint()
        record.update(generation_audit=asdict(report), elapsed_s=perf_counter()-shard_start,
            source_drift=sorted(p for p in epoch.keys()|after.keys() if epoch.get(p)!=after.get(p)))
        write(audit_path, record)
        records.append(record)
        print(f'{vessel}/{size}: {record["status"]}, seed={config["seed"]}, '
              f'voyage={record.get("voyage_ran")}, checker={record.get("checker_s")}, '
              f'elapsed={record["elapsed_s"]:.3f}s', flush=True)
        if record['source_drift']:
            raise RuntimeError(f'source changed during shard: {record["source_drift"]}')
    write(OUTPUT/'manifest.json', dict(generator=instances.INSTANCES_VERSION,
        vessel_condition=CONDITION_VERSION, c2_screened=False,
        default_rule='10% of each consumable group remains; declared vessel draw-down order',
        plan_kind='empty diagnostic; no feasibility search', records=records,
        checked=sum(r['status']=='checked' for r in records),
        voyage_ran=sum(r.get('voyage_ran', False) for r in records),
        full_checker_passed=sum(r.get('full_checker_passed', False) for r in records),
        json_roundtrips=sum(r.get('json_roundtrip', False) for r in records),
        total_shard_s=sum(r['elapsed_s'] for r in records), elapsed_this_run_s=perf_counter()-started))


if __name__=='__main__':
    main()
