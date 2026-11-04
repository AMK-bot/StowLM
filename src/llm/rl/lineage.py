"""Representation identity for newly encoded online rollouts, never old adapters."""
from __future__ import annotations


def rollout_data_identity(*, rule_catalogue: str | None = None) -> dict:
    """Record the encoders actually used; online RL reads no SFT dataset artifact."""
    from eval.training_lineage import data_version_identity
    from llm.rl import RL_VERSION
    from llm.stowlm.format import FORMAT_VERSION
    from llm.stowlm.format_prompt import PROMPT_VERSION
    from stowage.versions import component_versions

    if rule_catalogue is None:
        rule_catalogue = component_versions().get("rule_catalogue")
    return {"versions": data_version_identity({
                "prompt": PROMPT_VERSION, "format": FORMAT_VERSION,
                "dataset": None, "rule_catalogue": rule_catalogue}),
            "dataset_source": "online_rollouts", "rollout_version": RL_VERSION,
            "historical_sft_versions": None}
