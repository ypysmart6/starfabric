import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lab.live import check_evpn_system as system


class SystemAcceptanceTests(unittest.TestCase):
    def write(self, root, relative, value):
        path=root/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value))

    def test_runtime_stopped_before_first_observation_writes_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.write(root,'reports/live/status.json',{'run_id':'owned','phase':'failed'})
            with patch.object(system,'ROOT',root), patch.object(system,'pdu',return_value={}):
                result=system.monitor('owned',root/'output')
            self.assertEqual(result['result'],'failed')
            self.assertIn('runtime stopped before acceptance',result['failures'])
            self.assertEqual(json.loads((root/'output/result.json').read_text())['result'],'failed')

    def test_missing_l2_gateway_cannot_reduce_acceptance_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.write(root,'reports/live/status.json',{'run_id':'owned','phase':'running'})
            prefix='lab/unified/artifacts/owned/workloads/'
            self.write(root,prefix+'catalog.json',{'run_id':'owned','flows':[
                {'source':'gw-001','destination':'gw-002'}]})
            self.write(root,prefix+'evpn-l2-catalog.json',{'run_id':'owned','gateways':{'gw-001':[]}})
            with patch.object(system,'ROOT',root), patch.object(system,'pdu',return_value={}):
                result=system.monitor('owned',root/'output')
            self.assertEqual(result['result'],'failed')
            self.assertIn('L2 catalog does not cover all workload gateways',result['failures'])

    def test_cli_propagates_failed_measurement(self):
        with patch.object(system,'monitor',return_value={'result':'failed'}):
            self.assertEqual(system.main(['--run-id','owned','--output','unused']),1)


if __name__=='__main__':
    unittest.main()
