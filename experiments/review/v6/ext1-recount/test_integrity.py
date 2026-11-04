"""Synthetic receipt attacks; no experimental map payload is opened."""
import json
from pathlib import Path
import tempfile
import unittest

from integrity import audit_evidence, file_sha256, object_sha256, _prior_matches


class AuditReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ext1-integrity-", dir=Path(__file__).resolve().parent)
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name)
        self.job = self.run / "jobs" / "fixture"
        self.job.mkdir(parents=True)
        for name in ["authorizations", "consumed"]:
            (self.run / name).mkdir()
        self.row = {"map_id": "ext-1:306320000", "arm": "pipeline", "passed": True,
                    "full_checker_pass": True, "complete": True, "all_loaded": True,
                    "replay_legal": True, "level": 0, "plan_sha256": "plan", "map_sha256": "map",
                    "audit_verified": True, "audit_fresh_process": True, "audit_pid": 202,
                    "planning_pid": 101}
        self.write("row.json", {"planning": "candidate"})
        digest = file_sha256(self.job / "row.json")
        self.row["audited_target_sha256"] = digest
        self.write("audit-row.json", self.row)
        job = {"map_id": self.row["map_id"], "arm": "pipeline"}
        self.write("job.json", job)
        self.write("DISPATCHED.json", {"job_sha256": object_sha256(job), "t": 5})
        self.write("pid.json", {"pid": 101, "started": 10})
        self.write("audit.pid.json", {"pid": 202, "start_new_session": True, "started": 30})
        self.write("process.json", {"child_pid": 101})
        self.write("audit.process.json", {"audit_pid": 202, "audit_returncode": 0,
                   "audit_timed_out": False, "audit_cleanup_failed": False,
                   "audit_watchdog_fired": False, "audit_cleanup_errors": [], "audit_watchdog_errors": []})
        self.write("AUDIT-DISPATCHED.json", {"t": 29, "target_sha256": digest})
        auth = {"authorization_id": "a" * 64, "phase": "audit", "arm": "pipeline", "map_id": self.row["map_id"]}
        audit_job = dict(job, target=str(self.job / "row.json"), target_sha256=digest)
        receipt = dict(auth, payload_sha256=object_sha256(audit_job))
        self.write("audit-job.json", dict(audit_job, authorization=auth))
        for name in ["authorizations", "consumed"]:
            (self.run / name / ("a" * 64 + ".json")).write_text(json.dumps(receipt))

    def write(self, name, obj):
        (self.job / name).write_text(json.dumps(obj))

    def verify(self):
        return audit_evidence(self.job, self.row, self.run)

    def test_valid_fresh_receipts(self):
        self.assertTrue(self.verify()["ok"], self.verify())

    def counterfactual_fixture(self, rule, rule_hash=None):
        self.row.update(counterfactual_rule=rule,
                        counterfactual_rule_sha256=rule_hash or object_sha256(rule),
                        counterfactual_instruction_recheck=False)
        self.write("audit-row.json", self.row)

    def test_counterfactual_matches_committed_allocation(self):
        expected = {"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 1}}
        self.counterfactual_fixture(expected)
        result = audit_evidence(self.job, self.row, self.run, expected_rule=expected)
        self.assertTrue(result["ok"], result)

    def test_consistently_substituted_counterfactual_rejected(self):
        expected = {"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 1}}
        substituted = {"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 3}}
        self.counterfactual_fixture(substituted)
        result = audit_evidence(self.job, self.row, self.run, expected_rule=expected)
        self.assertFalse(result["ok"])
        self.assertIn("audit counterfactual rule differs from committed allocation", result["errors"])

    def test_consistently_substituted_counterfactual_hash_rejected(self):
        expected = {"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 1}}
        self.counterfactual_fixture(expected, rule_hash="f" * 64)
        result = audit_evidence(self.job, self.row, self.run, expected_rule=expected)
        self.assertFalse(result["ok"])
        self.assertIn("audit counterfactual rule hash differs from committed allocation", result["errors"])

    def test_target_mutation_rejected(self):
        self.write("row.json", {"planning": "different candidate"})
        self.assertFalse(self.verify()["ok"])
        self.assertIn("audit job target hash mismatch", self.verify()["errors"])

    def test_same_process_rejected(self):
        self.write("audit.pid.json", {"pid": 101, "start_new_session": True, "started": 30})
        self.assertFalse(self.verify()["ok"])

    def test_unconsumed_audit_rejected(self):
        next((self.run / "consumed").iterdir()).unlink()
        self.assertFalse(self.verify()["ok"])

    def test_published_plan_mismatch_rejected(self):
        self.row["plan_sha256"] = "substituted"
        self.assertFalse(self.verify()["ok"])

    def test_audit_before_dispatch_rejected(self):
        self.write("audit.pid.json", {"pid": 202, "start_new_session": True, "started": 28})
        self.assertFalse(self.verify()["ok"])

    def test_payload_escape_rejected(self):
        path = self.job / "audit-job.json"
        job = json.loads(path.read_text())
        job["target"] = str(self.run / "outside.json")
        path.write_text(json.dumps(job))
        self.assertFalse(self.verify()["ok"])

    def deadline_fixture(self, wall=20):
        plan = {"actions": []}
        plan_digest = object_sha256(plan)
        event = {"kind": "incumbent", "t_s": wall,
                 "data": {"checked": True, "plan_sha256": plan_digest, "plan": plan}}
        publication = {"plan": plan, "published_wall_s": wall,
                       "publication_sha256": object_sha256(event), "validation_source": "runner_checked_incumbent"}
        target = {"deadline_with_incumbent": True, "hard_wall_s": 1200,
                  "deadline_publication": publication}
        self.write("deadline-row.json", target)
        digest = file_sha256(self.job / "deadline-row.json")
        self.row.update(plan_sha256=plan_digest, deadline_incumbent_verified=True, deadline_exceeded=True,
                        deadline_publication=publication, audited_target_sha256=digest)
        self.write("audit-row.json", self.row)
        self.write("observed-incumbents.json", {"child_pid": 101, "arm": "pipeline",
                   "map_id": self.row["map_id"], "hard_wall_s": 1200, "observed": {plan_digest: publication}})
        self.write("AUDIT-DISPATCHED.json", {"t": 29, "target_sha256": digest})
        (self.job / "pipeline").mkdir()
        (self.job / "pipeline/events.jsonl").write_text(json.dumps(event) + "\n")
        audit_job = json.loads((self.job / "audit-job.json").read_text())
        audit_job.update(target=str(self.job / "deadline-row.json"), target_sha256=digest, deadline_recovery=True)
        self.write("audit-job.json", audit_job)
        auth = audit_job.pop("authorization")
        receipt = dict(auth, payload_sha256=object_sha256(audit_job))
        for sub in ["authorizations", "consumed"]:
            (self.run / sub / ("a" * 64 + ".json")).write_text(json.dumps(receipt))

    def test_checked_predeadline_incumbent_validates(self):
        self.deadline_fixture()
        self.assertTrue(self.verify()["ok"], self.verify())

    def test_late_incumbent_rejected(self):
        self.deadline_fixture(wall=1201)
        self.assertFalse(self.verify()["ok"])

    def test_deadline_cannot_use_normal_row_target(self):
        self.row["deadline_exceeded"] = True
        self.write("audit-row.json", self.row)
        self.assertFalse(self.verify()["ok"])

    def test_deadline_status_cannot_bypass_recovery_check(self):
        self.row["status"] = "deadline_exceeded"
        self.row["deadline_incumbent_verified"] = True
        self.write("audit-row.json", self.row)
        self.assertFalse(self.verify()["ok"])

    def test_deadline_budget_cannot_be_extended(self):
        self.deadline_fixture()
        observations = json.loads((self.job / "observed-incumbents.json").read_text())
        observations["hard_wall_s"] = 1300
        self.write("observed-incumbents.json", observations)
        self.assertFalse(self.verify()["ok"])

    def test_unpublished_deadline_incumbent_rejected(self):
        self.deadline_fixture()
        (self.job / "pipeline/events.jsonl").write_text("")
        self.assertFalse(self.verify()["ok"])

    def test_prior_collision_streaming_and_boundaries(self):
        path = self.run / "prior.json"
        path.write_bytes(b" " * (1024 * 1024 - 4) + b'"306320000" ' + b'"' + b"f" * 64 + b'" 13063200001')
        self.assertEqual(_prior_matches(path, [306320000], ["f" * 64]), ["306320000", "f" * 64])
        self.assertEqual(_prior_matches(path, [306320001], ["e" * 64]), [])


if __name__ == "__main__":
    unittest.main()
