"""Render measured batch progress without starting a solve or reading sealed data."""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time

from .sequence_provenance import frame_provenance

HERE = Path(__file__).resolve().parent


def active_manifest_path():
    """Keep the original manifest immutable while displaying active supplements."""
    try:
        active = json.loads((HERE/'campaign-control.json').read_text())['active_campaign']
        candidate = (HERE/'screening'/active/'manifest.json').resolve()
        if candidate.is_relative_to(HERE.resolve()) and json.loads(candidate.read_text()).get('campaign_id') == active:
            return candidate
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return HERE/'manifest.json'


def collect(row, *, base_record=None):
    out = {k: row.get(k) for k in ('id', 'key', 'sentences', 'status', 'lifts', 'lift_delta',
           'total_wall_s', 'release_wall_s', 'base_already_obeys', 'purpose', 'admission', 'sequence_repair_wall_s', 'computed_utc')}
    out['engine_wall_s'] = row.get('engine_wall_s', row.get('wall_s'))
    if row.get('budget_status') == 'timeout_with_checked_incumbent' and 'engine_wall_s' not in row:
        # Legacy screens assigned the allowance to wall_s; elapsed was not recorded.
        out['engine_wall_s'] = None
    out['engine_allowance_s'] = row.get('timeout_allowance_s', (row.get('profile') or {}).get('engine_timeout_s'))
    out['frame_provenance'] = frame_provenance(row, base_record=base_record)
    if row.get('plan_producer') == 'lattice.standard_plan_pool':
        out['native_gate_wall_s'] = row.get('native_gate_wall_s', (row.get('gate') or {}).get('wall_s'))
        if row.get('release_timing_scope') != 'after_initial_admission':
            out['historical_release_including_admission_s'] = row.get('release_wall_s')
            out['release_wall_s'] = None
    return out


def progress(*, run_dir=None):
    run_dir = Path(run_dir) if run_dir is not None else HERE/'runs'
    rows = []
    for path in sorted(run_dir.glob('*.json')):
        if any(path.name.endswith(s) for s in ('.running.json', '.incumbent.json', '.frames.json')):
            continue
        try:
            row = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if path.name.endswith('.error.json') and row.get('key') and (run_dir/(row['key']+'.json')).exists():
            continue
        rows.append(collect(row))
    manifest_path = active_manifest_path()
    if run_dir.resolve() == (HERE/'runs').resolve() and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        completed_ids = {row['id'] for row in rows}
        active = {}
        for path in run_dir.glob('*.running.json'):
            try:
                running = json.loads(path.read_text())
                active[running['id']] = running
            except (OSError, ValueError, KeyError):
                continue
        for spec in manifest.get('instructions', [])+manifest.get('off_lattice', []):
            if spec['id'] not in completed_ids:
                running = active.get(spec['id'])
                rows.append(dict(spec, status='running' if running else 'pending',
                                 admission=running.get('admission') if running else None))
    fleet = None
    try:
        fleet = json.loads((HERE.parents[3]/'experiments/fleet/core-state.json').read_text())
        fleet['reading_age_s'] = time.time()-float(fleet['t'])
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return dict(as_of_utc=datetime.now(timezone.utc).isoformat(), rows=rows, fleet=fleet,
                stored=sum(r['status'] == 'checked_plan' for r in rows),
                instructions_stored=sum(r['status'] == 'checked_plan' and r['id'] != 'base' for r in rows),
                bases_stored=sum(r['status'] == 'checked_plan' and r['id'] == 'base' for r in rows),
                proved_refusals=sum(r['status'] == 'proved_refusal' for r in rows),
                measured_live=sum(r['purpose'] == 'off_lattice' and r.get('engine_wall_s') is not None for r in rows),
                running=[p.name for p in run_dir.glob('*.running.json')])


def render_table(data=None):
    data = progress() if data is None else data
    lines = ['| Instruction | Status | Lifts | Change | Engine seconds | Original total seconds | Retry gate seconds | Allowance seconds | Admission load1 | Base obeys | Computed UTC | Crane sequence | Baseline crane sequence | Crane frames walked on this plan |',
             '|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|---|---:|']
    for row in data['rows']:
        def number(key):
            value = row.get(key)
            return '—' if value is None else f'{value:.2f}'
        load = (row.get('admission') or {}).get('load1')
        load_label = '—' if load is None else f'{load:.2f}'
        provenance = row.get('frame_provenance') or frame_provenance(row)
        lines.append('| '+' | '.join(['; '.join(row.get('sentences') or [row['id']]),
                     row['status'], number('lifts'), number('lift_delta'), number('engine_wall_s'),
                     number('total_wall_s'), number('release_wall_s'), number('engine_allowance_s'), load_label, str(row.get('base_already_obeys')),
                     row.get('computed_utc') or '—', provenance['label'],
                     provenance['base_sequence']['label'],
                     str(provenance['frames_checked']) if provenance['frames_checked'] is not None else '—'])+' |')
    return '\n'.join(lines)+'\n'


def _atomic_text(path, content):
    descriptor, temporary = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def update(*, run_dir=None, output_dir=None):
    """Serialize reporters so a slower worker cannot overwrite newer progress."""
    destination = Path(output_dir) if output_dir is not None else HERE
    destination.mkdir(parents=True, exist_ok=True)
    with (destination/'.progress.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = progress(run_dir=run_dir)
        _atomic_text(destination/'progress.json', json.dumps(data, indent=2)+'\n')
        _atomic_text(destination/'RULES.md',
            'Measured development results; engine seconds measure the owned engine call, including internal waits, after initial admission and before release gates. '
            'Original total seconds retain the first attempt; retry gate seconds are additional fresh release work, excluding its initial admission. '
            'Historical timeout search duration and portfolio retry clocks with mixed scopes are unknown (—), never substituted from allowances. '
            'Load1 is sampled at admission; machine load can change during each run.\n\n'+render_table(data))
        _refresh_hero_readme(destination)
        _refresh_rules_readme(destination, data)
    return data


def _refresh_rules_readme(destination, data):
    readme = destination/'README.md'
    if not readme.exists():
        return
    start, end = '<!-- RULE_STATUS_START -->', '<!-- RULE_STATUS_END -->'
    text = (f"\n## Current rule status\n\nAs of {data['as_of_utc']}: "
            f"{data['instructions_stored']} stored instructions, {data['bases_stored']} stored bases, "
            f"{data['proved_refusals']} proved refusals and {data['measured_live']} measured live requests.\n\n"
            "Stored entries retain their recorded computation timestamps. Engine seconds cover the owned "
            "engine call; retry gate seconds are additional fresh release work. Load1 is sampled at admission. "
            "Allowances are configured limits, not measurements; historical timeout durations and mixed-scope retry times are unknown (—). "
            "Crane sequence labels describe the selected plan; the baseline column describes only its comparison base. "
            "Portfolio selections inherit the donor sequence's repair status. Frame counts are crane frames walked on this plan, not a fleet pass rate. "
            "Individual checkpoints preserve the complete provenance and gate evidence.\n\n"+render_table(data))
    original = readme.read_text()
    if start in original and end in original:
        before, rest = original.split(start, 1)
        _, after = rest.split(end, 1)
        updated = before+start+text+end+after
    else:
        updated = original.rstrip()+'\n\n'+start+text+end+'\n'
    _atomic_text(readme, updated)


def _refresh_hero_readme(destination):
    hero_path, readme = destination/'hero.json', destination/'README.md'
    if not hero_path.exists() or not readme.exists():
        return
    data = json.loads(hero_path.read_text())
    hero, backup = data['hero'], data.get('backup')
    text = (f"Hero: **{hero['instance_id']}**, {hero['boxes']} boxes, "
            f"{hero['lifts']:g} extra lifts including projected later-port lifts; "
            f"{hero['frames_checked']} crane frames walked on this plan and passed; {frame_provenance(hero)['label']}. Computed {hero['computed_utc']}.\n\n"
            f"Source: `{hero['path']}`. {hero['why']}\n\n")
    text += (f"Backup: **{backup['instance_id']}**, {backup['boxes']} boxes, "
             f"{backup['lifts']:g} extra lifts, {backup['frames_checked']} crane frames walked on this plan and passed; {frame_provenance(backup)['label']}.\n\n"
             if backup else 'Backup voyage is still being screened.\n\n')
    text += ('`hero.json` pins the sources and selection evidence. `fixtures/hero-instruction.json` '
             'and `fixtures/hero-replan.json` identify the selected fully gated DG rule '
             'that visibly changes this base.\n')
    original = readme.read_text()
    start, end = '<!-- HERO_STATUS_START -->', '<!-- HERO_STATUS_END -->'
    if start in original and end in original:
        before, rest = original.split(start, 1)
        _, after = rest.split(end, 1)
        _atomic_text(readme, before+start+'\n'+text+end+after)


if __name__ == '__main__':
    print(json.dumps(update(), indent=2))
