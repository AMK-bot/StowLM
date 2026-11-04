from pathlib import Path
import subprocess, collections
out=Path('experiments/mvp/repo'); txt=(out/'commit-list.txt').read_text(); keep=collections.defaultdict(list); docs=[]; group=''
for line in txt.splitlines():
    if line.startswith('['): group=line.split(']')[0][1:]
    if '\t' not in line: continue
    n,p=line.split('\t',1)
    if not n.isdigit():continue
    if group.startswith('docs'): docs.append((p,int(n)));continue
    # APPROXIMATION: copied source snapshots and source inside generated environments are scratch.
    parts=Path(p).parts
    if any(('snapshot' in part.lower() or part in {'diagnostic-source','devtree','source-tree','frozen'}) for part in parts[1:-1]): continue
    keep[group].append((p,int(n)))
lines=['# Explicit proposed commit paths: <bytes> TAB <path>','# All paths conditional on secret/licence clearance. No staging performed.','# docs proposal is separately conditional on explicit user path approval.','# README/LICENSE/NOTICE are future root files and excluded from current byte totals.','']
for g,rr in keep.items(): lines += [f'[{g}] {len(rr)} files; {sum(n for p,n in rr):,} bytes']+[f'{n}\t{p}' for p,n in rr]+['']
total=sum(n for rr in keep.values() for p,n in rr);count=sum(map(len,keep.values()))
lines += [f'BASE TOTAL: {count} files; {total:,} bytes','','[docs proposal: NOT authorised to stage; review/redact first]']+[f'{n}\t{p}' for p,n in docs]+[f'CONDITIONAL DOCS: {len(docs)} files; {sum(n for p,n in docs):,} bytes',f'BASE + CONDITIONAL DOCS: {total+sum(n for p,n in docs):,} bytes']
(out/'commit-list.txt').write_text('\n'.join(lines)+'\n')
tracked=set(subprocess.check_output(['git','ls-files','-z']).decode().split('\0'))-{''}; selected={p for rr in keep.values() for p,n in rr}
a=(out/'inventory-audit.md').read_text();a+='\n## Final proposal overrides initial metadata totals above\n'
for g,rr in keep.items():a+=f'- {g}: {len(rr)} files, {sum(n for p,n in rr):,} bytes\n'
a+=f'- FINAL BASE: {count} files, {total:,} bytes. Base + conditional docs: {total+sum(n for p,n in docs):,} bytes.\n'
a+='- No proposed file exceeds 5 MB.\n' if not any(n>5000000 for rr in keep.values() for p,n in rr) else '- Proposed >5 MB files: '+repr([(p,n) for rr in keep.values() for p,n in rr if n>5000000])+'\n'
a+='- KCS-derived fleet params, source hull tables and generated fixture geometry remain blocked on provenance/licence review; the conditional candidate total includes them, not a legal clearance. Clean-clone setup must describe fetching and generating these prerequisites.\n- Existing tracked experiment docs require sensitivity scrubbing before reuse. Current-tree scan does not certify Git history.\n- Tracked index paths not present in final proposal (require author-reviewed handling, .gitignore cannot untrack them):\n'
for p in sorted(tracked-selected):a+='  - '+p+'\n'
a+='- Tracked deletion observed: params/fleet/feeder_1000.toml; include reviewed deletion/alias migration when approving final commit.\n'
(out/'inventory-audit.md').write_text(a)
print('FINAL',count,total,total+sum(n for p,n in docs));print('GT5MB',[(p,n) for rr in keep.values() for p,n in rr if n>5000000]);print('TRACKED_EXCLUDED',len(tracked-selected))
