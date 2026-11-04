from pathlib import Path
import difflib,json,hashlib
h=Path(__file__).resolve().parent;r=h.parents[4];v=h/'validation';t=v/'tests/llm/exit'
source=(h/'audit-tests/test_baseline_label.py').read_text()
start=source.index("PATH=Path(__file__)");end=source.index('\n\ndef rows(',start)
source=source[:start]+'from llm.exit import audit as A\n'+source[end:]
(t/'test_d137_baseline_label.py').write_text(source)
source=(h/'measure/test_sizing.py').read_text();start=source.index('SPEC=');end=source.index('\n\ndef settings',start)
source=source[:start]+'from llm.exit import sizing as module\n'+source[end:]
(t/'test_d137_sizing.py').write_text(source)
source=(h/'test_runner_integration.py').read_text();start=source.index('HERE=');end=source.index('\n@pytest.fixture',start)
source=source[:start]+"ROOT=next(p for p in Path(__file__).resolve().parents if (p/'pyproject.toml').is_file())\nfrom llm.exit import runner as R, selection as S\n"+source[end:]
(t/'test_d137_runner_integration.py').write_text(source)
source=(h/'final-recipe-tests/test_single_finisher.py').read_text()
start=source.index('PATH=');end=source.index('\n\n@pytest.fixture',start)
source=source[:start]+'from llm.exit import final_recipe as F\n'+source[end:]
(t/'test_d139_single_finisher.py').write_text(source)
parts=[];targets={}
for p in sorted(t.glob('*.py')):
    rel='tests/llm/exit/'+p.name;old=(r/rel).read_text() if (r/rel).exists() else '';new=p.read_text()
    if old==new:continue
    parts.extend(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/'+rel if old else '/dev/null',tofile='b/'+rel))
    targets[rel]=hashlib.sha256(new.encode()).hexdigest()
(h/'candidate-teacher-loop-tests.diff').write_text(''.join(parts))
(h/'test-targets.json').write_text(json.dumps(targets,indent=2)+'\n')
print(json.dumps(dict(changed_test_files=len(targets))))
