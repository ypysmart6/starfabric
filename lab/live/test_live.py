import copy
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from lab.live.model import OrbitClock, LiveFabric, pair_index
from lab.live.storage import PingCounters
from lab.live.service import Service
from lab.live import heartbeat
from tools.physical_constellation import compile_scenario, load_config

ROOT = Path(__file__).resolve().parents[2]


class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Build from tracked inputs so this suite also works without archived
        # reports from another developer's machine.
        with tempfile.TemporaryDirectory() as folder:
            seed_path=Path(folder)/'seed.json'
            subprocess.run(['go','run','./cmd/sfctl','constellation','generate','--config',
                str(ROOT/'scenarios/constellations/leo-120.config.json'),'--output',str(seed_path)],
                cwd=ROOT,check=True,capture_output=True,text=True,timeout=90)
            seed=json.loads(seed_path.read_text())
        nodes=seed['topology']['nodes']
        cls.config=load_config(ROOT/'scenarios/constellations/physical-defaults.json',
            sorted(n['id'] for n in nodes if n['kind']=='satellite'),
            sorted(n['id'] for n in nodes if n['kind']=='gateway'))
        cls.source=compile_scenario(seed,cls.config)

    def test_propagates_beyond_archived_duration_without_wrapping(self):
        clock = OrbitClock(self.source['topology']['nodes'], self.config)
        start = datetime.fromisoformat(self.config['epoch'].replace('Z','+00:00'))
        a = clock.sample(start)
        b = clock.sample(start+timedelta(seconds=310))
        c = clock.sample(start+timedelta(hours=3))
        self.assertEqual(len(c['states']),120)
        self.assertEqual(c['offset_seconds'],10800)
        self.assertNotEqual(a['states']['sat-0001'],b['states']['sat-0001'])
        self.assertNotEqual(b['states']['sat-0001'],c['states']['sat-0001'])
        with self.assertRaises(ValueError):
            clock.sample(start)

    def test_pair_addresses_are_unique_and_order_independent(self):
        ids = [f'n{i:03d}' for i in range(124)]
        values = [pair_index(a,b,ids) for i,a in enumerate(ids) for b in ids[i+1:]]
        self.assertEqual(len(values),len(set(values)))
        self.assertEqual(values,list(range(1,len(values)+1)))
        self.assertEqual(pair_index(ids[0],ids[-1],ids),pair_index(ids[-1],ids[0],ids))

    def test_skipped_application_intervals_preserve_contact_acquisition_history(self):
        start=datetime.fromisoformat(self.config['epoch'].replace('Z','+00:00'))
        delayed=OrbitClock(self.source['topology']['nodes'],self.config)
        regular=OrbitClock(self.source['topology']['nodes'],self.config)
        delayed.sample(start)
        result=delayed.sample(start+timedelta(seconds=120))
        for offset in range(0,121,self.config['step_seconds']):
            expected=regular.sample(start+timedelta(seconds=offset))
        self.assertEqual(result['links'],expected['links'])
        self.assertEqual(result['at'],expected['at'])
        self.assertEqual(delayed.contacts.selected,regular.contacts.selected)

    def test_live_bgpls_uses_collector_while_evpn_keeps_all_gateway_peers(self):
        fabric = LiveFabric(copy.deepcopy(self.source), 'sf-test')
        for node in fabric.gateways:
            config = '\n'.join(fabric.bgp_config(node))
            ls = config.split('address-family link-state link-state\n')[1].split('exit-address-family')[0]
            evpn = config.split('address-family l2vpn evpn\n')[1].split('exit-address-family')[0]
            self.assertEqual(ls.count(' activate'), int(node in fabric.pair))
            self.assertEqual(evpn.count(' activate'), len(fabric.gateways)-1)
            self.assertEqual(config.count('timers connect 5'), len(fabric.gateways)-1)

    def test_new_contacts_are_wired_down_and_reuse_addresses_after_retirement(self):
        source = copy.deepcopy(self.source)
        source['topology']['links'] = source['physical_model']['frames'][0]['links']
        f = LiveFabric(source,'sf-test')
        a, b = next((a,b) for a in f.satellites for b in f.satellites if a<b and (a,b) not in f.links)
        base = source['topology']['links'][0]
        frame = {'links':[dict(base,id=x+'--'+y,source=x,target=y,operational_up=True) for x,y in ((a,b),(b,a))]}
        added = f.register(frame,0)
        self.assertEqual(added,{(a,b)})
        old = copy.deepcopy(f.links[a,b])
        self.assertFalse(f.active(a,b))
        jobs = f.wiring({node:i+1000 for i,node in enumerate(f.nodes)},added)
        self.assertFalse(any('up' in j['args'] for j in jobs))
        self.assertFalse(any('sr0' in j['args'] for j in jobs))
        self.assertEqual(f.nodes[a]['labels']['next_hop_scheme'],'live-pair-v1')
        self.assertEqual(int(f.nodes[a]['labels']['next_hop_index']),f.ids.index(a))
        f.forget([(a,b)])
        f.register(frame,150)
        self.assertEqual(f.links[a,b],old)

    def test_expired_links_are_disabled_and_retired(self):
        source = copy.deepcopy(self.source)
        f = LiveFabric(source,'sf-test')
        f.last_present={pair:0 for pair in f.links}
        desired=f.desired({'links':[]})
        self.assertTrue(all(not link['operational_up'] for link in desired))
        f.set_links(desired)
        self.assertEqual(set(f.expired(121)),set(f.links))

    def test_fabric_configmap_remains_below_kubernetes_limit(self):
        f = LiveFabric(self.source,'sf-test')
        self.assertLess(len(json.dumps(f.scenario).encode()),950000)

    def test_global_catalog_fits_configmap_and_keeps_all_geographic_gateways(self):
        from lab.live.workload_model import catalog, controller_intents
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'seed.json'
            subprocess.run(['go','run','./cmd/sfctl','constellation','generate','--config',
                str(ROOT/'scenarios/constellations/leo-120-global.config.json'),'--output',str(path)],
                cwd=ROOT,check=True,capture_output=True,text=True,timeout=90)
            seed = json.loads(path.read_text())
            nodes = seed['topology']['nodes']
            config = load_config(ROOT/'scenarios/constellations/physical-defaults.json',
                [n['id'] for n in nodes if n['kind']=='satellite'],
                [n['id'] for n in nodes if n['kind']=='gateway'])
            self.assertEqual(len(config['ground_stations']),24)
            self.assertEqual(len({s['region'] for s in config['ground_stations']}),6)
            source = compile_scenario(seed,config)
            source['topology']['links'] = source['physical_model']['frames'][0]['links']
            fabric = LiveFabric(source,'sf-global-test')
            flows = catalog(json.loads((ROOT/'scenarios/constellations/live-workloads.json').read_text()),fabric.gateways)
            fabric.scenario['intents'].extend(controller_intents(flows))
            encoded = json.dumps(fabric.scenario,indent=2)
            self.assertLess(len(encoded.encode()),950000)
            path.write_text(encoded)
            result = subprocess.run(['go','run','./cmd/sfctl','scenario','validate','--check-paths','--file',str(path)],
                cwd=ROOT,capture_output=True,text=True,timeout=90)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_ping_counters_track_loss_and_sequence_wrap(self):
        counter=PingCounters()
        counter.consume('[1.0] 64 bytes: icmp_seq=1 time=20.1 ms')
        counter.consume('no answer yet for icmp_seq=2')
        counter.consume('[3.0] 64 bytes: icmp_seq=3 time=25.0 ms')
        stats,samples=counter.snapshot()
        self.assertEqual((stats['transmitted'],stats['received']),(3,2))
        self.assertAlmostEqual(stats['loss_percent'],100/3)
        counter.highest=65535
        counter.consume('[4.0] 64 bytes: icmp_seq=0 time=10.0 ms')
        self.assertEqual(counter.snapshot()[0]['transmitted'],65536)

    def test_dead_service_is_explicitly_interrupted(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            directory=root/'reports/live'
            directory.mkdir(parents=True)
            (directory/'owner.json').write_text(json.dumps({'pid':99999999,'identity':'1'}))
            (directory/'status.json').write_text(json.dumps({'phase':'running','run_id':'test'}))
            status=Service(root).status()
            self.assertFalse(status['active'])
            self.assertEqual(status['phase'],'interrupted')

    def test_one_heartbeat_timeout_does_not_skip_other_satellites(self):
        def send(args, **kwargs):
            if args[2] == '101':
                raise subprocess.TimeoutExpired(args, 4)
            return subprocess.CompletedProcess(args, 0, '', '')
        with tempfile.TemporaryDirectory() as folder:
            request=Path(folder)/'request.json'
            request.write_text(json.dumps({'generation':9,'nodes':{'sat-0001':101,'sat-0002':102}}))
            output=io.StringIO()
            with patch.object(heartbeat.sys,'argv',['heartbeat.py',str(request)]), \
                    patch.object(heartbeat.subprocess,'run',side_effect=send), contextlib.redirect_stdout(output):
                heartbeat.main()
            result=json.loads(output.getvalue())
            self.assertEqual(result['generation'],9)
            self.assertFalse(result['nodes']['sat-0001']['ok'])
            self.assertTrue(result['nodes']['sat-0002']['ok'])


if __name__=='__main__':
    unittest.main()
