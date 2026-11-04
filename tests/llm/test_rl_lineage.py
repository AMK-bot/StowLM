"""Current rollout representation evidence is separate from historical SFT data."""
from llm.rl import lineage
from llm.stowlm.format import FORMAT_VERSION
from llm.stowlm.format_prompt import PROMPT_VERSION


def test_rollout_lineage_records_actual_representation_and_admitted_catalogue():
    catalogue = 'sha256:' + 'a' * 64
    identity = lineage.rollout_data_identity(rule_catalogue=catalogue)
    assert identity['versions'] == {
        'prompt': [PROMPT_VERSION], 'format': [FORMAT_VERSION],
        'dataset': None, 'rule_catalogue': [catalogue],
    }
    assert identity['dataset_source'] == 'online_rollouts'
    assert identity['historical_sft_versions'] is None
