import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lab.live.performance import cpu_snapshot, source_manifest, workloads_ready


class PerformanceEvidenceTests(unittest.TestCase):
    def test_pending_service_state_cannot_certify_forwarding_completion(self):
        self.assertTrue(workloads_ready(None))
        workload = SimpleNamespace(flows=[{}], states={'forward':{'status':'ready'},'reverse':{'status':'pending'}},
                                   n3_evpn=True, n3_states={}, f=SimpleNamespace(service=lambda:[1,2]))
        self.assertFalse(workloads_ready(workload))
        workload.states['reverse']['status'] = 'ready'
        self.assertFalse(workloads_ready(workload))
        workload.n3_states = {'up':{'status':'ready'},'down':{'status':'ready'}}
        self.assertTrue(workloads_ready(workload))
        workload.n3_states['down']['status'] = 'pending'
        self.assertFalse(workloads_ready(workload))

    def test_cpu_evidence_includes_parent_quota_and_missing_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc, cgroup = root/'proc', root/'cgroup'
            (proc/'123').mkdir(parents=True)
            (proc/'123/status').write_text('Name:\tdriver\nCpus_allowed_list:\t2-7\n')
            (proc/'123/cgroup').write_text('0::/kind/pod\n')
            (cgroup/'kind/pod').mkdir(parents=True)
            (cgroup/'kind/pod/cpu.max').write_text('100000 100000\n')
            (cgroup/'kind/cpu.max').write_text('200000 100000\n')
            (cgroup/'kind/cpu.stat').write_text('usage_usec 123\nnr_throttled 4\n')
            sample = cpu_snapshot({'driver':123,'gone':456}, proc, cgroup)
            self.assertEqual(sample['processes']['driver']['cpus_allowed_list'], '2-7')
            self.assertEqual(sample['cgroups'][str(cgroup/'kind')]['cpu.max'], '200000 100000')
            self.assertEqual(sample['cgroups'][str(cgroup/'kind/pod')]['cpu.max'], '100000 100000')
            self.assertIn('unavailable', sample['processes']['gone'])
            self.assertNotIn('cpu.stat', sample['cgroups'][str(cgroup/'kind/pod')])

    def test_manifest_covers_controller_executor_and_build_inputs(self):
        files = ['internal/app/app.go', 'gen/schema.proto', 'lab/platform/namespace_jobs.py',
                 'onboard/Cargo.lock', 'ntn/single-pc/ueransim-gnb.Dockerfile',
                 'deploy/helm/starfabric/values.yaml', 'README.md']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in files:
                path = root/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name)
            outputs = ['\0'.join(files)+'\0', ' M internal/app/app.go\n', 'commit\n']
            with patch('lab.live.performance.subprocess.check_output', side_effect=outputs):
                manifest = source_manifest(root)
            self.assertTrue(manifest['dirty'])
            self.assertEqual(set(manifest['source_sha256']), set(files)-{'README.md'})
            # Only evidence, never source contents or shell output, is stored.
            self.assertEqual(len(manifest['source_sha256']['onboard/Cargo.lock']), 64)
            json.dumps(manifest)


if __name__ == '__main__':
    unittest.main()
