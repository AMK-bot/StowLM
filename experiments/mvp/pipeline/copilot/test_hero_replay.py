import json
import pytest

from experiments.mvp.pipeline.contract.schema import sha256_file
from experiments.mvp.pipeline.copilot.hero_replay import replay_shot10


def recording(folder):
    files = {}
    for name, value in (('shot10.json', {'reader':'fixture reader'}), ('shot10-facts.json', {'label':'fixture facts'})):
        path = folder/name
        path.write_text(json.dumps(value))
        files[name] = sha256_file(path)
    (folder/'playback-manifest.json').write_text(json.dumps({'files':files}))


def test_hero_playback_preserves_recorded_scope_without_provider_or_native_tools(tmp_path):
    recording(tmp_path)
    value = replay_shot10(folder=tmp_path)
    assert value['mode'] == 'recorded' and value['recorded'] and value['synthetic']
    assert value['conversation']['reader'] == 'fixture reader'
    assert 'does not perform a current check' in value['label']


def test_hero_playback_rejects_changed_conversation(tmp_path):
    recording(tmp_path)
    (tmp_path/'shot10.json').write_text('{}')
    with pytest.raises(ValueError, match='artifact changed'):
        replay_shot10(folder=tmp_path)


def test_default_hero_take_delivers_checked_ranking_and_confirmed_plan_offline(monkeypatch):
    from llm.spark.client import SparkClient
    from experiments.mvp.pipeline.copilot.tools import ToolSet

    def forbidden(*args, **kwargs):
        pytest.fail('Recorded playback invoked a model or tool')

    monkeypatch.setattr(SparkClient, 'complete', forbidden)
    monkeypatch.setattr(ToolSet, 'call', forbidden)
    value = replay_shot10()
    assert value['mode'] == 'recorded' and value['recorded'] and value['synthetic']
    facts = value['facts']
    assert 'Unadapted cloud Spark' in value['conversation']['reader']
    assert facts['ranking_delivered'] is True
    comparison = value['conversation']['turns'][2]
    assert comparison['gate']['reasons'] == []
    assert comparison['raw_say'] in comparison['turn']['say']
    assert 'recommend dg-clearance' in comparison['turn']['say']
    assert all(token in comparison['turn']['say'] for token in ('70.0', '82.0', '12.0 lower'))
    assert value['conversation']['host_confirmation']['planner_confirmed'] is True
    assert [(card['id'], card['label'], card['lifts']) for card in facts['alternatives']] == [
        ('dg-clearance', 'checked', 70.0), ('bay-closure', 'checked', 82.0)]
    assert facts['observed_acceptance'] == [facts['selected_plan_sha256']]
    assert value['conversation']['turns'][-1]['turn']['plan_sha256'] == facts['selected_plan_sha256']
    assert facts['selected_plan_sha256'] == '359e8fd8273c1b493b84d0ad424d4c235607c86962db69a95a7218055d2ef0ee'
    assert all(card['evidence']['reused_recorded'] and not card['evidence']['freshly_rechecked']
               for card in facts['alternatives'])
