"""Build private ExIt overlay and a patch; never modify production source."""
from pathlib import Path
import shutil,difflib,json,hashlib
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[4]
OUT=HERE/'assembled'
base=HERE/'base-d132'
(OUT/'src/llm/exit').mkdir(parents=True,exist_ok=True)
for p in (ROOT/'src/llm/exit').glob('*.py'):
    shutil.copy2(p,OUT/'src/llm/exit'/p.name)
for p in (base/'src/llm/exit').glob('*.py'):
    shutil.copy2(p,OUT/'src/llm/exit'/p.name)
replacements={
    'data.py':HERE/'maps-control/data.py','control.py':HERE/'maps-control/control.py',
    'selection.py':HERE/'candidates/selection.py','preferences.py':HERE/'candidates/preferences.py',
    'reverify.py':HERE/'candidates/reverify.py','runner.py':HERE/'runner.py','audit.py':HERE/'audit.py'}
if (HERE/'sizing.py').exists():replacements['sizing.py']=HERE/'sizing.py'
if (HERE/'final_recipe.py').exists():replacements['final_recipe.py']=HERE/'final_recipe.py'
for name,path in replacements.items():shutil.copy2(path,OUT/'src/llm/exit'/name)
# A regular llm parent package extends imports into production for read-only dependencies.
(OUT/'src/llm/__init__.py').write_text('from pkgutil import extend_path\n__path__ = extend_path(__path__, __name__)\n')
patch=[];targets={};before={}
for name,path in sorted(replacements.items()):
    rel='src/llm/exit/'+name
    old=(base/rel).read_text() if (base/rel).exists() else ''
    new=path.read_text()
    if old==new:continue
    patch.extend(difflib.unified_diff(old.splitlines(True),new.splitlines(True),
        fromfile='a/'+rel if old else '/dev/null',tofile='b/'+rel))
    targets[rel]=hashlib.sha256(new.encode()).hexdigest()
    before[rel]=hashlib.sha256(old.encode()).hexdigest() if old else None
(HERE/'candidate-teacher-loop.diff').write_text(''.join(patch))
for p in (base/'src/llm/exit').glob('*.py'):
    rel='src/llm/exit/'+p.name
    targets.setdefault(rel,hashlib.sha256((OUT/rel).read_bytes()).hexdigest())
(HERE/'installed-manifest.json').write_text(json.dumps(dict(schema='exit-d137-installed-v1',targets=targets,
    before=before,prerequisites=['D132 parallel dataset','D132 outcome ACK','D136 checkpoint suffix'],
    production_installed=False),indent=2)+'\n')
print(json.dumps(dict(overlay=str(OUT),targets=len(targets))))
