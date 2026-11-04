"""Audit only the frozen scratch source, never execute proposed programs."""
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
copy = json.loads((HERE / 'fresh-clone-copy.json').read_text())
tree = Path(copy['tree'])
selected = {row['path'] for row in copy['rows']}
began = time.monotonic()
syntax_errors, absolute_literals = [], []
python_files = 0
for row in copy['rows']:
    name = row['path']
    if not name.endswith('.py'):
        continue
    python_files += 1
    path = tree / name
    try:
        module = ast.parse(path.read_text(), filename=name)
    except (SyntaxError, UnicodeError) as exc:
        syntax_errors.append(dict(path=name, line=getattr(exc, 'lineno', None),
                                  error_type=type(exc).__name__, message=getattr(exc, 'msg', str(exc))))
        continue
    for node in ast.walk(module):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.match(r'^/(Users|home)/', node.value):
            # Locations only: never copy literal source/credential values to the report.
            absolute_literals.append(dict(path=name, line=node.lineno))

omitted = []
for folder in ('src', 'app'):
    for path in (REPO / folder).rglob('*'):
        if not path.is_file() or '__pycache__' in path.parts:
            continue
        relative = path.relative_to(REPO).as_posix()
        if path.suffix == '.py' or relative.startswith('app/static/'):
            if relative not in selected:
                omitted.append(relative)

record = dict(created_utc=datetime.now(timezone.utc).isoformat(),
    proposal_sha256=copy['candidate_sha256'], python_files_parsed=python_files,
    syntax_errors=syntax_errors, absolute_literal_locations=absolute_literals,
    omitted_current_python_or_app_static_paths=sorted(omitted),
    no_test_input_bodies_in_candidate=not any('/corpus/test/' in name for name in selected),
    wall_s=time.monotonic()-began,
    limits='Static source-literal locations are not proven runtime failures. Current-tree omission inventory can drift; the copied source is pinned by fresh-clone-copy.json.')
(HERE / 'fresh-clone-static.json').write_text(json.dumps(record, indent=2, sort_keys=True)+'\n')
print(json.dumps({key: record[key] for key in ['python_files_parsed', 'syntax_errors', 'wall_s']}))
print(json.dumps(dict(absolute_literal_count=len(absolute_literals), omitted_count=len(omitted))))
