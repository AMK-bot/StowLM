"""Fake filesystem/model tests: never run a stowage solve or full checker."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
from unittest.mock import patch
import unittest

from experiments.mvp.strategy.blockrule import capacity_train as train


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


class TrainControllerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="blockrule-v2-test-")
        self.root = Path(self.temporary.name)
        self.here = self.root / "blockrule"
        self.here.mkdir()
        self.stack = [patch.object(train, "ROOT", self.root), patch.object(train, "HERE", self.here)]
        for replacement in self.stack:
            replacement.start()

    def tearDown(self):
        for replacement in reversed(self.stack):
            replacement.stop()
        self.temporary.cleanup()

    def test_ready_waits_for_supervisor_error_and_absent_controller_locks(self):
        for name in ("small-train", "medium-train"):
            write(self.here / "runs" / name / "completion.json", {"errors": []})
        write(self.here / "supervisor-status.json", {"phase": "medium_train"})
        self.assertFalse(train.ready())
        write(self.here / "supervisor-status.json", {"phase": "error"})
        self.assertTrue(train.ready())
        lock = self.here / "runs/small-train/controller.lock"
        write(lock, {"pid": 123})
        self.assertFalse(train.ready())
        lock.unlink()
        write(self.here / "validation-once.json", {})
        with self.assertRaisesRegex(RuntimeError, "after a freeze/validation claim"):
            train.ready()

    def test_physical_hashes_allow_only_exact_archived_study_metadata_drift(self):
        physical = self.root / "physical.py"
        physical.write_text("pinned")
        current = self.here / "study.py"
        current.write_text("def source_snapshot(): return 'new'\ndef compute(): return 1\n")
        archived = self.here / "versions/v1/study.py"
        archived.parent.mkdir(parents=True)
        archived.write_text("def source_snapshot(): return 'old'\ndef compute(): return 1\n")
        run = {"sources": {"physical.py": train.study.digest(physical),
                            "blockrule/study.py": train.study.digest(archived)}}
        train.physical_epoch(run)
        current.write_text("def source_snapshot(): return 'new'\ndef compute(): return 2\n")
        with self.assertRaisesRegex(RuntimeError, "changed beyond metadata"):
            train.physical_epoch(run)
        current.write_text("def source_snapshot(): return 'new'\ndef compute(): return 1\n")
        physical.write_text("changed physics")
        with self.assertRaisesRegex(RuntimeError, "Pinned physical source changed"):
            train.physical_epoch(run)

    def test_final_study_may_change_only_the_authorized_rule_import(self):
        current = self.here / "study.py"
        archived = self.here / "versions/v1/study.py"
        archived.parent.mkdir(parents=True)
        archived.write_text("sys.path[:0] = [str(HERE), str(HEADROOM)]\ndef run_map():\n from rule import port_blocking_layout\n return 1\n")
        current.write_text("sys.path[:0] = [str(HERE), str(ROOT), str(HEADROOM)]\ndef run_map():\n from capacity_candidate import capacity_relaxed_layout as port_blocking_layout\n return 1\n")
        run = {"sources": {"blockrule/study.py": train.study.digest(archived)}}
        train.physical_epoch(run)
        current.write_text("sys.path[:0] = [str(HERE), str(ROOT), str(HEADROOM)]\ndef run_map():\n from capacity_candidate import capacity_relaxed_layout as port_blocking_layout\n return 2\n")
        with self.assertRaisesRegex(RuntimeError, "authorized policy import"):
            train.physical_epoch(run)

    def test_actual_staged_v2_normalizes_to_exact_archived_v1(self):
        real = Path(__file__).resolve().parent
        archived = self.here / "versions/v1/study.py"
        archived.parent.mkdir(parents=True)
        archived.write_bytes((real / "versions/v1/study.py").read_bytes())
        (self.here / "study.py").write_bytes((real / "versions/v2/study.py").read_bytes())
        run = {"sources": {"blockrule/study.py": train.study.digest(archived)}}
        train.physical_epoch(run)

    def test_clone_and_alias_commit_preserve_initial_metadata_and_certificate_bytes(self):
        original = self.here / "runs/small-train"
        sources = {"physical.py": "hash"}
        run = dict(sources=sources, config={"small_budget": 120}, jobs=[])
        write(original / "run.json", run)
        write(original / "completion.json", {"maps": 0, "errors": []})
        certificate = original / "maps/m1/finished/default-raw.json"
        certificate.parent.mkdir(parents=True)
        certificate.write_bytes(b'{"physical-plan":"unchanged"}\n')
        before = certificate.read_bytes()
        run_before = (original / "run.json").read_bytes()
        policy = self.here / "capacity-policy.json"
        write(policy, {"candidate": "frozen"})
        with patch.object(train, "physical_epoch"):
            source, final, initial, origin = train.clone_initial("small-train", policy, {"new.py": "new"})
        self.assertEqual((final / "initial-run.json").read_bytes(), run_before)
        self.assertEqual((final / "maps/m1/finished/default-raw.json").read_bytes(), before)
        self.assertEqual((source / "run.json").read_bytes(), run_before)
        campaigns = {"small-train": dict(final=final, origin=origin)}
        train.publish(campaigns)
        self.assertTrue(original.is_symlink())
        self.assertEqual(original.resolve(), final.resolve())
        archive = self.here / "runs/initial-small-train"
        self.assertEqual((archive / "run.json").read_bytes(), run_before)
        self.assertEqual((original / "maps/m1/finished/default-raw.json").read_bytes(), before)
        train.publish(campaigns)  # Same alias commit is idempotent.

    def test_rule_cache_preserves_v1_and_resumes_distinct_v2(self):
        folder = self.here / "runs/final-medium-train/maps/m1"
        for category in ("evals", "plans", "checks", "reports", "finished"):
            write(folder / category / "rule-raw.json", {"version": 1})
            write(folder / category / "default-raw.json", {"version": 1})
        train.prepare_rule_cache(folder, "candidate", {"source": "hash"})
        self.assertTrue((folder / "checks/rule-raw.json").exists())
        self.assertEqual(json.loads((folder / "finished/rule-raw.json").read_text()), {"version": 1})
        self.assertTrue((folder / "finished/default-raw.json").exists())
        write(folder / "finished/rule-v2-raw.json", {"version": 2})
        train.prepare_rule_cache(folder, "candidate", {"source": "hash"})
        self.assertEqual(json.loads((folder / "finished/rule-v2-raw.json").read_text()), {"version": 2})
        with self.assertRaisesRegex(RuntimeError, "wrong mask/epoch"):
            train.prepare_rule_cache(folder, "changed", {"source": "hash"})

    def test_artifact_rebasing_is_lexical_and_rejects_outside_paths(self):
        artifact = self.here / "runs/small-train/maps/m1/finished/default-raw.json"
        archive = self.here / "runs/initial-small-train"
        self.assertEqual(train.artifact_in(artifact, "small-train", archive),
                         archive / "maps/m1/finished/default-raw.json")
        with self.assertRaisesRegex(RuntimeError, "outside initial campaign"):
            train.artifact_in(self.root / "outside.json", "small-train", archive)

    def test_inherited_certificate_audits_layout_without_rechecking(self):
        import layout
        from stowage.model import PlannerResult
        original = self.here / "runs/small-train"
        final = self.here / "runs/final-small-train"
        check_relative = Path("maps/m1/checks/rule-raw.json")
        report_relative = Path("maps/m1/reports/rule-raw.json")
        finished_relative = Path("maps/m1/finished/rule-raw.json")
        record = dict(passed=True, checker_attempted=True, checker_passed=True, moves=8,
                      report_path=str((original / report_relative).relative_to(self.root)),
                      finished_path=str((original / finished_relative).relative_to(self.root)))
        write(original / check_relative, record)
        for directory in (original, final):
            write(directory / report_relative, {"passed": True, "count": {"weighted_nonproductive": 8}})
            write(directory / finished_relative, {"dummy": "same plan"})
        origin = dict(initial_run_sha256="runhash", initial_sources_sha256="sourceshash")
        restriction = NS(fingerprint="mask")
        with patch.object(PlannerResult, "model_validate", return_value=NS(status="ok", plan=NS())), \
             patch.object(layout, "audit_layout", return_value=[]) as auditor, \
             patch("stowage.checker.check", side_effect=AssertionError("No full checker may run")) as checker:
            result = train.inherited_record(record, "rule", "raw", "m1", "small-train", original,
                                             final, origin, NS(), NS(), restriction)
        auditor.assert_called_once()
        checker.assert_not_called()
        self.assertTrue(result["inherited_certificate"])
        self.assertFalse(result["inheritance"]["checker_called_this_campaign"])
        self.assertEqual(result["inheritance"]["final_layout_fingerprint"], "mask")
        self.assertEqual(result["inheritance"]["original_check_path"],
                         "blockrule/runs/initial-small-train/maps/m1/checks/rule-raw.json")
        self.assertEqual(result["inheritance"]["original_check_sha256"], train.study.digest(original / check_relative))

    def test_unchecked_inherited_outcome_is_not_called_certificate(self):
        original = self.here / "runs/small-train"
        final = self.here / "runs/final-small-train"
        record = dict(passed=False, checker_attempted=False, finalize_status="not_reached")
        write(original / "maps/m1/checks/rule-raw.json", record)
        origin = dict(initial_run_sha256="runhash", initial_sources_sha256="sourceshash")
        result = train.inherited_record(record, "rule", "raw", "m1", "small-train", original,
                                         final, origin, NS(), NS(), None)
        self.assertTrue(result["inherited_outcome"])
        self.assertFalse(result["inherited_certificate"])
        self.assertIn("checker not reached", result["inheritance"]["kind"])


class IsolatedBootstrapTests(unittest.TestCase):
    def test_actual_staged_bootstrap_imports_candidate_from_unrelated_cwd(self):
        script = Path(train.study.__file__).resolve()
        staged = Path(__file__).resolve().parent / "versions/v2/study.py"
        code = """
from pathlib import Path
import sys
script = Path(sys.argv[1]).resolve()
source = Path(sys.argv[2]).read_text()
namespace = {'__file__': str(script), '__name__': 'isolated_study_import'}
exec(compile(source, str(script), 'exec'), namespace)
exec('from capacity_candidate import capacity_relaxed_layout as port_blocking_layout', namespace)
assert callable(namespace['port_blocking_layout'])
"""
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        with tempfile.TemporaryDirectory(prefix="blockrule-bootstrap-") as folder:
            result = subprocess.run([sys.executable, "-c", code, str(script), str(staged)], cwd=folder,
                                    env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
