"""Initial discovery only. Run inventory-finalize.py afterward for final commit proposal. Read-only metadata; writes only this prep folder."""
from pathlib import Path
import os, subprocess, collections
root=Path.cwd(); out=root/'experiments/mvp/repo'
tracked=set(subprocess.check_output(['git','ls-files','-z']).decode().split('\0'))-{''}
# APPROXIMATION: whole generated cache/environment/snapshot trees are excluded by directory name; no byte-by-byte scan of their contents.
pruned=[]; rows=[]
for base, dirs, files in os.walk(root, followlinks=False):
    rel=Path(base).relative_to(root)
    for d in list(dirs):
        low=d.lower()
        if d in {'.git','.venv','.uv-cache','.pytest_cache','__pycache__','node_modules','.claude'} or any(x in low for x in ('venv','pytest','uv-cache')) or d in {'cache','.cache','checkpoints','snapshot','source-snapshot','frozen-epoch','receipts'}:
            dirs.remove(d); pruned.append(str(rel/d))
    for name in files:
        p=Path(base)/name; path=str(p.relative_to(root))
        if p.is_symlink(): continue
        try: n=p.stat().st_size
        except FileNotFoundError: continue
        rows.append((path,n))
paths=[p for p,n in rows]
ignored=set(subprocess.run(['git','check-ignore','--stdin','-z'],input=('\0'.join(paths)+'\0').encode(),capture_output=True).stdout.decode().split('\0'))
# Keep a minimal reproducible code tree plus all tracked experiment material for continuity.
keep=[]; docs=[]; hold=[]
for p,n in rows:
    parts=Path(p).parts
    if parts[0]=='docs':
        if p in {'docs/architecture.md','docs/data-sources.md','docs/domain.md','docs/impact.md','docs/frontier.md'}: docs.append((p,n))
        else: hold.append((p,n,'private/working docs; explicit user approval required'))
    elif p in ignored and p not in tracked: hold.append((p,n,'already ignored; do not force-add'))
    elif p.startswith('experiments/mvp/repo/'): hold.append((p,n,'local publication prep, contains scan metadata'))
    elif parts[0] in {'src','tests','app','params'}: keep.append((p,n,parts[0]))
    elif p.startswith('data/fixtures/'): keep.append((p,n,'fixtures'))
    elif p in {'.gitignore','.python-version','pyproject.toml','uv.lock','conftest.py'}: keep.append((p,n,'root-config'))
    elif parts[0]=='experiments':
        # Include source programs referenced by tests and reproducible generation; skip copied epochs and local fleet operations.
        excluded=any(x in {'fleet','review','background-1028','solver-diag-1028','checker','cache-warm'} for x in parts[1:])
        if p in tracked or (Path(p).suffix in {'.py','.sh'} and not excluded): keep.append((p,n,'experiments'))
        else: hold.append((p,n,'scratch/generated/review evidence; curate measured demo bundle separately'))
    else: hold.append((p,n,'local-only or not selected'))
by=collections.defaultdict(list)
for p,n,g in keep: by[g].append((p,n))
text=['# Proposed explicit public commit paths (bytes, path)','# Snapshot; no staging performed. All paths conditional on secret/licence clearance.','# Existing tracked deletions must also be reviewed separately (git diff --name-status).','# README-draft.md must be promoted to README.md by its owner; absent root LICENSE/NOTICE/README are not counted.','']
for g, rr in sorted(by.items()):
    text += [f'[{g}] {len(rr)} files; {sum(n for p,n in rr):,} bytes']+[f'{n}\t{p}' for p,n in sorted(rr)]+['']
text += [f'BASE TOTAL: {len(keep)} files; {sum(n for p,n,g in keep):,} bytes', '', '[docs proposal: NOT authorised to stage; review/redact first]']+[f'{n}\t{p}' for p,n in docs]+[f'CONDITIONAL DOCS: {len(docs)} files; {sum(n for p,n in docs):,} bytes',f'BASE + CONDITIONAL DOCS: {sum(n for p,n,g in keep)+sum(n for p,n in docs):,} bytes']
(out/'commit-list.txt').write_text('\n'.join(text)+'\n')
a=['# Inventory audit','',f'Metadata inventory: {len(rows):,} regular nonsymlink files after pruning {len(pruned)} generated directory roots. Tracked index: {len(tracked)} paths. No git mutation.','', '## Proposed groups']
for g,rr in sorted(by.items()): a.append(f'- {g}: {len(rr)} files, {sum(n for p,n in rr):,} bytes')
a += ['',f'Base total: {sum(n for p,n,g in keep):,} bytes ({sum(n for p,n,g in keep)/1_048_576:.2f} MiB). Conditional docs: {sum(n for p,n in docs):,} bytes.','', '## Files above 5 MB (decimal; metadata only; pruned trees not inventoried)']
for p,n in sorted(rows,key=lambda x:-x[1]):
    if n>5_000_000: a.append(f'- {p}: {n:,} bytes; '+('PROPOSED: review size/licence' if any(q==p for q,_,_ in keep) else 'EXCLUDE from base public commit'))
a += ['', '## Proposed experiment exclusions (files and measured bytes by top-level campaign; pruned trees additional)']
drop=collections.defaultdict(list)
for p,n,why in hold:
    if p.startswith('experiments/'): drop['/'.join(Path(p).parts[:2])].append((p,n))
for g,rr in sorted(drop.items()): a.append(f'- {g}: {len(rr)} files / {sum(n for p,n in rr):,} bytes excluded; source programs may separately be included above.')
a += ['', '## Pruned roots (exclude from publication; sizes deliberately not traversed)']+['- '+p for p in sorted(pruned)]
a += ['', '## Conditions and limitations','- Source/test/app/params paths include all current files except generated/ignored material. Tests may reference excluded campaign artifacts; those tests must be skipped or small synthetic fixtures curated before clean-clone verification.','- Current docs are working drafts. Proposed five public docs need editorial, sensitivity, fact and licence review plus explicit user path approval; domain.md in particular may contain expert notes.','- Readme links and default app sample must resolve after selected demo bundle is added. No generated result claims are promoted merely because they exist.','- Current tracked experiment documents retained to avoid silently removing public history; scan clearance still required.','- Inventory is a live working-tree snapshot; concurrent writers mean rerun immediately before approval/commit.','- Ignored model weights stay excluded. Publish original StowLM adapter on Hugging Face with a model card and verified base licence; release asset is alternative. Git LFS is unnecessary for this code-only proposal.','- APPROXIMATION: exclude directory-name-matched generated environments/caches/snapshots wholesale; no claim of complete size accounting for those pruned trees.','- nice -n 10 is denied by sandbox (setpriority Operation not permitted); scans remain single-process metadata work.']
(out/'inventory-audit.md').write_text('\n'.join(a)+'\n')
print('\n'.join(text[-5:])); print('ROWS',len(rows),'PRUNED',len(pruned)); print('GROUPS',[(g,len(rr),sum(n for p,n in rr)) for g,rr in sorted(by.items())])
