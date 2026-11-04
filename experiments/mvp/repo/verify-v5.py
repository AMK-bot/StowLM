"""Verify v5 artifact and scratch-snapshot consistency, without application imports."""
from pathlib import Path
import ast
import hashlib
import json
import re

HERE=Path(__file__).resolve().parent


def main():
    checks=[]
    def check(name,condition):
        checks.append(dict(name=name,passed=bool(condition)))
    report=json.loads((HERE/'generated-v5/closure.json').read_text())
    rows=report['rows']; names={r['path'] for r in rows}
    check('unique candidates',len(rows)==len(names))
    check('metadata totals',report['selected']==len(rows) and report['bytes']==sum(r['bytes'] for r in rows))
    check('no static dependency errors',not report['errors'])
    forbidden={'docs','models','.git','.venv','.uv-cache','RCSPPSuite','rcsppsuite','test-v3','test_v3','sealed','sealed-v2','sealed-v3'}
    check('forbidden path components excluded',all(not(forbidden & set(Path(p).parts)) for p in names))
    check('private files excluded',all(Path(p).name not in {'CLAUDE.md','.env','PROJECT.docx'} for p in names))
    check('no raw data or prep files selected',all(not p.startswith(('data/raw/','experiments/mvp/repo/')) for p in names))
    check('no tensors or generated audio selected',all(Path(p).suffix.lower() not in {'.safetensors','.gguf','.pt','.pth','.wav','.mp3','.pem','.key'} for p in names))
    check('held paths omitted',not(names & {r['path'] for r in report['publication_holds']}))
    parsed=[]
    for line in (HERE/'candidate-paths-v5.txt').read_text().splitlines():
        if re.match(r'^\d+\t',line):
            size,path=line.split('\t',1);parsed.append((path,int(size)))
    check('explicit list matches hashes manifest',sorted(parsed)==sorted((r['path'],r['bytes']) for r in rows))
    frozen=Path(report['pinned_source'])
    check('pinned source complete and byte matched',all((frozen/r['path']).is_file() and hashlib.sha256((frozen/r['path']).read_bytes()).hexdigest()==r['sha256'] for r in rows))
    home=re.compile(rb'/Users/|/home/|C:\\\\Users\\\\')
    check('no absolute home paths in selected bytes',all(not home.search((frozen/r['path']).read_bytes()) for r in rows))
    docs=['COMMIT-PROPOSAL-V5.md','LICENCE-CHECK-V5.md','MODEL-CARD-READER-DRAFT.md','USER-ITEMS-0.10-V5.md','FRESH-CLONE-V5.md','V5-APPROVAL-PACKET.md']
    check('required review documents exist',all((HERE/p).is_file() for p in docs))
    for name in ['prepare-v5.py','run-readme-v5.py','verify-v5.py']:
        ast.parse((HERE/name).read_text());check(name+' syntax',True)
    card=(HERE/'MODEL-CARD-READER-DRAFT.md').read_text()
    for marker in ['base_model: meta-models/Muse-Glimmer-30B','{{ADAPTER_SHA256}}','{{BASE_REVISION}}','118/128','transportation','{{VERIFIED_INSTALL_COMMAND}}']:
        check('model card marker '+marker,marker in card)
    run=json.loads((HERE/'readme-run-v5.json').read_text())
    check('fresh copy complete',not run['copy_errors'] and run['copy_count']==len(rows))
    check('run manifest binding',run['manifest_sha256']==hashlib.sha256((HERE/'candidate-paths-v5.txt').read_bytes()).hexdigest())
    check('run receipt closed',run.get('completed') is True)
    attempted=[r for r in run['jobs'] if r.get('admission')]
    check('each admitted run closed and reaped',all(r.get('reaped') and r['status']!='running' for r in attempted))
    check('admission respected',all(r['admission']['mask_cores']>=8 and r['admission']['load1']<=45 and 0<=r['admission']['age_s']<120 for r in attempted))
    for name in docs:
        s=(HERE/name).read_text()
        check(name+' no home paths',not home.search(s.encode()))
        check(name+' code fences balanced',sum(line.startswith('```') for line in s.splitlines())%2==0)
    result=dict(checks=checks,passed=sum(c['passed'] for c in checks),total=len(checks),
        scope='Artifact consistency only. README/application failures remain failures; these checks do not clear rights or gates.')
    (HERE/'v5-verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return 0 if all(c['passed'] for c in checks) else 1


if __name__=='__main__':raise SystemExit(main())
