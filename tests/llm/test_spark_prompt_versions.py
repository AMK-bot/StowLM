"""Spark's prompt and identity must select the same observation version."""
import json

import pytest

from llm.spark.planner import REASONING_SETTING, SparkPlanner, _without_reasoning_line, build_prompt
from llm.spark.rules_appendix import HEADER
from llm.stowlm.format_prompt import instance_to_prompt, system_prompt
from stowage.model.fixtures import build_instance
from stowage.rules.catalogue import rule_freeze_v1


@pytest.mark.parametrize("enabled", [False, True])
def test_spark_header_legend_matches_body_and_identity(enabled):
    planner = SparkPlanner()
    with rule_freeze_v1(enabled=enabled):
        instance = build_instance()
        prompt = build_prompt(instance)
        version = json.loads(instance_to_prompt(instance))["v"]
        assert "/" + version + "/rules-appendix@" in planner.version
        assert planner.version.endswith("/reasoning=" + REASONING_SETTING)
        assert SparkPlanner.version == planner.version
        selected = _without_reasoning_line(system_prompt(instance))
        assert selected.split("Codes:", 1)[1] in prompt
        assert "Reasoning strength" not in prompt
        assert HEADER in prompt
        if enabled:
            assert selected in prompt
            assert "mass(A=2000kg" not in prompt
        else:
            assert "mass(A=2000kg" in prompt
        assert "No feasibility mask is available" in prompt
        # No request is attempted, but early results must report the selected run identity.
        result = planner.plan(instance, time_limit_s=0)
        assert result.planner_version == planner.version


def test_compact_only_ablation_is_labelled_and_has_no_appendix():
    planner = SparkPlanner(appendix=False)
    instance = build_instance()
    prompt = build_prompt(instance, appendix=False)
    assert HEADER not in prompt and "Reasoning strength" not in prompt
    assert build_prompt(instance).startswith(prompt.rstrip("\n"))
    assert "/compact-only/" in planner.version and planner.prompt_appendix is None
    assert SparkPlanner().prompt_appendix.startswith("rules-appendix@")
