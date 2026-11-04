"""Render recorded profiles without recomputing the workload."""
import json
from pathlib import Path
P=Path(__file__).resolve().parent
R={r:json.loads((P/f'profile-{r}.json').read_text()) for r in ('medium','full')}

def f(row,suffix,func):
    found=[x for x in row['profile']['functions'] if x['file'].endswith(suffix) and x['func']==func]
    return found[0] if found else dict(cum_s=0.,calls=0,self_s=0.)
lines=['# Switch-on cost-workload profiles','',
'All sources/tests are read-only. `profile_mask.py` copies the construction/warm-cache schedule from `tests/llm/test_mask_cost.py`, then profiles exactly one chosen action per rung and schedule. `profile_boundary_count.py` adds the production-like exact count immediately after the initial head-domain query. Fixtures retain the hull, bridge and fixed background; yards contain singleton row-one stacks, H=8, C2 off. These are timing inputs, not physical certification.',
'', '## Reproduce','', '```sh',
'UV_CACHE_DIR=/tmp/w2-mask-perf-uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \\',
'VECLIB_MAXIMUM_THREADS=1 STOWAGE_IN_PORT_WORKERS=1 \\',
'uv run --offline python experiments/perf/mask-w2/profile_mask.py --rung medium --repeats 3',
'# Repeat with --rung full; replace profile_mask.py with profile_boundary_count.py for boundary counts.',
'```','',
'Construction and first-action warm-up are excluded. Tokenizer/field caches are warm, each sample uses a fresh engine. There are three measured repeats. No GPU/network used. UV cache was moved into /tmp because the default cache is outside writable roots.',
'', '## Unprofiled action wall time','',
'| Rung | Decode domain ms | Commit ms | Decode total ms | Late exact-count increment ms | Total with late count ms | Boundary-count total ms |',
'|---|---:|---:|---:|---:|---:|---:|']
for rung,r in R.items():
    a=r['decode']['mean'];b=r['with_count']['mean'];c=json.loads((P/f'profile-{rung}-boundary-count.json').read_text())['mean']
    lines.append(f"| {rung} | {a['domain_s']*1e3:.2f} | {a['commit_s']*1e3:.2f} | {a['total_s']*1e3:.2f} | {b['count_s']*1e3:.2f} | {b['total_s']*1e3:.2f} | {c['total_s']*1e3:.2f} |")
lines+=['','The late count matches the existing cost workload, after the chosen box tail has been built; the boundary count matches eval scheduling, after initial heads but before choosing a box. An eager count populates the engine complete legal set, so the remaining tail is cheap. These costs are not interchangeable.', '',
'## Component cumulative time, milliseconds per profiled action','',
'Each cell is `cumulative ms / calls`; nested rows overlap and MUST NOT be added. cProfile approximately doubles wall time in this workload; use the previous unprofiled table for latency. Components absent from these inputs are shown explicitly. Raw JSON contains every function and per-phase profiles; `.pstats` files are reusable with pstats.', '',
'| Component / entry point | Medium decode | Full decode | Medium late-count total | Full late-count total |',
'|---|---:|---:|---:|---:|']
entries=[
('Whole selected action','profile_mask.py','action'),
('Heads: engine box projection','feasibility/_engine.py','legal_box_ids'),
('Chosen box: engine slot projection','feasibility/_engine.py','legal_slots_for_box'),
('Exact count query','stowlm/mask.py','n_legal'),
('Feasibility evaluation (includes expander/replay)','feasibility/_engine.py','_evaluate'),
('Forced hatch group expansion + recursive screening','feasibility/_engine.py','_restow_places'),
('Grouped physical + D69 screens','feasibility/_port_screen.py','port_actions_allowed'),
('Expander can_expand','actions/expander.py','can_expand'),
('Expander step','actions/expander.py','step'),
('Expander validated commit','actions/expander.py','_validated_commit'),
('Expander snapshot + deepcopy','actions/expander.py','world_state'),
('Expander shallow mutable-state copy','actions/expander.py','copy'),
('Move engine apply_move','moves/engine.py','apply_move'),
('Move replay index construction','moves/_physical.py','__init__'),
('Move replay state materialization','moves/_physical.py','state'),
('Crossing paths','moves/_paths.py','crossing_paths'),
('Crossing geometry index construction','moves/_paths.py','__init__'),
('D69 pending lift rows','moves/_sequencing.py','pending_lift_rows'),
('D64 gate (screen disabled)','feasibility/in_port.py','in_port_screen_enabled'),
('D64 actual prefix screen','feasibility/in_port.py','screen_in_port_prefix'),
('DG C1g','feasibility/_engine.py','_c1g'),
('DG static tables','feasibility/_engine.py','_dg_tables'),
('DG free-slot cache','feasibility/_engine.py','_dg_F'),
('Catalogue snapshot resolution','rules/catalogue.py','catalogue_snapshot'),
('Captured catalogue context construction','actions/expander.py','__init__'),
('Captured catalogue context entry (includes body)','actions/expander.py','run'),
('Head trie build (includes engine)','stowlm/mask.py','_build_from_boxes'),
('Tail trie build (includes engine)','stowlm/mask.py','_tail'),
('Engine copy','feasibility/_engine.py','copy'),
('Engine push','feasibility/_engine.py','push'),
('Container ID check-digit validation','conventions.py','iso6346_check_digit'),
('Slot address parse','conventions.py','parse_slot'),
('Yard address parse','conventions.py','parse_yard'),
]
for name,suffix,func in entries:
    cells=[]
    for rung,mode in [('medium','decode'),('full','decode'),('medium','with_count'),('full','with_count')]:
        v=f(R[rung][mode],suffix,func);cells.append(f"{v['cum_s']*1e3:.3f} / {v['calls']:,}")
    lines.append('| '+name+' | '+' | '.join(cells)+' |')
lines+=['',
'Catalogue context construction is absent from the measured action because engines/expanders are constructed before the timer. Captured-context entry is frequent but its cumulative time includes all method bodies; catalogue resolution self time is small. DG class/free-slot tables are absent because these cost fixtures use plain cargo. D64 is deliberately gated off (`D64_COST_PRUNING_QUALIFIED` false); zero screen time does not predict its future activated cost. H=8 is above reached deck heights on these prefixes, so the expensive D69 obligation path is absent.',
'', '## Nonoverlapping views','',
'The unprofiled domain/count/commit rows above are a nonoverlapping phase decomposition. The following is a second nonoverlapping decomposition by **exclusive Python/C function self time**, with library helper work left in its own bucket. It is not the end-to-end time of each semantic component: e.g. validators call address/ID helpers and those helpers are charged to address parsing. The cProfile action cumulative time exceeds summed self time; the residual is shown separately without assigning it to a semantic component.', '',
'| Exclusive self-time component | Medium decode ms / action share | Full decode ms / action share |',
'|---|---:|---:|']
keys=R['medium']['decode']['profile']['exclusive_s'].keys()
for key in keys:
    cells=[]
    for rung in ('medium','full'):
        p=R[rung]['decode'];total=f(p,'profile_mask.py','action')['cum_s'];v=p['profile']['exclusive_s'].get(key,0.)
        cells.append(f'{v*1e3:.3f} / {v/total*100:.2f}%')
    lines.append('| '+key+' | '+' | '.join(cells)+' |')
for title in ('residual profiler accounting',):
    cells=[]
    for rung in ('medium','full'):
        p=R[rung]['decode'];total=f(p,'profile_mask.py','action')['cum_s'];v=total-sum(p['profile']['exclusive_s'].values());cells.append(f'{v*1e3:.3f} / {v/total*100:.2f}%')
    lines.append('| '+title+' | '+' | '.join(cells)+' |')
lines+=['', '## Interpretation and comparison to the historical 0.15 / 0.43 s claim','',
'The activated cost workload no longer reproduces the order’s 0.641/2.623 s decode latencies: this captured source gives 0.167/0.708 s. All relevant source SHA-256 values are embedded before/after in the JSON; no source drift occurred during either run. Existing grouped probes and discard-state yard replay are already present in the snapshot. Do not attribute the historical-to-current improvement to this read-only profiling work.',
'',
'The 0.15/0.43 s feasibility-only comparison is not an additive layer comparison. The mask directly calls the feasibility engine; almost all expensive `_evaluate` time is spent below the engine in forced hatch expansion, physical replay, validated model materialization and address/ID validation. Trie/tokenizer self time is below 0.1%. Full decode spends about 69% cumulative inside `_restow_places`, 66% inside `apply_move`, and 61% in Pydantic model construction (nested measures). These confirm a replay/materialization bottleneck and refute an interpretation that token-trie machinery itself accounts for the missing seconds.',
'',
'The feasibility probe’s lazy schedule chooses `legal_box_ids()[0]` (lexicographically first box ID), while the token mask chooses the smallest allowed token at each prefix. The selected full mask action here is `Place(QZWU0102033, 0050104)`; medium is `Place(QZWU0101037, 0020104)`. Different selected boxes can need different forced hatch work. Furthermore current source differs from the historical fix log. Thus 0.15/0.43 cannot safely be subtracted from 0.167/0.708, or from the order’s historical numbers, to infer mask overhead.',
'',
'Artifacts: `profile-medium.json`, `profile-full.json` contain per-repeat, per-phase, per-function data and source hashes; `profile-*-boundary-count.json` captures the production-like count schedule; `.pstats` holds complete cProfile call graphs. All nine timing samples per rung (decode, late count, boundary count) have identical token-by-token allowed-set digests, chosen action and token sequence. This only verifies the selected timing paths, not the full agreement corpus. No src or tests were edited.',
]
(P/'profile-notes.md').write_text('\n'.join(lines)+'\n')
print(P/'profile-notes.md')
