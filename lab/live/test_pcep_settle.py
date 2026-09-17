import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from lab.live.workloads import Workloads


class PCEPSettlingTests(unittest.TestCase):
    def make(self):
        workload = Workloads.__new__(Workloads)
        workload.flows = [{'id':'pcep-01', 'carrier':'pcep', 'source':'a', 'destination':'b',
            'source_address':'198.18.0.2', 'destination_address':'198.18.0.3',
            'binding_sid':30001, 'table':10001}]
        paths = {'pcep-01-forward':['a','s1','s2','b'], 'pcep-01-reverse':['b','s2','s1','a']}
        workload.states = {key:{'status':'pending', 'nodes':nodes,
            'error':'waiting for PCRep and installed binding SID matching the path'}
            for key,nodes in paths.items()}
        workload.pce = SimpleNamespace(responses=[{'intent':'work-'+key, 'nodes':nodes}
                                                  for key,nodes in paths.items()])
        workload.f = SimpleNamespace(sid=lambda n:{'a':16001,'b':16002,'s1':16003,'s2':16004}[n],
                                     interface=lambda *args:'sf0', hop=lambda *args:'10.0.0.1')
        def lfib(node, *args):
            return json.dumps({'30001':{'installed':True, 'nexthops':[{
                'installed':True, 'type':'SR-TE', 'nexthop':'10.0.0.1', 'interface':'sf0',
                'outLabelStack':[16004,16002] if node == 'a' else [16003,16001]}]}})
        workload.r = SimpleNamespace(vty=Mock(side_effect=lfib), pids={'a':1,'b':2})
        workload.stopped = threading.Event()
        workload.jobs = Mock()
        workload.installed = {}
        return workload

    def test_matching_reply_and_lfib_restore_both_directions_in_same_frame(self):
        workload = self.make()
        workload.settle_pcep(timeout=1)
        self.assertTrue(all(s['status'] == 'ready' and 'error' not in s
                            for s in workload.states.values()))
        self.assertEqual(len(workload.installed), 2)
        self.assertEqual(workload.r.vty.call_count, 2)
        self.assertTrue(all('mpls' in args for args in workload.installed.values()))

    def test_old_stack_next_hop_interface_or_missing_reply_cannot_restore_forwarding(self):
        for mode in ('old_stack', 'old_next_hop', 'old_interface', 'no_reply'):
            with self.subTest(mode=mode):
                workload = self.make()
                if mode == 'old_stack':
                    original = workload.r.vty.side_effect
                    def old_stack(*args):
                        value = json.loads(original(*args))
                        value['30001']['nexthops'][0]['outLabelStack'] = [999]
                        return json.dumps(value)
                    workload.r.vty.side_effect = old_stack
                elif mode == 'old_next_hop':
                    workload.f.hop = lambda *args:'10.0.0.2'
                elif mode == 'old_interface':
                    workload.f.interface = lambda *args:'sf1'
                else:
                    workload.pce.responses = []
                workload.settle_pcep(timeout=.1)
                self.assertTrue(all(s['status'] == 'pending' for s in workload.states.values()))
                workload.jobs.assert_not_called()

    def test_failed_steering_is_not_published_ready(self):
        workload = self.make()
        workload.jobs.side_effect = RuntimeError('netlink failed')
        with self.assertRaises(RuntimeError):
            workload.settle_pcep(timeout=1)
        self.assertTrue(all(s['status'] == 'error' for s in workload.states.values()))
        self.assertEqual(workload.installed, {})

    def test_stop_skips_router_queries(self):
        workload = self.make()
        workload.stopped.set()
        workload.settle_pcep(timeout=1)
        workload.r.vty.assert_not_called()


if __name__ == '__main__':
    unittest.main()
