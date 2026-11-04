from pathlib import Path
p=Path(__file__).resolve().parent/'matched.py'
s=p.read_text().replace('fail closed on D204 admission','fail closed on D206 admission')
s=s.replace("LOCK_HELD = False", "LOCK_HELD = False\nSTATUS = OUT / 'status.json'")
s=s.replace("OUT / 'status.json'",'STATUS')
s=s.replace('STATUS = STATUS',"STATUS = OUT / 'status.json'")
s=s.replace("reason='D204 requires core-state age < 120 s, load1 <= 30 and core_mask >= 4095'", "reason='D206 requires age < 120 s, load1 <= 45 and popcount(mask) >= 8'")
s=s.replace('D204 admission failed','D206 admission failed')
s=s.replace('def main():', '''def completed_map(man, job):
    """Resume only whole, source-bound, valid cohorts; never overwrite outcomes."""
    from matched_report import valid_admission
    paths = [OUT / 'rows' / job['instance_id'] / f'{arm}.json' for arm in ARMS]
    if not any(p.exists() for p in paths):
        return False
    if not all(p.exists() for p in paths):
        raise RuntimeError('partial cohort must be reviewed before resume')
    identity = json.loads((OUT / 'cohort-identity.json').read_text())
    for arm, path in zip(ARMS, paths):
        row = json.loads(path.read_text())
        expected = dict(input_sha256=job['input_sha256'], sample_sha256=job['samples'][arm],
                        code=man['code'], finisher_s=600, **identity)
        if (row.get('complete') is not True or row.get('error') is not None
                or row.get('binding') != expected or row.get('arm') != arm
                or row.get('instance_id') != job['instance_id'] or row.get('sample') != 0
                or not valid_admission(row)):
            raise RuntimeError(f'invalid historical cohort row: {path}')
        assert_binding(man, job, arm)
        if row.get('passed'):
            for suffix, key in (('.json', 'plan_sha256'), ('.report.json', 'report_sha256')):
                if sha(OUT / 'plans' / job['instance_id'] / f'{arm}{suffix}') != row['winner'][key]:
                    raise RuntimeError('historical winner artifact changed')
    return True


def main():''')
s=s.replace('global CONTROLLER_LOCK, LOCK_HELD','global CONTROLLER_LOCK, LOCK_HELD, STATUS')
s=s.replace("parser.add_argument('--probe-only', action='store_true')", "parser.add_argument('--probe-only', action='store_true')\n    parser.add_argument('--resume-d206', action='store_true')")
s=s.replace("CONTROLLER_LOCK = (OUT / 'controller.lock').open('a+')",'''if os.getpriority(os.PRIO_PROCESS, 0) != 0:
        raise RuntimeError('normal priority (nice 0) required')
    if args.resume_d206:
        migration = json.loads((OUT / 'migration-D206.json').read_text())
        for job in new['maps']:
            if completed_map(new, job):
                continue
            old = OUT / 'batches' / job['instance_id']
            if not (old / 'fence.json').exists() or (old / 'start.json').exists():
                raise RuntimeError('obsolete queue not safely fenced')
        STATUS = OUT / 'status-D206.json'
    CONTROLLER_LOCK = (OUT / ('controller-D206.lock' if args.resume_d206 else 'controller.lock')).open('a+')''')
s=s.replace("LOCK_HELD = True\n    write", "LOCK_HELD = True\n    if args.resume_d206:\n        write(OUT / 'active-controller.json', dict(pid=os.getpid(), status_path=str(STATUS), policy='D206', at=now()))\n    write")
s=s.replace("if any(p.exists() for p in paths):\n            raise RuntimeError(f'prior fresh rows exist for {iid}; preserve them; do not silently reuse or mix cohorts')", "if args.resume_d206 and completed_map(new, job):\n            continue\n        if any(p.exists() for p in paths):\n            raise RuntimeError(f'prior fresh rows exist for {iid}; preserve them; do not silently reuse or mix cohorts')")
s=s.replace("batch = OUT / 'batches' / iid", "batch = OUT / ('batches-D206' if args.resume_d206 else 'batches') / iid")
s=s.replace("binding=dict(input_sha256", "nice=os.getpriority(os.PRIO_PROCESS, 0), binding=dict(input_sha256")
s=s.replace("try:\n        assert_binding(man, job, arm)", "try:\n        if row['nice'] != 0:\n            raise RuntimeError('worker must have normal priority')\n        assert_binding(man, job, arm)")
p.write_text(s)
