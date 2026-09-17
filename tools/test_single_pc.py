"""Regression tests for evidence that previously could produce a false pass."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.single_pc import execute_stage, local_path, sha256, source_digest
from tools.verify_single_pc_closure import truthy, validate_gate


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run_dir = self.root / "reports/runs/example"
        self.run_dir.mkdir(parents=True)

    def stage(self, code, **extra):
        return execute_stage({"id": "probe", "command": [sys.executable, "-c", code],
                              "reports": ["reports/probe.json"], "timeout_seconds": 5, **extra}, self.run_dir, self.root)

    def test_raw_packet_evidence_is_archived_and_tampering_rejected(self):
        outcome = self.stage("from pathlib import Path; import json; "
            "p=Path('raw/one'); p.mkdir(parents=True); (p/'capture.pcap').write_bytes(b'packet evidence'); "
            "Path('reports/probe.json').write_text(json.dumps({'success':True,'artifacts':'raw/one'}))",
            artifact_reports={"reports/probe.json": "raw"})
        self.assertEqual(outcome["status"], "passed", outcome)
        gate = {"report": "reports/probe.json"}
        self.assertEqual(validate_gate("test", gate, None, self.run_dir, {"stages": [outcome]}), [])
        evidence = outcome["artifacts"]["reports/probe.json"]["raw/one/capture.pcap"]
        (self.run_dir / evidence["path"]).write_bytes(b"changed packet evidence")
        self.assertIn("raw artifact hash mismatch", validate_gate("test", gate, None, self.run_dir, {"stages": [outcome]})[0])

    def test_new_report_cannot_attest_old_raw_capture(self):
        directory = self.root / "raw/one"
        directory.mkdir(parents=True)
        (directory / "capture.pcap").write_bytes(b"old packet evidence")
        outcome = self.stage("from pathlib import Path; import json; "
            "Path('reports/probe.json').write_text(json.dumps({'success':True,'artifacts':'raw/one'}))",
            artifact_reports={"reports/probe.json": "raw"})
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("artifact was not produced", outcome["errors"][0])

    def test_old_success_cannot_satisfy_zero_exit(self):
        (self.root / "reports/probe.json").write_text('{"success": true}')
        result = self.stage("pass")
        self.assertEqual(result["status"], "failed")
        self.assertIn("not produced", result["errors"][0])
        self.assertEqual(result["reports"], {})

    def test_current_report_snapshotted_and_detects_tampering(self):
        outcome = self.stage("from pathlib import Path; Path('reports/probe.json').write_text('{\"success\": true, \"checks\": {\"fib\": true}}')")
        self.assertEqual(outcome["status"], "passed", outcome)
        run = {"stages": [outcome]}
        gate = {"report": "reports/probe.json", "checks": ["fib"]}
        self.assertEqual(validate_gate("test", gate, None, self.run_dir, run), [])
        snapshot = self.run_dir / outcome["reports"][gate["report"]]["path"]
        snapshot.write_text('{"success": true, "checks": {"fib": false}}')
        self.assertIn("hash mismatch", validate_gate("test", gate, None, self.run_dir, run)[0])

    def test_failed_command_does_not_attest_success_file(self):
        outcome = self.stage("from pathlib import Path; Path('reports/probe.json').write_text('{\"success\": true}'); raise SystemExit(7)")
        self.assertEqual(outcome["status"], "failed")
        errors = validate_gate("test", {"report": "reports/probe.json"}, None, self.run_dir, {"stages": [outcome]})
        self.assertIn("no successful", errors[0])

    def test_absent_stage_cannot_use_workspace_report(self):
        (self.root / "reports/probe.json").write_text('{"success": true}')
        errors = validate_gate("test", {"report": "reports/probe.json"}, None, self.run_dir, {"stages": []})
        self.assertIn("no successful", errors[0])

    def test_gate_requires_typed_finite_positive_result(self):
        for value in ("false", "true", "0", [], [False], {"success": False}, -1, float("nan"), float("inf")):
            with self.subTest(value=value):
                self.assertFalse(truthy(value))
        for value in (True, 1, 0.1):
            self.assertTrue(truthy(value))

    def test_invalid_report_shape_is_a_failure(self):
        outcome = self.stage("from pathlib import Path; Path('reports/probe.json').write_text('[]')")
        self.assertEqual(outcome["status"], "failed")

    def test_artifact_cannot_escape_root(self):
        for relative in ("../escape.json", "/tmp/escape.json"):
            with self.assertRaises(ValueError):
                local_path(self.root, relative)

    def test_source_hash_ignores_outputs_and_detects_config_changes(self):
        config = self.root / "scenario.json"
        config.write_text('{"seed": 1}')
        before = source_digest(self.root)
        (self.root / "reports/probe.json").write_text('{"success": true}')
        self.assertEqual(before, source_digest(self.root))
        config.write_text('{"seed": 2}')
        self.assertNotEqual(before, source_digest(self.root))

    def test_check_string_false_and_future_timestamp_rejected(self):
        report = self.root / "reports/probe.json"
        report.write_text(json.dumps({"success": True, "checks": {"fib": "false"},
                                      "generated_at": "2999-01-01T00:00:00Z"}))
        with patch("tools.verify_single_pc_closure.ROOT", self.root):
            errors = validate_gate("test", {"report": "reports/probe.json", "checks": ["fib"]}, 24)
        self.assertEqual(len(errors), 2, errors)


if __name__ == "__main__":
    unittest.main()
