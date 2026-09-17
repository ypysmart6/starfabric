import copy
import ipaddress
import json
from pathlib import Path
import socket
import struct
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lab.live.workload_model import catalog, controller_intents, summary
from lab.live.workload_traffic import Counters, Worker, identify, packet
from lab.live.workload_capture import decode, matches
from lab.live.workloads import Workloads
from lab.live.workload_snapshot import observe
from lab.platform.pce import PCE, objects
from frontend.server import Dashboard

ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads((ROOT/'scenarios/constellations/live-workloads.json').read_text())


def ipv4(payload, protocol=17, source='198.18.0.2', target='198.18.0.3'):
    return struct.pack('!BBHHHBBH4s4s', 0x45, 0, len(payload)+20, 0, 0, 64, protocol, 0,
                       socket.inet_aton(source), socket.inet_aton(target)) + payload


def ethernet(payload, protocol=0x800):
    return bytes(12) + struct.pack('!H', protocol) + payload


class WorkloadTests(unittest.TestCase):
    def test_initial_fraction_paces_workers_without_reducing_reservations(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime=SimpleNamespace(fabric=SimpleNamespace(gateways=['a','b']),art=Path(folder),identifier='test')
            workloads=Workloads(runtime,CONFIG)
            self.assertEqual(json.loads((workloads.folder/'rates.json').read_text())['scale'],.25)
            self.assertEqual(summary(workloads.flows)['offered_payload_bps'],101760000)

    def test_channel_batches_keep_fleet_wide_down_shape_up_barriers(self):
        from lab.platform import physics_jobs
        commands=[['ip','link','set','sf1','down'],['tc','qdisc','change','dev','sf1'],
                  ['ip','link','set','sf2','up'],['ip','-j','link','show','dev','sf2']]
        jobs=[{'name':n,'pid':1,'commands':commands,'timeout_seconds':60} for n in ('a','b')]
        phases=[]
        def apply(job):
            phases.append(job['commands'][0] if job['commands'] else [])
            return job['name'],[]
        with patch.object(physics_jobs,'apply',side_effect=apply):
            self.assertEqual(set(physics_jobs.execute(jobs,2)),{'a','b'})
        self.assertEqual(phases,[c for c in commands for _ in range(2)])
        called=[]
        def run(command,**kwargs):
            called.append((command,kwargs))
            return SimpleNamespace(returncode=0,stdout='',stderr='')
        with patch.object(physics_jobs.subprocess,'run',side_effect=run):
            physics_jobs.apply({'name':'a','pid':1,'timeout_seconds':60,'commands':[commands[0],commands[0],commands[1],commands[2]]})
        self.assertEqual(len(called),3)
        self.assertEqual(called[0][0][-3:],['ip','-batch','-'])
        self.assertEqual(called[0][1]['input'],'link set sf1 down\n'*2)
        self.assertEqual(called[1][0][-3:],['tc','-batch','-'])
        self.assertTrue(all(kwargs['timeout']==60 for _,kwargs in called))

    def test_evpn_readiness_requires_exact_valid_addresses_and_one_read(self):
        from lab.live import runtime  # establish platform's runtime imports
        from protocols import Protocols
        fabric = SimpleNamespace(gateways=['a','b','c'], svi=lambda n:{'a':'10.232.0.2','b':'10.232.0.1','c':'10.232.0.10'}[n])
        owner = SimpleNamespace(fabric=fabric, vty=Mock(return_value=' *>i [2]:[32]:[10.232.0.10]\n    [2]:[32]:[10.232.0.1]'))
        self.assertEqual(Protocols(owner).evpn_missing('a'), ['b'])
        self.assertEqual(owner.vty.call_count, 1)

    def test_regression_accumulates_real_phase_proofs_and_invalidates_changed_paths(self):
        from lab.live.validate_workloads import Regression

        def snapshot(proven, path='original', evidence_at=101):
            return {'flows': [{'id': name, 'fresh': True, 'evidence_fresh': name in proven,
                'metrics': {'goodput_bps': 1000}, 'evidence_at': evidence_at,
                'evidence': {'forward': 1, 'reverse': 1} if name in proven else {},
                'directions': {d: {'status': 'ready', 'nodes': [path] if name == 'a' else ['b']}
                               for d in ('forward', 'reverse')}} for name in ('a', 'b')]}

        cases = [([snapshot({'a'}), snapshot({'b'})], 2),
                 ([snapshot({'a'}), snapshot({'b'}, 'changed'), snapshot({'a'}, 'changed')], 3),
                 ([snapshot({'a','b'}, evidence_at=99), snapshot({'a'}), snapshot({'b'})], 3)]
        for samples, count in cases:
            with self.subTest(samples=count), tempfile.TemporaryDirectory() as folder:
                runtime = SimpleNamespace(current_plan={}, art=Path(folder), event=Mock(),
                    workloads=SimpleNamespace(update=Mock(), snapshot=Mock(side_effect=samples)))
                with patch('lab.live.validate_workloads.time.sleep'), patch('lab.live.validate_workloads.time.time', return_value=100):
                    result = Regression.verify(runtime, 'test')
                self.assertEqual(runtime.workloads.snapshot.call_count, count)
                self.assertEqual(set(result['verification']['flows']), {'a','b'})
                self.assertEqual(result['verification']['best_simultaneously_verified'], 1)
                self.assertEqual(sum(f['evidence_fresh'] for f in result['flows']), 1)

    def test_default_catalog_has_unique_endpoints_and_no_controller_ospf_routes(self):
        gateways = [f'gw-{i:03d}' for i in range(1, 25)]
        flows = catalog(CONFIG, gateways)
        self.assertEqual(len(flows), 384)
        endpoints = [f[k] for f in flows for k in ('source_address', 'destination_address')]
        self.assertEqual(len(set(endpoints)), 768)
        self.assertTrue(all(ipaddress.ip_address(a) for a in endpoints))
        intents = controller_intents(flows)
        self.assertEqual(len(intents), 576)
        distributed = {f['id'] for f in flows if f['control'] == 'distributed'}
        self.assertFalse(any(i['labels']['workload'] in distributed for i in intents))
        self.assertEqual(sum(f['rate_bps'] for f in flows), 101760000)
        distribution = summary(flows)
        self.assertEqual(distribution['gateway_pairs'], 276)
        for node in gateways:
            self.assertEqual(distribution['gateway_distribution'][node]['originating'], 16)
            self.assertEqual(distribution['gateway_distribution'][node]['terminating'], 16)
            self.assertEqual(len(distribution['gateway_distribution'][node]['peers']), 23)
        for profile in CONFIG['profiles']:
            selected = [f for f in flows if f['carrier'] == profile['carrier']]
            for node in gateways:
                self.assertEqual(sum(f['source'] == node for f in selected), 2)
                self.assertEqual(sum(f['destination'] == node for f in selected), 2)
        for key in ('port', 'table', 'binding_sid'):
            self.assertEqual(len({f[key] for f in flows}), len(flows))

    def test_mesh_works_for_partial_rounds_and_legacy_ring_remains_available(self):
        for size in (2, 3, 16, 24, 32):
            gateways = [f'g{i}' for i in range(size)]
            for copies in (1, size, min(64, size * 2 + 1)):
                flows = catalog(dict(CONFIG, copies_per_profile=copies), gateways)
                self.assertTrue(all(f['source'] != f['destination'] for f in flows))
                for profile in CONFIG['profiles']:
                    selected = [f for f in flows if f['carrier'] == profile['carrier']]
                    counts = [sum(f['source'] == node for f in selected) for node in gateways]
                    self.assertLessEqual(max(counts) - min(counts), 1)
        flows = catalog(dict(CONFIG, copies_per_profile=4, pairing='ring'), ['a','b','c','d'])
        self.assertEqual([(f['source'], f['destination']) for f in flows[:4]],
                         [('a','b'),('b','c'),('c','d'),('d','a')])

    def test_small_global_trials_keep_all_gateway_origins(self):
        gateways = [f'gw-{i:03d}' for i in range(1,25)]
        for copies in (3,4,8,12,16):
            flows = catalog(dict(CONFIG, copies_per_profile=copies), gateways)
            counts = [sum(f['source']==node for f in flows) for node in gateways]
            self.assertGreater(min(counts),0)
            self.assertLessEqual(max(counts)-min(counts),1)

    def test_sparse_global_trial_uses_all_available_endpoints(self):
        gateways = [f'gw-{i:03d}' for i in range(1,25)]
        for copies in (1,2):
            flows = catalog(dict(CONFIG, copies_per_profile=copies), gateways)
            endpoints = {n for f in flows for n in (f['source'],f['destination'])}
            self.assertEqual(len(endpoints),min(24,2*len(flows)))
            self.assertTrue(all(f['source'] != f['destination'] for f in flows))

    def test_unbounded_and_mismatched_input_is_rejected(self):
        for key, value in [('rate_scale', float('nan')), ('copies_per_profile', 100),
                           ('sample_seconds', 0), ('capture_seconds', 1000), ('pairing', 'random'),
                           ('initial_rate_fraction',0), ('initial_rate_fraction',float('nan'))]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                catalog(dict(CONFIG, **{key:value}), ['a','b'])
        changed = copy.deepcopy(CONFIG)
        changed['profiles'][0]['control'] = 'centralized'
        with self.assertRaises(ValueError):
            catalog(changed, ['a','b'])

    def test_route_observations_are_batched_and_missing_prefix_stays_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            config = dict(CONFIG, copies_per_profile=16, profiles=[CONFIG['profiles'][0]])
            runtime = SimpleNamespace(fabric=SimpleNamespace(gateways=['a','b']), art=Path(folder),
                                      identifier='test', protocols=SimpleNamespace(edges=lambda:set()))
            workloads = Workloads(runtime, config)
            first = workloads.flows[0]
            runtime.vty = Mock(return_value=json.dumps({first['destination_address']+'/32':
                                [{'protocol':'ospf','installed':True}]}))
            workloads.jobs = Mock()
            workloads.maybe_capture = Mock()
            workloads.update(None)
            self.assertEqual(runtime.vty.call_count, 2)
            self.assertEqual(workloads.states[first['id']+'-forward']['status'], 'ready')
            self.assertEqual(workloads.states[first['id']+'-reverse']['status'], 'pending')
            runtime.vty.side_effect = RuntimeError('read failed')
            workloads.update(None)
            self.assertTrue(all(s['status']=='pending' for s in workloads.states.values()))

    def test_packet_proof_requires_correct_run_and_encapsulation(self):
        token = bytes.fromhex('12'*16)
        data = packet(token, 1, 99, 256)
        udp = struct.pack('!HHHH', 1234, 20001, len(data)+8, 0) + data
        inner = ipv4(udp)
        plain = decode(ethernet(inner))
        self.assertEqual(identify(plain['payload'], token)[:2], (1,99))
        self.assertIsNone(identify(plain['payload'], bytes(16)))
        self.assertTrue(matches('ospf', plain))
        for carrier in ('srv6', 'sr-mpls', 'pcep', 'evpn', 'ldp', 'ospf6'):
            self.assertFalse(matches(carrier, plain), carrier)
        for label, carrier in [(16004, 'sr-mpls'), (16004, 'pcep'), (42, 'ldp')]:
            mpls = struct.pack('!I', (label << 12) | 0x140) + inner
            decoded = decode(ethernet(mpls, 0x8847))
            self.assertTrue(matches(carrier, decoded))
            self.assertEqual(identify(decoded['payload'], token)[:2], (1,99))
        srh = bytes([4,2,4,0,0,0,0,0])+socket.inet_pton(socket.AF_INET6,'fc00::1')
        outer = struct.pack('!IHBB16s16s', 6 << 28, len(srh+inner), 43, 64,
                            socket.inet_pton(socket.AF_INET6,'fc00::2'), socket.inet_pton(socket.AF_INET6,'fc00::1'))
        self.assertTrue(matches('srv6', decode(ethernet(outer+srh+inner, 0x86dd))))
        vxlan = bytes([8,0,0,0,0,0,100,0]) + ethernet(inner)
        overlay = ipv4(struct.pack('!HHHH', 50000, 4789, len(vxlan)+8, 0)+vxlan)
        self.assertTrue(matches('evpn', decode(ethernet(overlay))))
        self.assertIsNone(decode(ethernet(inner)[:-1]))

    def test_pending_packets_are_not_immediately_counted_as_loss(self):
        counters = Counters()
        counters.sent(1, 256)
        self.assertIsNone(counters.snapshot()['loss_percent'])
        counters.acknowledged(1)
        counters.acknowledged(1)
        value = counters.snapshot()
        self.assertEqual(value['acked_packets'], 1)
        self.assertEqual(value['loss_percent'], 0)

    def test_actual_tcp_and_udp_workers_deliver_and_accept_bounded_rate_changes(self):
        flows = catalog(dict(CONFIG, copies_per_profile=1), ['left', 'right'])
        flows = [next(f for f in flows if f['transport'] == t) for t in ('tcp','udp')]
        for flow in flows:
            flow.update(source_address='127.0.0.2', destination_address='127.0.0.3', rate_bps=64000)
            with socket.socket() as sock:
                sock.bind(('127.0.0.3',0))
                flow['port'] = sock.getsockname()[1]
        with tempfile.TemporaryDirectory() as folder:
            token = '12'*16
            rates = Path(folder)/'rates.json'
            rates.write_text(json.dumps({'run_id':'test','token':token,'scale':.5}))
            workers = [Worker({'run_id':'test','token':token,'node':node,'flows':flows,
                        'sample_seconds':.1, 'rate_file':str(rates), 'output':str(Path(folder)/(node+'.json'))}) for node in ('left','right')]
            origins = {w.node:w for w in workers}
            threads = [threading.Thread(target=w.run) for w in workers]
            try:
                for thread in reversed(threads): thread.start()
                deadline = time.monotonic()+5
                while time.monotonic()<deadline:
                    if all(origins[f['source']].counters[f['id']].values['acked_packets']>=3 for f in flows): break
                    time.sleep(.05)
                for flow in flows:
                    self.assertGreaterEqual(origins[flow['source']].counters[flow['id']].values['acked_packets'], 3)
                self.assertEqual(workers[0].rate_scale, .5)
                rates.write_text(json.dumps({'run_id':'other','token':token,'scale':1}))
                workers[0].reload_rate()
                self.assertEqual(workers[0].rate_scale, .5)
            finally:
                for worker in workers: worker.stopped.set()
                for thread in threads: thread.join(2)

    def test_missing_controller_path_withdraws_encapsulation_without_native_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            f = SimpleNamespace(gateways=['a','b'])
            runtime = SimpleNamespace(fabric=f,art=Path(folder),identifier='test', pids={'a':1,'b':2},
                                      protocols=SimpleNamespace(edges=lambda:set()))
            config = copy.deepcopy(CONFIG)
            config.update(copies_per_profile=1, profiles=[next(p for p in CONFIG['profiles'] if p['carrier']=='srv6')])
            workloads = Workloads(runtime,config)
            workloads.installed['interactive-01-forward'] = ['old-steering']
            workloads.jobs = Mock()
            workloads.maybe_capture = Mock()
            workloads.update(None)
            operations = workloads.jobs.call_args.args[1]
            self.assertEqual(len(operations), 1)
            self.assertIn('unreachable', operations[0]['args'])
            self.assertIn(str(workloads.flows[0]['table']), operations[0]['args'])
            self.assertFalse(workloads.installed)
            self.assertEqual(workloads.states['interactive-01-forward']['status'], 'pending')

    def test_pce_resolves_per_business_endpoint_and_rejects_stale_graph(self):
        pce = PCE.__new__(PCE)
        pce.responses = []
        pce.log = Mock()
        pce.protocols = SimpleNamespace(resolve_pcep_target=lambda target:'business-two' if target=='198.18.0.9' else None,
            f=SimpleNamespace(sid=lambda node:16000+int(node)),
            path=lambda intent:({'id':intent,'topology_version':5}, ['1','2','3']),
            edges=lambda:{('1','2'),('2','3')})
        rp = struct.pack('!BBHI', 2, 0x12, 8, 1)
        endpoints = struct.pack('!BBH4s4s', 4, 0x12, 12, socket.inet_aton('198.18.0.8'), socket.inet_aton('198.18.0.9'))
        reply = pce.reply(rp+endpoints,32)
        self.assertTrue(any(cls==7 for cls,_,_ in objects(reply[4:])))
        self.assertEqual(pce.responses[-1]['intent'],'business-two')
        pce.protocols.edges = lambda:set()
        reply = pce.reply(rp+endpoints,32)
        self.assertTrue(any(cls==3 for cls,_,_ in objects(reply[4:])))
        self.assertEqual(len(pce.responses),1)

    def test_rate_api_rejects_stale_run_and_above_reserved_capacity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run = 'sf-unified-abc123'
            directory = root/'lab/unified/artifacts'/run/'workloads'
            directory.mkdir(parents=True)
            (directory/'catalog.json').write_text(json.dumps({'run_id':run,'token':'12'*16,'flows':[{'id':'test'}]}))
            dashboard = Dashboard(root)
            dashboard.service.status = lambda:{'run_id':run,'active':True}
            result = dashboard.set_workload_rate({'run_id':run,'scale':.25})
            self.assertEqual(result['state'],'requested')
            before = (directory/'rates.json').read_bytes()
            for body in ({'run_id':run,'scale':2}, {'run_id':'sf-unified-old','scale':.5},
                         {'run_id':run,'scale':float('nan')}, {'run_id':run,'scale':True}):
                with self.assertRaises(ValueError): dashboard.set_workload_rate(body)
            self.assertEqual((directory/'rates.json').read_bytes(),before)

    def test_capture_is_bound_to_run_and_service_path_not_unrelated_plan_changes(self):
        with tempfile.TemporaryDirectory() as folder, patch('lab.live.workloads.ROOT', Path(folder)):
            runtime = SimpleNamespace(fabric=SimpleNamespace(gateways=['a','b']),art=Path(folder),identifier='test')
            config = dict(CONFIG, copies_per_profile=1, profiles=[next(p for p in CONFIG['profiles'] if p['carrier']=='srv6')])
            workloads = Workloads(runtime,config)
            flow = workloads.flows[0]
            for suffix in ('forward','reverse'):
                workloads.states[flow['id']+'-'+suffix] = {'status':'ready','nodes':['a','x','b']}
            proof = {'run_id':'test','token':workloads.token,'finished_at':time.time(),
                     'signatures':{flow['id']:workloads.proof_signature(flow)},'flows':{flow['id']:{'forward':1,'reverse':1}}}
            path = workloads.folder/'capture-latest.json'
            path.write_text(json.dumps(proof))
            workloads.plan = {'id':'other-plan-with-the-same-service-path'}
            self.assertTrue(workloads.snapshot()['flows'][0]['evidence_fresh'])
            workloads.states[flow['id']+'-forward']['nodes'] = ['a','y','b']
            self.assertFalse(workloads.snapshot()['flows'][0]['evidence_fresh'])
            proof['token'] = 'not-this-run'
            path.write_text(json.dumps(proof))
            self.assertFalse(workloads.snapshot()['flows'][0]['evidence'])

    def test_new_packet_metrics_do_not_wait_for_another_orbital_snapshot(self):
        with tempfile.TemporaryDirectory() as folder, patch('lab.live.workloads.ROOT', Path(folder)):
            runtime = SimpleNamespace(fabric=SimpleNamespace(gateways=['a','b']),art=Path(folder),identifier='test')
            workloads = Workloads(runtime,dict(CONFIG,copies_per_profile=1,profiles=[CONFIG['profiles'][0]]))
            before = workloads.snapshot()
            self.assertFalse(before['flows'][0]['fresh'])
            for node in ('a','b'):
                (workloads.folder/('metrics-'+node+'.json')).write_text(json.dumps({'run_id':'test','token':workloads.token,
                    'observed_at':time.time(),'rate_scale':.5,'flows':{'telemetry-01':{'goodput_bps':32000}}}))
            current = observe(before,workloads.folder)
            self.assertTrue(current['flows'][0]['fresh'])
            self.assertEqual(current['flows'][0]['metrics']['goodput_bps'],32000)
            self.assertFalse(before['flows'][0]['fresh'])


if __name__ == '__main__':
    unittest.main()
