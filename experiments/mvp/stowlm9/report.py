"""Read durable train-only artifacts; refresh LABELS.md without touching workers."""
from collections import Counter
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import time
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
TZ = ZoneInfo('America/New_York')


def read(path,default=None):
    return json.loads(path.read_text()) if path.exists() else default


def mean(values):
    return round(sum(values)/len(values),3) if values else None


def render():
    manifest = read(HERE/'manifest.json')
    if not manifest:
        return
    text = [f"# D199 StowLM 8.5 / 8.6 label streams\n\nUpdated {datetime.now(TZ).isoformat(timespec='seconds')}. CPU only; no GPU; no src edits.\n",
        f"Manifest `{manifest['manifest_id']}`; outcome epoch `{manifest['outcome_epoch']}`. "
        f"Corpus census: {manifest['corpus_count']} instance JSON files (audit sidecars excluded); "
        f"{len(manifest['excluded_val'])} frozen-val overlaps excluded; {len(manifest['entries'])} train maps eligible. "
        "Both streams use the same fixed 14-small/3-tiny order, with round-robin ship/yard strata. "
        "No validation/test/showcase/RCSPPSuite map is planned.\n",
        "The original prompt already contains exact `g.covers` stack membership. Prompt 9.0 adds explicit "
        "cover-to-bay runs and hatch-access/cost guidance; absence of hatch information was not confirmed. "
        "The 80 historical validation rows remain byte-identical and separate, with their original prompt version. "
        "New-prompt training must use `prompt_v9.install_training()`; this admits only those exact pinned legacy val rows.\n",
        "Prompt token probe: 36 stratified train maps, 3 per size/ship/yard stratum. "
        "Tiny mean 702.72 tokens (684–722), +125 over base; small mean 1009.39 (894–1124), +138. "
        "See `prompt_tokens.json` and `prompt_audit.md`.\n",
        "| Stream | Completed / scope | Passing labels | Source counts | Mean winner / best-d172 (paired maps) | Workers | ETA |\n"
        "|---|---:|---:|---|---|---:|---|\n"]
    details = []
    for stream,total in [('rule',len(manifest['entries'])),('frontier',len(manifest['entries']))]:
        status = read(HERE/stream/'status.json',{})
        rows = [read(p) for p in sorted((HERE/stream/'rows').glob('*.json'))]
        rows = [r for r in rows if r.get('manifest_id')==manifest['manifest_id']]
        complete = [r for r in rows if r.get('status')=='complete']
        passing = [r for r in complete if r.get('passed') is True]
        paired = [r for r in passing if r.get('baseline_moves') is not None]
        source = dict(Counter(r['source'] for r in passing))
        workers = status.get('workers',3 if stream=='rule' else 0)
        avg = mean([r['wall_s'] for r in rows if 'wall_s' in r])
        if avg:
            remaining = max(0,total-len(rows))*avg/max(1,workers)
            end = datetime.now(TZ)+timedelta(seconds=remaining)
            eta = f"{end:%b %d %H:%M %Z} ({remaining/3600:.1f} h; observed {avg:.1f} s/map)"
        else:
            calibration = 2*144.1
            hours = total*calibration/max(1,workers)/3600
            eta = f'provisional {hours:.1f} h total from tiny smoke; first-map timing pending'
            if stream=='frontier':
                eta += f'; 1000 labels ~{1000*calibration/max(1,workers)/3600:.1f} h at {workers} workers'
        if stream=='frontier' and (HERE/'frontier/PAUSED.json').exists():
            phase=read(HERE/'frontier/PAUSED.json').get('phase','paused')
            eta='PAUSED by leader; '+phase+'; resume requires explicit instruction'
        if stream=='rule':
            eta += '; runs overnight; initial snapshot Oct 31 19:45 EDT, then every 200 additional labels'
        text.append(f"| {stream} | {len(complete)} / {total} | {len(passing)} | {source} | "
            f"{mean([r['moves'] for r in paired])} / {mean([r['baseline_moves'] for r in paired])} ({len(paired)}) | {workers} | {eta} |\n")
        errors = Counter(r.get('error','unspecified') for r in rows if r.get('status')=='error')
        rejects = [dict(key=r['key'],source=c['source'],reason=c.get('error') or (c.get('check') or {}).get('error') or (c.get('check') or {}).get('unavailable_reason'))
                   for r in complete for c in r.get('candidates',[]) if c.get('passed') is not True]
        bad_canonical = [r for r in rejects if r.get('reason') and ('LabelError' in r['reason'] or 'canonical' in r['reason'] or 'placement' in r['reason'])]
        export = read(HERE/stream/'export-metrics.json')
        info = dict(stream=stream,completed=len(complete),passing=len(passing),pass_rate=len(passing)/len(complete) if complete else None,
                    errors=dict(errors),candidate_rejections=rejects,canonical_rejections=bad_canonical,
                    export=export)
        report_path = HERE/stream/'report-current.json'
        report_path.parent.mkdir(exist_ok=True)
        tmp = report_path.with_name(report_path.name+f'.{os.getpid()}.tmp')
        tmp.write_text(json.dumps(info,indent=1,allow_nan=False)+'\n');tmp.replace(report_path)
        details.append(f"\n{stream}: pass rate {len(passing)}/{len(complete)} completed; "
            f"errors {sum(errors.values())}; candidate rejections {len(rejects)}; "
            f"canonical-form rejections {len(bad_canonical)}. Exact rejected map/source/reason list: "
            f"`{stream}/report-current.json`. Export/token/source metrics: `{stream}/export-metrics.json`.\n")
        if export:
            details.append(f"Export token lengths by size: `{json.dumps(export.get('by_size',{}),sort_keys=True)}`.\n")
    text.extend(details)
    text.append("\nEvery admitted label has one stored Place per box, a deterministic final-stow ordering, "
        "a fresh guarded full-checker certificate bound to the current epoch, exact objective agreement, "
        "and a complete layered-mask token replay without repair. Best-d172 is freshly canonicalized and checked "
        "on the same map; lowest checked canonical cost wins, ties keep best-d172. Raw baseline costs are retained "
        "in each row as `baseline_raw_moves`. Canonicalization failures are explicit exclusions.\n\n"
        "Exports use fresh generation directories and an atomic data-85/data-86 symlink swap. "
        "Rows bind manifest, input bytes, baseline candidate bytes, stream and epoch; errors retry on resume. "
        "Missing/error/no-teacher cases stay counted. EXPORT.DONE means publication succeeded, not full quota coverage. "
        "D209: rule continues through the night at 3 workers over the full fixed queue. At 19:45 data-85 and data-85-0000 are published and rule/EXPORT.DONE records success; later data-85-0001, 0002, ... retain snapshots every 200 further admitted labels. No post-19:45 rows are excluded. See rule/snapshots and rule/MIX.md for retained failures and ship/size/fill selection effects. Frontier remains paused; "
        "`frontier/1000.DONE` reports the 1,000-passing-label milestone.\n\n"
        "Assumptions: rule planning budget is 300 s and V4 finisher budget is 900 s; frontier runs unedited 0.9.0 with "
        "6 s/box search capped at 120 s and 300 s per finisher. Native numerical libraries are set to one thread. "
        "The unedited frontier source requests two CP-SAT workers, so a pinned runtime wrapper clamps only "
        "that native resource setting to one at solve entry. Rule quota is 3 workers; increases require a leader instruction; frontier is explicitly paused and ARM.DONE cannot resume it. "
        "Preserved pre-upgrade passing labels retain their original producer manifest/proof in history; the physical outcome epoch is unchanged. The supplied 5,324 count includes audit sidecars; the actual instance census above is used.\n")
    text.append("\nVerification: 29 original CPU/offline regression tests plus 8 supervisor/readout update regressions; real train tiny smoke passed full guarded checker "
        "and mask with 4 actions, 29 completion tokens, and 9 matching moves. Its certification took 144.1 s "
        "under high shared-machine load. Startup ETA uses two such certifications per map and excludes planning; "
        "it is provisional and is replaced by observed stream timing. Details: `verification.json`.\n")
    path = HERE/'LABELS.md'
    tmp = path.with_name(path.name+f'.{os.getpid()}.tmp')
    tmp.write_text(''.join(text));tmp.replace(path)


if __name__=='__main__':
    render()
