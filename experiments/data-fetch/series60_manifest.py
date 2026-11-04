"""Hash inert Series60 downloads; no downloaded code is imported or run."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json

root = Path(__file__).resolve().parents[2] / 'data/raw/series60'
base = 'https://archive.org/download/DTIC_AD0419990/'
urls = {
    'todd1963-series60.pdf': base + 'DTIC_AD0419990.pdf',
    'todd1963-series60.txt': base + 'DTIC_AD0419990_djvu.txt',
    'archive-metadata.json': 'https://archive.org/metadata/DTIC_AD0419990',
}
manifest = [dict(file=name, url=url, retrieved=datetime.now(timezone.utc).isoformat(),
                 licence='public domain (US federal government report; BHL catalogue status)',
                 sha256=hashlib.sha256((root/name).read_bytes()).hexdigest())
            for name, url in urls.items()]
(root/'MANIFEST').write_text(json.dumps(manifest, indent=2) + '\n')
print(f'{len(manifest)} payloads hashed')
