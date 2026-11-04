"""Install only D120 files after revalidating both complete guards and live epoch."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import tempfile

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition,message):
    if not condition:
        raise RuntimeError(message)


def live_manifest(root):
    """Match the decode manifest filter, including newly added files."""
    files={}
    for folder in (root/'src',root/'params'):
        for path in sorted(folder.rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                files[str(path.relative_to(root))]=sha(path)
    files['pyproject.toml']=sha(root/'pyproject.toml')
    return files


def require_live_epoch(root,expected):
    current=live_manifest(root)
    drift=sorted(name for name in current.keys() | expected.keys()
                 if current.get(name)!=expected.get(name))
    require(not drift,f'Live source/parameter drift since guard: {drift}')


def bind_checker_sources(config,decode_source):
    pins=config['source_pins']
    require(bool(pins),'Missing checker source pins')
    drift=sorted(name for name,digest in pins.items() if decode_source.get(name)!=digest)
    require(not drift,f'Checker/decode source epochs differ: {drift}')


def require_target(path,expected):
    """Recheck immediately before replacing a target; never accept foreign data."""
    if expected is None:
        require(not path.exists() and not path.is_symlink(),f'New file collision: {path}')
    else:
        require(path.is_file() and not path.is_symlink() and sha(path)==expected,
                f'Promotion target changed concurrently: {path}')


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def main(apply):
    checker=module('d120_checker_summary',HERE/'checker/summarize.py')
    checked=checker.summarize(HERE/'checker/before',HERE/'checker/after')
    require(checked['passed'] and checked['exact_reports']>=100,'Checker guard incomplete')
    decode=module('d120_decode_summary',HERE/'decode/summarize.py')
    before,after=read(HERE/'decode/timing-before.json'),read(HERE/'decode/timing-after.json')
    guard_path=HERE/'decode/guard.json';guard=read(guard_path)
    workers={arm:read(guard_path.with_name(f'guard-{arm}-worker.json')) for arm in ('before','after')}
    timing=decode.summarize(before,after)
    qualification=decode.qualify(before,after,guard,workers,read(HERE/'decode/harness-manifest.json'))
    require(qualification['guard_qualified'] and timing['exact_trajectories'],'Decode guard incomplete')
    for arm,run in (('before',before),('after',after)):
        bind_checker_sources(read(HERE/f'checker/{arm}/config.json'),run['source_before'])
    # Other executives own the rest of the tree. Detect drift, never overwrite
    # it or install an unreviewed combination of cache and physical behavior.
    require_live_epoch(ROOT,before['source_before'])
    manifest=read(HERE/'candidate-manifest.json')
    paths=manifest['changes']
    require(sorted(paths)==sorted(qualification['changed_runtime_paths']),'Candidate path delta differs')
    require(all(name in before['source_before'] or not (ROOT/name).exists() for name in paths),
            'New production file collides with foreign work')
    require(all(sha(HERE/'candidate-runtime'/name)==manifest['files'][name] for name in paths),
            'Candidate bytes differ from manifest')
    edits={ROOT/name:(HERE/'candidate-runtime'/name).read_bytes() for name in paths}
    require(all(hashlib.sha256(edits[ROOT/name]).hexdigest()==manifest['files'][name]
                for name in paths),'Candidate changed while reading promotion bytes')
    expected={ROOT/name:before['source_before'].get(name) for name in paths}
    for name in ('test_profiles.py','test_next_call_environments.py','test_yard_blocking_height.py'):
        path=ROOT/'tests/ports'/name
        old_bytes=path.read_bytes()
        old=old_bytes.decode('utf-8')
        new=old.replace('profiles.tomllib, "loads"','profiles, "load_toml_bytes"')
        new=new.replace('profiles.tomllib.loads','profiles.load_toml_bytes')
        require(new!=old,f'Expected parser mock boundary missing: {path}')
        edits[path]=new.encode()
        expected[path]=hashlib.sha256(old_bytes).hexdigest()
    test=ROOT/'tests/test_toml_cache.py'
    require(not test.exists() and not test.is_symlink(),
            'New test path already exists; do not overwrite foreign work')
    edits[test]=(HERE/'candidate/tests/test_toml_cache.py').read_bytes()
    expected[test]=None
    result=dict(ready=True,applied=False,guard_traces=len(guard['traces']),
                checker_exact_reports=checked['exact_reports'],
                files={str(p.relative_to(ROOT)):hashlib.sha256(data).hexdigest() for p,data in edits.items()})
    if apply:
        require_live_epoch(ROOT,before['source_before'])
        for path in edits:
            require_target(path,expected[path])
        backup=HERE/'promotion-backup';backup.mkdir(exist_ok=False)
        # Install the independent module first so every intermediate import is valid.
        ordered=sorted(edits,key=lambda p:(p.name!='toml_cache.py',str(p)))
        for path in ordered:
            require_target(path,expected[path])
            relative=path.relative_to(ROOT)
            if path.exists():
                target=backup/relative;target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(path,target)
            mode=stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
            with tempfile.NamedTemporaryFile(dir=path.parent,prefix=f'.{path.name}.d120-',
                                             delete=False) as stream:
                temporary=Path(stream.name)
                stream.write(edits[path])
            try:
                temporary.chmod(mode)
                require_target(path,expected[path])
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        result['applied']=True
        require(all(sha(ROOT/name)==digest for name,digest in result['files'].items()),
                'Installed content differs from promotion plan')
    (HERE/'promotion.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    main(parser.parse_args().apply)
