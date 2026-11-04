"""Record code, input and selected budget hashes before the 72-map run."""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
from datetime import datetime, timezone

HERE=Path(__file__).resolve().parent


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--profile',default='PROFILE.json')
    parser.add_argument('--arms',nargs='+',default=['layover'])
    parser.add_argument('--output',default='FREEZE.json')
    parser.add_argument('--stored-pages-only',action='store_true',
                        help='Pin unchanged archived C4 Pages rows by bytes; do not parse their contents')
    args=parser.parse_args()
    spec=importlib.util.spec_from_file_location('_c6_freeze_runner',HERE/'runner.py')
    runtime=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    profile=(HERE/args.profile).resolve()
    destination=(HERE/args.output).resolve()
    if not profile.is_relative_to(HERE) or not destination.is_relative_to(HERE):
        raise ValueError('Freeze profile and output must remain local')
    if destination.exists():
        raise ValueError('Freeze record already exists; choose a new version explicitly')
    manifest=json.loads((HERE/'cohort/MANIFEST.json').read_text())
    hashes={record['seed']:hashlib.sha256(runtime.input_path(record).read_bytes()).hexdigest()
            for record in manifest['maps']}
    if len(hashes)!=72 or any(hashes[r['seed']]!=r['sha256'] for r in manifest['maps']):
        raise ValueError('Cohort does not match predeclared 72 inputs')
    record=dict(frozen_utc=datetime.now(timezone.utc).isoformat(), arms=args.arms,
        profile=str(profile.relative_to(HERE)),profile_sha256=hashlib.sha256(profile.read_bytes()).hexdigest(),
        cohort_manifest_sha256=hashlib.sha256((HERE/'cohort/MANIFEST.json').read_bytes()).hexdigest(),
        development_sha256=hashlib.sha256((HERE/'DEV.json').read_bytes()).hexdigest(),
        input_sha256=hashes,source_sha256={arm:runtime.provenance(arm) for arm in args.arms})
    if args.stored_pages_only:
        directory=HERE/'results/stored-cycle4/layover'
        files={path.name:path for path in directory.glob('*.json')}
        expected={Path(item['path']).name for item in manifest['maps']}
        if set(files)!=expected:
            raise ValueError('Archived Pages requires exactly the same 72 map filenames')
        # Bytes may be pinned before measurement. Row JSON contents are not
        # opened for analysis until this formal freeze has been written.
        native={key:value for key,value in runtime.provenance(args.arms[0]).items()
                if key.startswith('src/stowage/') or key.startswith('params/')}
        record['archived_pages']=dict(directory=str(directory.relative_to(HERE)),
            rows_sha256={name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in sorted(files.items())},
            native_source_sha256=native,
            mode='unchanged archived cold C4 Pages; failed tier reports unavailable; no fresh speed comparison')
    runtime.dump(destination,record)
    print(json.dumps(dict(freeze=str(destination),arms=args.arms,maps=len(hashes))))


if __name__=='__main__':
    main()
