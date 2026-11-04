"""Offline captures retain the same result context as the HTTP demo."""

from app.inputs import LoadedInputs
from stowage.model import PlannerResult


def test_capture_preserves_result_context_and_cannot_overwrite_shipping(replay_case, monkeypatch):
    from app import capture

    instance, plan = replay_case
    result = PlannerResult(status="timeout", plan=plan, planner="imported.custom",
                           planner_version="2", later_plans=[plan])
    calls = []

    def replay(*args, **kwargs):
        calls.append(kwargs)
        return {"metadata": {"departure_approved": False, "notice": "fresh verdict"}}

    monkeypatch.setattr(capture, "build_replay", replay)
    inputs = LoadedInputs(instance, plan, {
        "planner_result": result.model_dump(mode="json"),
        "label": "Imported timeout", "departure_approved": True, "notice": "stale approval",
    })
    payload = capture.checked_replay(inputs)
    assert calls[0].get("planner_result") == result
    assert payload["metadata"]["label"] == "Imported timeout"
    assert payload["metadata"]["departure_approved"] is False
    assert payload["metadata"]["notice"] == "fresh verdict"
