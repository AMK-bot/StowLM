"""Pin the promoted v2 epoch after root declares sources frozen; no jobs started."""
import json
from datetime import datetime, timezone
from worker import HERE, save, sha, snapshot, canonical


def main():
    target=HERE/'runtime-freeze-v2.json'
    assert not target.exists(), 'Use a new labelled epoch rather than overwrite a freeze'
    old=json.loads((HERE/'runtime-freeze-v1.json').read_text());base=snapshot(False);current=snapshot(True)
    assert base==old['base_sources'], 'Non-lookahead production sources changed; needs separate review'
    assert current!=old['lookahead_runtime_sources'], 'V2 has not been promoted'
    changed=[key for key in current.keys()|old['lookahead_runtime_sources'].keys() if current.get(key)!=old['lookahead_runtime_sources'].get(key)]
    assert all(key.startswith('src/llm/lookahead/')for key in changed)
    save(target,dict(created_utc=datetime.now(timezone.utc).isoformat(),base_sources=base,lookahead_runtime_sources=current,
                     epoch=canonical(current),worker_sha256=sha(HERE/'worker.py'),paired_worker_sha256=sha(HERE/'paired_v2.py'),
                     controller_sha256=sha(HERE/'controller.py'),v1_changed_files=changed,
                     policy_order='production -> lookahead -> securing outer',policy='Independent v2 source epoch; no source exclusions.'))
    print(json.dumps(dict(frozen=True,epoch=canonical(current),changed=changed)))


if __name__ == '__main__':
    main()
