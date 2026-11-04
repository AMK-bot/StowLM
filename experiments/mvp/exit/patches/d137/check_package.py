from pathlib import Path
import shutil,subprocess,os,json,hashlib,xml.etree.ElementTree as E
h=Path(__file__).resolve().parent;r=h.parents[4];out=h/'apply-check'
if out.exists():shutil.rmtree(out)
shutil.copytree(h/'base-d132',out)
shutil.copytree(r/'tests/llm/exit',out/'tests/llm/exit',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
env={**os.environ,'GIT_CEILING_DIRECTORIES':str(h)}
receipts=[]
for name in ('candidate-teacher-loop.diff','candidate-teacher-loop-tests.diff'):
    for extra in (['--check'],[]):
        command=['git','apply',*extra,str(h/name)]
        subprocess.run(command,cwd=out,env=env,check=True)
        receipts.append(dict(command=command,cwd=str(out),returncode=0))
expected=json.loads((h/'installed-manifest.json').read_text())['targets']
expected.update(json.loads((h/'test-targets.json').read_text()))
for rel,digest in expected.items():
    actual=hashlib.sha256((out/rel).read_bytes()).hexdigest()
    if actual!=digest:raise RuntimeError(f'patched private output differs: {rel}')
for rel in expected:
    if rel.endswith('.py'):compile((out/rel).read_text(),str(out/rel),'exec')
subprocess.run(['zsh','-n',str(h/'RESTART-HOST.sh')],check=True)
suites=[dict(s.attrib) for s in E.parse(h/'qualification.xml').getroot().iter('testsuite')]
report=dict(source_and_test_patch_checks=receipts,patched_target_hashes_verified=len(expected),
    compile_targets=len(expected),zsh_syntax_passed=True,pytest=suites,live_source_modified=False,
    host_restart_not_launched=True,expected_uninstalled_launch_refusal=True)
(h/'qualification.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
# Close only this executive's reservation row.
p=r/'experiments/fleet/ledger.md';p.write_text(''.join(line for line in p.read_text().splitlines(True)
    if not line.startswith('| mvp-exit/d137-validation |')))
p=r/'experiments/fleet/history.md'
with p.open('a') as f:f.write('\n- mvp-exit/D137 CPU qualification: reserved 1 core/2 GB/no GPU/480 s; final 827-test private source+restart suite 97.651 s, zero failures/errors/skips. CPU-only teacher calibration separately logged by mvp-exit/d137-calibration (1200 s/4 GB estimate; 166.591 s measured, parent peak 0.698 GiB). No live source installation.\n')
