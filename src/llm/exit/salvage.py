"""D129 fresh-process inheritance of CPU-readmitted saved generation outcomes."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import time


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def validate_prepared(path,args):
    """Fail before GPU admission if preparation, its source epoch or targets drift."""
    from llm.exit.runner import read_json,adapter_identity,source_pins
    from llm.exit.reverify import assert_training_receipt
    path=Path(path).resolve(); row=read_json(path)
    payload={k:v for k,v in row.items() if k!='prepared_sha256'}
    if row.get('schema')!='exit-salvage-prepared-v1' or row.get('prepared_sha256')!=_digest(payload):
        raise ValueError('salvage preparation content/schema mismatch')
    if row.get('status')!='ready':raise ValueError('no current-epoch winners ready to inherit')
    if (Path(row['new_out']).resolve()!=Path(args.out).resolve()
            or Path(row['parent']).resolve()!=Path(args.parent).resolve()):
        raise ValueError('salvage output or original parent changed')
    if row['parent_identity']!=adapter_identity(args.parent) or row['current_sources']!=source_pins():
        raise ValueError('salvage parent or source epoch changed; CPU re-verification required')
    receipt=assert_training_receipt(row['receipt_path'],selections=row['original_selection_paths'],
        training_epoch=row['current_sources'],model_identity=dict(adapter=row['parent_identity']))
    for name,digest in row.get('output_sha256',{}).items():
        # The schedule legitimately grows only after inheritance commits.
        if (Path(args.out)/'gen-000/summary.json').exists() and Path(name).name=='schedule.json':
            continue
        if hashlib.sha256(Path(name).read_bytes()).hexdigest()!=digest:
            raise ValueError('prepared critical output changed: '+name)
    if not row.get('output_sha256'):
        raise ValueError('prepared critical output inventory missing')
    result=read_json(Path(row['receipt_path']).parent/'result.json')
    if result['selections']!=row['selections']:
        raise ValueError('prepared selections differ from the bound re-verification result')
    return row,receipt


def inherit_prepared(args):
    """Train only newly admitted rows; retain all original generation evidence."""
    from collections import Counter
    from llm.exit.runner import (read_json,write_json,train_selected,resize_after_pilot,
                                 source_pins,adapter_identity)
    from llm.exit.progress import start_generation
    row,receipt=validate_prepared(args.recover_prepared,args)
    directory=args.out/'gen-000'; target=directory/'summary.json'
    manifest=read_json(directory/'instances.json')
    parent_config=manifest['population'][0]
    if target.exists():
        summary=read_json(target)
        if summary.get('salvage_prepared_sha256')!=row['prepared_sha256']:
            raise ValueError('published inheritance belongs to another preparation')
        population=summary['next_population']
    else:
        tracker=start_generation(args.out,0,workers=args.workers,
            maps=len(manifest['instances']),k=args.k,salvaged_from=row['source_out'])
        args._active_tracker=tracker;args._active_generation=0
        tracker.update(reused_saved_decode=True,decoded_calls=len(manifest['instances']),
            total_decode_calls=len(manifest['instances']),training_reverification=row['counts'])
        started=time.monotonic()
        args._training_extra_checks=lambda: validate_prepared(args.recover_prepared,args)
        inherited=train_selected(0,parent_config,row['selections'],
            [Path(p) for p in manifest['instances']],args,directory,tracker,
            training_receipt=receipt,original_selections=row['original_selection_paths'],
            model_identity=dict(adapter=row['parent_identity']))
        # Refuse weights produced across another edit; never relabel this epoch.
        validate_prepared(args.recover_prepared,args)
        del args._training_extra_checks
        children=inherited['children']
        population=[{**parent_config,'elite':True}]+[{**c,'elite':False} for c in children]
        selected=row['selections']; passed=[s for s in selected if s['passed']]
        timings=dict(inherited['timings'])
        original=row.get('original_timings') or {}
        original_phases=original.get('phases',[])
        pipeline=next((p.get('wall_s') for p in original_phases
                       if p['name']=='decode_verify_pipeline'),None)
        original_decode=original.get('counters',{}).get('decode_wall_s',0.)
        # APPROXIMATION: sizing uses the original measured decode/verify workload
        # plus re-verification, not the near-zero cost of reading its saved files.
        timings.update(decode_s=original_decode,
            decode_verify_pipeline_s=pipeline if pipeline is not None else original_decode,
            reused_decode_s=original_decode,new_decode_s=0.,
            training_reverification_s=read_json(Path(row['receipt_path']).parent/'result.json')['wall_s'],
            inheritance_wall_s=time.monotonic()-started,
            wall_s=time.monotonic()-started)
        summary=dict(generation=0,parent=str(args.parent),next_population=population,children=children,
            fitness=dict(best_adapter=str(args.parent),best_method='sft',
                selection='sole original parent; no child fitness comparison yet'),audit=None,
            source_epoch=source_pins(),source_adapter_identity=adapter_identity(args.parent),
            salvage_prepared_sha256=row['prepared_sha256'],salvaged_from=row['source_out'],
            training_reverification=row['counts'],winner_count=len(passed),instance_count=len(selected),
            winners_by_source=dict(Counter(s['source'] for s in passed)),
            winners_by_source_detail=dict(Counter(s.get('winner_source_detail',s['source']) for s in passed)),
            preference_pairs=len(inherited['pairs']),preference_over_length_pairs=inherited['over_length_pairs'],
            timings=timings,datasets={str(r):b[1] for r,b in inherited['builds'].items()},
            recovery=inherited['recovery_report'],generations_per_hour=None,
            timing_note='original decode reused; inheritance wall is not a full-generation wall measurement')
        write_json(target,summary)
        write_json(args.out/'survivor.json',dict(adapter=str(args.parent),generation=0))
        tracker.finish(status='completed',children_trained=len(children),
            preference_pairs=len(inherited['pairs']),salvaged=True)
    resize_after_pilot(summary,args)
    args._start_generation=1;args._resume_population=population
    args._loaded_source_pins=source_pins()
