"""CPU reservations are scoped to owned radios and reversible after partial setup."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'unified'))
from resources import HostBudget


class RadioCPUTests(unittest.TestCase):
    def test_cpu_reservation_and_partial_setup_restore_original_limits(self):
        for fail_second in [False,True]:
            with self.subTest(fail_second=fail_second), tempfile.TemporaryDirectory() as directory:
                state={name:{'Id':name+'-owned','Config':{'Labels':{'com.docker.compose.project':'starfabric-5g'}},
                       'HostConfig':{'CpusetCpus':'','CpuShares':0}} for name in ['nr_gnb','nr_ue']}
                if fail_second:state['nr_ue']['Config']['Labels']['com.docker.compose.project']='another-project'
                original=copy.deepcopy(state)
                def command(*args,**kwargs):
                    if args[:2]==('docker','inspect'):
                        return SimpleNamespace(stdout=json.dumps([state[args[2]]]),returncode=0)
                    if args[:2]==('docker','update'):
                        name=args[-1]
                        state[name]['HostConfig']['CpusetCpus']=args[args.index('--cpuset-cpus')+1]
                        state[name]['HostConfig']['CpuShares']=int(args[args.index('--cpu-shares')+1])
                    return SimpleNamespace(stdout='',returncode=0)
                with patch('resources.command',side_effect=command), patch('resources.os.sched_getaffinity',return_value={4,7,9}), \
                     patch('resources.os.sched_setaffinity') as affinity:
                    budget=HostBudget(Path(directory))
                    if fail_second:
                        with self.assertRaises(AssertionError):budget.prepare()
                    else:
                        budget.prepare()
                        self.assertEqual(budget.data_cpu_set,'7,9')
                        self.assertTrue(all(v['HostConfig']['CpusetCpus']=='4' for v in state.values()))
                        affinity.assert_called_with(0,[7,9])
                    budget.cleanup()
                    self.assertEqual(state,original)
                    affinity.assert_called_with(0,[4,7,9])
                    report=json.loads((Path(directory)/'radio-cpu-budget.json').read_text())
                    self.assertTrue(all(record['restored'] for record in report['radio_containers']))


if __name__=='__main__':unittest.main()
