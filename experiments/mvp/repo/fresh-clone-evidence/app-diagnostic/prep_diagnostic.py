import hashlib,json,pathlib,shutil,time
base=pathlib.Path('/private/tmp/stowlm-judge-99-3-xzmzsouq')
source=pathlib.Path('.')
dest=base/'diagnostic-code-only'
assert not dest.exists()
started=time.monotonic()
shutil.copytree(base/'proposed',dest,ignore=shutil.ignore_patterns('.venv','__pycache__','.pytest_cache'))
paths=['app/stage_fast.py','app/stage_extras.py','app/instruct.py','src/stowage/solver/securing_screen.py','experiments/mvp/rules.json']
for prefix in ['src/llm/securing','src/llm/lookahead','src/llm/instructions']:
 paths.extend(str(p.relative_to(source)) for p in sorted((source/prefix).glob('*.py')))
rows=[]
for name in sorted(set(paths)):
 p=source/name;q=dest/name
 if q.exists():
  assert p.read_bytes()==q.read_bytes(),f'existing baseline source differs: {name}'
  continue
 q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q)
 rows.append({'path':name,'bytes':q.stat().st_size,'sha256':hashlib.sha256(q.read_bytes()).hexdigest()})
(base/'logs/diagnostic-additions.json').write_text(json.dumps({'variant':str(dest),'classification':'diagnostic code/static/rule-display additions only; NOT exact proposed clone','files':rows,'copy_wall_s':time.monotonic()-started},indent=2)+'\n')
print(json.dumps({'variant':str(dest),'added_files':len(rows),'wall_s':time.monotonic()-started}),flush=True)
