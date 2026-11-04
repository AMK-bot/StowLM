import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from experiments.mvp.pipeline.binding.measurement_lifecycle import Attempt, preserve_unfinished, unknown_attempts


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(5), RuntimeError("unexpected")])
def test_graceful_baseexception_is_durable_unknown_before_rethrow(tmp_path, error):
    attempt = Attempt(tmp_path, "logical", {"sha256": "identity"}, {})
    attempt.incumbent({"lifts": 4, "wall_s": 2})
    with pytest.raises(type(error)):
        try:
            raise error
        except BaseException as caught:
            attempt.unknown(caught)
            raise
    unknown = json.loads((attempt.path / "unknown.json").read_text())
    events = [json.loads(line) for line in (attempt.path / "lifecycle.jsonl").read_text().splitlines()]
    assert unknown["verdict"] == "unknown" and unknown["wall_s"] >= 0
    assert [row["state"] for row in events] == ["started", "checked_incumbent", "unknown"]
    assert not (attempt.path / "final.json").exists()
    assert len(unknown_attempts(tmp_path)) == 1


def test_abrupt_exit_survives_resume_without_truncating_prior_evidence(tmp_path):
    code = """
import os,sys
from experiments.mvp.pipeline.binding.measurement_lifecycle import Attempt
a=Attempt(sys.argv[1], 'logical', {'sha256':'identity'}, {})
(a.path/'stack.log').write_text('original stack')
a.incumbent({'lifts':7,'wall_s':1})
os._exit(7)
"""
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    completed = subprocess.run([sys.executable, "-c", code, str(tmp_path)], env=environment,
                               check=False, timeout=30)
    assert completed.returncode == 7
    prior = next((tmp_path / "attempts").iterdir())
    before = {str(path.relative_to(prior)): path.read_bytes() for path in prior.rglob("*") if path.is_file()}
    recovery = preserve_unfinished(tmp_path, "logical")
    assert recovery and json.loads((Path(recovery) / "recovery.json").read_text())["verdict"] == "unknown"
    new = Attempt(tmp_path, "logical", {"sha256": "identity"}, {})
    assert new.path != prior and not (new.path / "incumbents").exists()
    assert before == {str(path.relative_to(prior)): path.read_bytes() for path in prior.rglob("*") if path.is_file()}


def test_legacy_unfinished_progress_and_incumbent_are_copied_not_overwritten(tmp_path):
    progress = tmp_path / "progress"; progress.mkdir()
    (progress / "old.json").write_text(json.dumps({"started_at": 1, "completed": False}))
    (progress / "old.stack.log").write_text("stack")
    (progress / "old.checked-incumbent.json").write_text('{"passed":true}')
    before = {path.name: path.read_bytes() for path in progress.iterdir()}
    recovered = Path(preserve_unfinished(tmp_path, "old", progress))
    assert before == {path.name: path.read_bytes() for path in progress.iterdir()}
    assert (recovered / "old.stack.log").read_text() == "stack"
    assert (recovered / "old.checked-incumbent.json").read_bytes() == before["old.checked-incumbent.json"]
    assert unknown_attempts(tmp_path)[0]["status"] == "unknown"


def test_repeated_legacy_resume_preserves_copies_but_reports_one_original_attempt(tmp_path):
    progress = tmp_path / "progress"; progress.mkdir()
    header = {"started_at": 1, "pid": 1234, "key": "old", "completed": False}
    (progress / "old.json").write_text(json.dumps(header))
    first = preserve_unfinished(tmp_path, "old", progress)
    # Reformatting does not turn the same started-at/PID/key into a new attempt.
    (progress / "old.json").write_text(json.dumps(header, indent=2))
    second = preserve_unfinished(tmp_path, "old", progress)
    assert first != second and Path(first).is_dir() and Path(second).is_dir()
    assert len(unknown_attempts(tmp_path)) == 1
