import json

from . import operations


def test_operations_survives_each_table_refresh_without_claiming_execution(tmp_path, monkeypatch):
    here = tmp_path/'binding'; here.mkdir()
    output = here/'campaign'; output.mkdir()
    status = here/'operations.json'
    status.write_text(json.dumps({'message': 'Two workers stalled; four Reader executions remain queued.'}))
    monkeypatch.setattr(operations, 'HERE', here)
    monkeypatch.setattr(operations, 'STATUS', status)
    def base(path):
        for target in (output/'RESULTS.md', here/'RESULTS.md', here/'RESULTS-LIVE.md'):
            target.write_text('Measured rows: 9\n')
        return {'current_finalized_rows': 9}
    refresh = operations.wrap(base)
    assert operations.wrap(refresh) is refresh
    for _ in range(2):
        state = refresh(output)
        assert state['current_finalized_rows'] == 9
        assert 'remain queued' in state['operational_limitations']['message']
        text = (here/'RESULTS.md').read_text()
        assert text.count('## Operational limitation') == 1
        assert 'remain queued' in text
