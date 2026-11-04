"""Copy only the frozen proposed path list; no git or network operation."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'experiments/mvp/repo'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    began = time.monotonic()
    proposal = OUT / 'commit-list.txt'
    listed = proposal.read_bytes()
    rows = []
    group = ''
    for line in listed.decode().splitlines():
        if line.startswith('['):
            group = line.split(']', 1)[0][1:]
        if '\t' not in line:
            continue
        count, name = line.split('\t', 1)
        if count.isdigit():
            relative = Path(name)
            assert not relative.is_absolute() and '..' not in relative.parts
            assert relative.parts[0] not in {'.git', 'models'}
            assert relative.name not in {'.env', 'CLAUDE.md'}
            rows.append((name, int(count), group))
    assert len(rows) == len({row[0] for row in rows})
    scratch = Path(tempfile.mkdtemp(prefix='stowlm-judge-99-3-', dir='/private/tmp'))
    tree = scratch / 'proposed'
    tree.mkdir()
    (scratch / 'logs').mkdir()
    entries, missing, changed = [], [], []
    for name, expected_bytes, group in rows:
        source = ROOT / name
        if not source.is_file():
            missing.append(name)
            continue
        assert source.resolve().is_relative_to(ROOT)
        destination = tree / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Pin complete bytes, not a mixture of an overlapping writer's versions.
        for attempt in range(3):
            raw = source.read_bytes()
            destination.write_bytes(raw)
            if source.read_bytes() == raw:
                break
        else:
            raise RuntimeError(f'Source kept changing during copy: {name}')
        assert destination.read_bytes() == raw
        if len(raw) != expected_bytes:
            changed.append(dict(path=name, proposed_bytes=expected_bytes, copied_bytes=len(raw)))
        entries.append(dict(path=name, sha256=sha(raw), bytes=len(raw), group=group))
    ignore = tree / '.gitignore'
    original = ignore.read_bytes()
    additions = (OUT / 'gitignore.additions').read_bytes()
    ignore.write_bytes(original + b'\n# Fresh-clone rehearsal: drafted additions\n' + additions)
    record = dict(started_utc=datetime.now(timezone.utc).isoformat(), scratch=str(scratch),
        tree=str(tree), candidate_path_file='experiments/mvp/repo/commit-list.txt',
        candidate_sha256=sha(listed), selected=len(rows), copied=len(entries), missing=missing,
        conditional_docs=sum(group.startswith('docs') for _, _, group in rows),
        copied_bytes=sum(row['bytes'] for row in entries), size_drift=changed,
        gitignore=dict(original_sha256=sha(original), additions_sha256=sha(additions),
                       combined_sha256=sha(ignore.read_bytes())),
        rows=entries, wall_s=time.monotonic()-began,
        assumptions=['Numeric TAB rows are the candidate-path file; no separate candidate-path file exists.',
                     'Include five conditional docs locally; copying is not publication approval.',
                     'Explicit proposed paths are copied even if drafted ignore rules match, as tracked paths would be.'],
        no_git_directory=True, no_network=True)
    (OUT / 'fresh-clone-copy.json').write_text(json.dumps(record, indent=2, sort_keys=True)+'\n')
    (scratch / 'copy-manifest.json').write_text(json.dumps(record, indent=2, sort_keys=True)+'\n')
    print(json.dumps({key: record[key] for key in ['scratch', 'tree', 'selected', 'copied', 'missing', 'copied_bytes', 'wall_s']}))


if __name__ == '__main__':
    main()
