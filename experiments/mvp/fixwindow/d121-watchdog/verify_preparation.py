"""Hash-only preparation audit; never imports or applies live project code."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
manifest = json.loads((HERE/'PATCHES.json').read_text())
rows = []
for entry in manifest['files']:
    label = entry['patch'].removesuffix('-watchdog.patch')
    source_sha = sha256((ROOT/entry['target']).read_bytes()).hexdigest()
    candidate_sha = sha256((HERE/f'{label}-candidate.py').read_bytes()).hexdigest()
    rows.append(dict(**entry, patch_sha256=sha256((HERE/entry['patch']).read_bytes()).hexdigest(),
                     source_unchanged=source_sha == entry['source_sha256'],
                     candidate_unchanged=candidate_sha == entry['candidate_sha256']))
command = ['git', 'apply', '--check', *[str(HERE/row['patch']) for row in rows]]
checked = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
suites = [node.attrib for node in ET.parse(HERE/'helper-tests-verified.xml').iter('testsuite')]
result = dict(applied=False, patches=rows, read_only_check_command=command,
              read_only_check_exit_code=checked.returncode,
              read_only_check_stderr=checked.stderr, test_suites=suites,
              all_commands_completed=True, owned_background_processes=0,
              actual_signals_used=False, numerical_builders_used=False)
result['passed'] = (checked.returncode == 0 and all(row['source_unchanged'] and row['candidate_unchanged'] for row in rows)
                    and all(node['errors'] == node['failures'] == '0' for node in suites))
(HERE/'VERIFY.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps({'passed': result['passed'], 'patches': len(rows), 'applied': False}))
raise SystemExit(0 if result['passed'] else 1)
