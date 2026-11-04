"""Positive end-to-end controls with adequate independent shipping evidence."""
import json
from pathlib import Path

from . import read_documents
from .spark_reader import RecordedClient


def test_recorded_mixed_dg_lq_mp_two_uns_and_reefer_accept_end_to_end():
    case=Path(__file__).parent/'fixtures/realistic_v7/development_final'
    original=Path(__file__).parent/'fixtures/realistic_v7/development_01'
    expected=json.loads((original/'expected.json').read_text())
    documents=json.loads((original/'documents.json').read_text())
    # A second independently supplied SI declaration remedies the public
    # packet's deliberately insufficient single EIR in-gauge observation.
    # This is generated unit evidence, not part of any measured model request.
    documents.append({'id':'synthetic-supplemental-si','mime_type':'text/csv',
        'role':'shipping_instructions','synthetic':True,
        'text':'id,oog\n'+''.join(f'{r["id"]},NO\n' for r in expected['rows'])})
    result=read_documents(documents,base_instance=json.loads((original/'base_instance.json').read_text()),
                          client=RecordedClient(case/'spark_raw.json'))
    actual={r['id']:r for r in result.rows}
    clean=[r for r in expected['rows'] if r['id'] in expected['clean_control_ids']]
    for row in clean:
        assert not [q for q in result.questions if q.row is None or result.rows[q.row]['id']==row['id']]
        assert actual[row['id']]==row
    assert any(r['dg'] and r['dg']['additional'] for r in clean)
    assert any(r['dg'] and r['dg']['limited_quantity'] and r['dg']['marine_pollutant'] for r in clean)
    assert any(r['reefer'] and r['reefer_setpoint_c']==-18 for r in clean)
