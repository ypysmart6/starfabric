"""Kernel observations must back topology freshness after a held snapshot."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'unified'))
from physics_runtime import PhysicalReplay


class PhysicsObservationTests(unittest.TestCase):
    def setup_replay(self, directory):
        link = {'id':'ab','source':'a','target':'b','latency_us':1000,'capacity_bps':1000000,
                'loss_ppm':0,'operational_up':True}
        f = SimpleNamespace(nodes=['a'], adjacency={'a':['b']}, directed={('a','b'):link},
            interface=lambda a,b:'sf1', scenario={'topology':{'links':[link]}},
            physical_model={'frames':[{'at':'2026-09-15T00:05:00Z'}]})
        r = SimpleNamespace(fabric=f, identifier='test', art=Path(directory),
            request=Mock(return_value={'topology':{'version':42}}), event=Mock())
        replay = PhysicalReplay(r)
        record = {'a':[{'interface':'sf1','rp_filter':0,'link':{'flags':['UP','LOWER_UP']},'qdisc':[{'kind':'netem',
            'options':{'delay':{'delay':0.001},'rate':{'rate':125000}}}]}]}
        return replay, r, record

    def test_interface_and_queue_drift_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            replay,r,records=self.setup_replay(directory)
            replay.verify_readback(records)
            for field,value in [('flags',[]),('flags',['UP']),('delay',0.02),('rate',250000),('loss',0.1),('rp_filter',2)]:
                with self.subTest(field=field,value=value):
                    changed=copy.deepcopy(records);item=changed['a'][0]
                    if field=='flags':item['link']['flags']=value
                    elif field=='rp_filter':item['rp_filter']=value
                    elif field=='loss':item['qdisc'][0]['options']['loss-random']={'loss':value}
                    else:item['qdisc'][0]['options'][field][field]=value
                    with self.assertRaises(AssertionError):replay.verify_readback(changed)

    def test_failed_observation_cannot_refresh_stale_telemetry(self):
        with tempfile.TemporaryDirectory() as directory:
            replay,r,records=self.setup_replay(directory)
            replay.last_sequence=100030
            replay.channels=Mock(side_effect=AssertionError('actual interface is down'))
            with self.assertRaises(AssertionError):replay.refresh_held_snapshot()
            r.request.assert_not_called()
            self.assertEqual(replay.last_sequence,100030)
            self.assertFalse((r.art/'physical-held-snapshot.json').exists())

    def test_verified_held_snapshot_has_fresh_sequence_and_preserves_model_time(self):
        with tempfile.TemporaryDirectory() as directory:
            replay,r,records=self.setup_replay(directory)
            replay.last_sequence=100030
            def observe(*args,**kwargs):
                r.request.assert_not_called()
                self.assertTrue(kwargs['readback'])
                replay.verify_readback(records)
            replay.channels=Mock(side_effect=observe)
            replay.refresh_held_snapshot()
            events=r.request.call_args.args[1]['events']
            self.assertEqual(events[0]['sequence'],100031)
            self.assertEqual(events[0]['link'],r.fabric.scenario['topology']['links'][0])
            report=json.loads((r.art/'physical-held-snapshot.json').read_text())
            self.assertEqual(report['model_at'],'2026-09-15T00:05:00Z')
            self.assertNotEqual(report['observed_at'],report['model_at'])
            self.assertEqual(report['directed_interfaces_verified'],1)


if __name__ == '__main__':unittest.main()
