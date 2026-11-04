"""Make one capped Spark reading and preserve it before validating the JSON."""
from dataclasses import asdict
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path

from .spark_reader import MultimodalSparkClient, read
from experiments.mvp.pipeline.contract.schema import sha256_file


def record_case(case):
    case = Path(case).resolve()
    raw_path = case / 'spark_raw.json'
    response_path = case / 'spark_response.json'
    if raw_path.exists() or response_path.exists():
        raise ValueError('A reading already exists; recordings are never overwritten')
    manifest = json.loads((case.parent / 'freeze.json').read_text())
    frozen = next(item for item in manifest['cases'] if item['split'] == case.name)
    for name, digest in frozen['files'].items():
        if sha256_file(case / name) != digest:
            raise ValueError('A frozen input changed; no request sent')
    if case.name == 'heldout' or str(manifest.get('evaluation_split', '')).startswith('heldout'):
        from .evaluate_v2 import verify_frozen
        verify_frozen(case.parent)
    documents = json.loads((case / 'documents.json').read_text())
    for document in documents:
        if document.get('path') and not Path(document['path']).exists():
            document['path'] = str(case / document['path'])

    class Capture(MultimodalSparkClient):
        def complete_documents(self, *args, **kwargs):
            response = super().complete_documents(*args, **kwargs)
            data = {'text': response.text, 'usage': asdict(response.usage),
                    'cost_usd': float(response.cost_usd), 'request_id': response.request_id,
                    'recorded_utc': datetime.now(timezone.utc).isoformat()}
            response_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
            return response

    raw = read(documents, client=Capture())
    raw_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + '\n')
    return {key: value for key, value in raw['metadata'].items() if key != 'raw_text'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case', type=Path)
    args = parser.parse_args()
    print(json.dumps(record_case(args.case), indent=2))
