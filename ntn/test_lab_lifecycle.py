"""Safety regressions for repeatable 5G lab startup."""

import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("prepare_lab", Path(__file__).parent / "single-pc/prepare_lab.py")
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


def container(name="mongo", project="starfabric-5g", status="exited"):
    return {"Id": "a" * 64, "Name": "/" + name,
            "Config": {"Labels": {"com.docker.compose.project": project}},
            "State": {"Running": status == "running", "Status": status}}


class LifecycleTests(unittest.TestCase):
    def test_foreign_collision_prevents_all_removals(self):
        results = [subprocess.CompletedProcess([], 0, json.dumps([container()]), ""),
                   subprocess.CompletedProcess([], 0, json.dumps([container("nrf", "another-lab")]), "")]
        with patch.object(prepare.subprocess, "run", side_effect=results) as run:
            with self.assertRaises(ValueError):
                prepare.main(["mongo", "nrf"])
            self.assertTrue(all(call.args[0][1] == "inspect" for call in run.call_args_list))

    def test_running_owned_lab_is_not_removed(self):
        result = subprocess.CompletedProcess([], 0, json.dumps([container(status="running")]), "")
        with patch.object(prepare.subprocess, "run", return_value=result) as run:
            with self.assertRaises(ValueError):
                prepare.main(["mongo"])
            self.assertEqual(run.call_count, 1)

    def test_stopped_owned_container_removed_by_id_without_volumes_or_force(self):
        result = subprocess.CompletedProcess([], 0, json.dumps([container()]), "")
        with patch.object(prepare.subprocess, "run", return_value=result) as run:
            prepare.main(["mongo"])
            self.assertEqual(run.call_args_list[-1].args[0], ["docker", "rm", "a" * 64])

    def test_daemon_error_is_not_treated_as_missing_container(self):
        result = subprocess.CompletedProcess([], 1, "", "permission denied")
        with patch.object(prepare.subprocess, "run", return_value=result):
            with self.assertRaises(RuntimeError):
                prepare.main(["mongo"])

    def test_missing_container_needs_no_removal(self):
        for message in ("Error: No such object: mongo", "error: no such object: mongo"):
            with self.subTest(message=message):
                result = subprocess.CompletedProcess([], 1, "", message)
                with patch.object(prepare.subprocess, "run", return_value=result) as run:
                    prepare.main(["mongo"])
                    self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
