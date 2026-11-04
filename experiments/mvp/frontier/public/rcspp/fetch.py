"""Download RCSPPSuite (Sivertsen, Reinhardt, Jensen 2025) from its public share into a folder git ignores.

The data has no licence and is anonymised real vessel and cargo data, used here for evaluation only by the user's decision (D197).
It stays out of git, is never used for training, and only derived numbers go into the repo. Files are stored read-only and nothing
downloaded is ever run; RcsppSuiteReader.py is kept as text.

Usage: fetch.py <empty_or_partial_dir>
"""
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.request

SHARE = 'VfEPKOfYV2'
BASE = f'https://kilia.egnyte.com/rest/public/1.0/links/info/{SHARE}/contents'
FILE = f'https://kilia.egnyte.com/dd/{SHARE}/?entryId={{}}&forceDownload=true'


def listing(path):
    """The share lists 50 items a call, so page by offset and insist on the folder's own count."""
    found, total = [], None
    while total is None or len(found) < total:
        body = json.dumps({'path': path, 'offset': len(found)}).encode()
        with urllib.request.urlopen(urllib.request.Request(BASE, body, {'Content-Type': 'application/json'}), timeout=60) as reply:
            contents = json.load(reply)['contents']
        total = contents['count']
        if not contents['results']:
            raise RuntimeError(f'{path}: listing stopped at {len(found)} of {total}')
        found += contents['results']
    return found


def files(path):
    for item in listing(path):
        if item['type'] == 'folder':
            yield from files(item['path'])
        else:
            yield item


def main():
    root = Path(sys.argv[1])
    manifest = []
    for item in files('/RCSPPSuite'):
        target = root/item['path'].removeprefix('/RCSPPSuite/')
        size = item.get('size')
        if not target.exists() or (size and target.stat().st_size != size):
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_suffix(target.suffix+'.part')
            with urllib.request.urlopen(FILE.format(item['entryId']), timeout=300) as reply, open(part, 'wb') as out:
                while chunk := reply.read(1 << 20):
                    out.write(chunk)
            part.rename(target)
            target.chmod(0o444)
            time.sleep(.5)
        manifest.append({'path': str(target.relative_to(root)), 'size': target.stat().st_size, 'listed_size': size,
                         'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'entryId': item['entryId']})
        print(manifest[-1]['path'], manifest[-1]['size'], flush=True)
    (root/'manifest.json').write_text(json.dumps(manifest, indent=1))
    print(len(manifest), 'files', sum(m['size'] for m in manifest), 'bytes')


if __name__ == '__main__':
    main()
