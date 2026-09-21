import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lab.live.launch import DEFAULT, validate, materialize, ROOT
from lab.live.model import LiveFabric
from lab.live.workload_model import catalog, directions
from lab.live.workloads import Workloads
from lab.live.workload_capture import matches
from types import SimpleNamespace
from lab.live import evpn_codec as wire
from lab.live.service import Service


def fixture(igp='isis', carriers=None, gateways=2):
    nodes = [{'id':f'gw-{i+1:04d}','kind':'gateway'} for i in range(gateways)]+[{'id':'sat-0001','kind':'satellite'}]
    links = [{'id':a+'--'+b, 'source':a,'target':b,'capacity_bps':1000000000,'latency_us':1000}
             for n in nodes if n['kind']=='gateway' for a,b in [(n['id'],'sat-0001'),('sat-0001',n['id'])]]
    return LiveFabric({'topology':{'nodes':nodes,'links':links},
                      'intents':[{'id':'sample','source':'gw-0001','destination':'gw-0002'}],
                      'network_profile':{**DEFAULT,'igp':igp,'carriers':carriers or ['evpn-srv6']}}, 'sf-protocol-check')


class LaunchTests(unittest.TestCase):
    def test_stop_does_not_interrupt_wrapper_cleanup_after_runtime_failure(self):
        service=Service()
        for phase in ('failed','stopping','stopped'):
            with self.subTest(phase=phase), patch.object(service,'status',return_value={
                'active':True,'phase':phase,'supervisor_pid':123}), patch('lab.live.service.os.kill') as kill:
                self.assertEqual(service.stop()['phase'],'stopping')
                kill.assert_not_called()

    def test_l2_evpn_wire_mac_imet_withdrawal_and_malformed_sid(self):
        for evi in (201,202):
            for mac in (None,'02:53:00:c9:00:01'):
                packet=wire.l2_update('10.255.0.1',evi,'fc00:0:1::ffff','fc00:0:1::20c9',mac=mac,sequence=7)
                routes,withdrawn=wire.read_update(packet[19:])
                self.assertFalse(withdrawn); self.assertEqual(len(routes),1)
                route=routes[0]
                self.assertEqual(route['evi'],evi)
                self.assertEqual(route['behavior'],'End.DX2')
                self.assertEqual(route['route_type'],2 if mac else 3)
                if mac: self.assertEqual(route['sequence'],7)
                withdrawal=wire.l2_update('10.255.0.1',evi,'fc00:0:1::ffff','fc00:0:1::20c9',mac=mac,withdraw=True)
                self.assertEqual(wire.read_update(withdrawal[19:]),([],[route['key']]))
                # Changing the advertised behavior must not install an L2 SID.
                sid=bytes.fromhex('fc0000000001000000000000000020c9')
                damaged=packet[19:].replace(sid+b'\x00\x00\x15\x00',sid+b'\x00\x00\x11\x00')
                self.assertEqual(wire.read_update(damaged),([],[route['key']]))

    def test_reject_invalid_or_unimplemented_selection(self):
        for changes in [{'satellites':1000}, {'satellites':240,'gateways':24}, {'satellites':121},
                        {'gateways':True}, {'carriers':[]}, {'carriers':['evpn-srv6','evpn-srv6']},
                        {'igp':'ospf','carriers':['pcep']}, {'algorithm':'ecmp'}, {'algorithm':[]},
                        {'carriers':['arbitrary']}, {'command':'rm'}, {'step_seconds':float('nan')}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError): validate({**DEFAULT,**changes})

    def test_materialized_files_match_selection_and_do_not_modify_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'scenarios').symlink_to(ROOT/'scenarios', target_is_directory=True)
            paths = materialize({**DEFAULT,'satellites':60,'gateways':4,'planes':6,'algorithm':'hops','carriers':['srv6','evpn-srv6']}, root)
            c = json.loads(Path(paths['constellation']).read_text())
            self.assertEqual((c['satellites'],c['gateways'],c['planes']), (60,4,6))
            w = json.loads(Path(paths['workloads']).read_text())
            flows = catalog(w, ['gw-'+str(i) for i in range(4)])
            self.assertEqual(len(flows),16)
            self.assertEqual({f['carrier'] for f in flows},{'srv6','evpn-srv6'})
            self.assertEqual(json.loads(Path(paths['launch']).read_text())['algorithm'],'hops')
            self.assertEqual(json.loads((ROOT/'scenarios/constellations/live-workloads.json').read_text())['copies_per_profile'],48)

    def test_selected_protocols_apply_to_initial_and_dynamic_links(self):
        for igp in ('isis','ospf'):
            f = fixture(igp)
            for node in f.nodes:
                config = f.config(node)
                self.assertNotIn('mpls', config)
                self.assertNotIn('ldpd', f.daemons(node))
                if igp == 'isis':
                    self.assertIn('ipv6 router isis SF', config)
                    self.assertNotIn('ospf', config)
                else:
                    self.assertNotIn('isis', config)
                    self.assertIn('router ospf6', config)
            dynamic = '\n'.join(f.interface_commands('gw-0001','sat-0001'))
            self.assertNotIn('mpls',dynamic)
            self.assertIn('ipv6 router isis SF' if igp=='isis' else 'ipv6 ospf6 area',dynamic)
            jobs = f.wiring({n:i+1 for i,n in enumerate([*f.nodes,'nr_gnb','upf'])})
            self.assertFalse(any('vxlan' in j['args'] or 'br100' in j['args'] for j in jobs))

    def test_optional_legacy_carriers_enable_their_dependencies(self):
        f = fixture(carriers=['ldp','pcep','evpn'])
        config = f.config('gw-0001')
        for text in ('segment-routing on','mpls ldp','address-family link-state','address-family l2vpn evpn'):
            self.assertIn(text,config)

    def test_selected_live_profile_reserves_only_real_workload_and_n3_intents(self):
        f = fixture()
        self.assertEqual({i['id'] for i in f.scenario['intents']},{'n3-forward','n3-reverse'})
        self.assertEqual(f.gateway_intents,[])

    def test_evpn_steering_requires_and_uses_the_received_service_sid(self):
        with tempfile.TemporaryDirectory() as directory:
            f=fixture()
            config=json.loads((ROOT/'scenarios/constellations/live-workloads.json').read_text())
            config['profiles']=config['profiles'][:1]; config['copies_per_profile']=1
            config['profiles'][0].update(carrier='evpn-srv6',control='hybrid')
            w=Workloads(SimpleNamespace(identifier='test',fabric=f,art=Path(directory)),config)
            flow=w.flows[0]; d=next(directions(flow)); path=[d['node'],'sat-0001',d['remote']]
            with self.assertRaisesRegex(ValueError,'not received'): w.route(flow,d,path)
            learned=f.locator(d['remote'])+'abcd'
            w.evpn_ribs={d['node']:{'routes':{d['target']+'/32':{'sid':learned,'peer':f.locator(d['remote'])+'ffff'}}}}
            args=w.route(flow,d,path)
            self.assertEqual(args[args.index('segs')+1].split(',')[-1],learned)
            self.assertEqual(args[args.index('dev')+1],'sr0')
            self.assertTrue(matches('evpn-srv6',{'layers':['srh']}))
            self.assertFalse(matches('evpn-srv6',{'layers':['srh','vxlan']}))
            self.assertFalse(matches('evpn-srv6',{'layers':[]}))

    def test_service_passes_immutable_paths_and_rejects_a_second_configuration(self):
        import fcntl
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'scenarios').symlink_to(ROOT/'scenarios',target_is_directory=True)
            service=Service(root)
            with patch('lab.live.service.subprocess.Popen') as popen, patch.object(service,'status',return_value={'active':True}):
                service.start(DEFAULT)
                env=popen.call_args.kwargs['env']
                self.assertEqual(json.loads(Path(env['SF_LIVE_LAUNCH_CONFIG']).read_text()),DEFAULT)
                self.assertEqual(json.loads(Path(env['SF_LIVE_CONSTELLATION']).read_text())['gateways'],24)
                self.assertEqual(env['SF_LIVE_STEP'],'10')
            with (service.directory/'service.lock').open('a+') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with patch('lab.live.service.subprocess.Popen') as popen, self.assertRaises(RuntimeError):
                    service.start({**DEFAULT,'algorithm':'hops'})
                popen.assert_not_called()


class EVPNCodecTests(unittest.TestCase):
    def test_wire_type5_and_service_sid(self):
        packet = wire.update('10.255.0.1','198.18.0.2/32','fc00:0:1::ffff','fc00:0:1::d')
        # Type-5 /34-byte NLRI, IPv4 RD, zero ESI/tag, /32, implicit-null.
        expected = bytes.fromhex('052200010aff00010064'+'00'*14+'20c6120002'+'00'*4+'000030')
        self.assertIn(expected,packet)
        # Prefix-SID attribute 40, SRv6 L3 TLV=5, SID info=1, End.DX4=17.
        self.assertIn(bytes.fromhex('0500190001001500')+bytes.fromhex('fc00000000010000000000000000000d')+bytes.fromhex('00001100'),packet)
        routes, withdrawn = wire.read_update(packet[19:])
        self.assertEqual(withdrawn,[])
        self.assertEqual(routes[0]['sid'],'fc00:0:1::d')
        self.assertEqual(routes[0]['prefix'],'198.18.0.2/32')
        self.assertEqual(wire.read_update(wire.update('10.255.0.1','198.18.0.2/32','::1','::d',True)[19:]),([],['198.18.0.2/32']))

    def test_bad_service_sid_is_withdrawn_and_truncated_messages_rejected(self):
        packet = wire.update('10.255.0.1','198.18.0.2/32','fc00:0:1::ffff','fc00:0:1::d')[19:]
        self.assertEqual(wire.read_update(packet.replace(b'\x00\x11\x00',b'\x00\x15\x00')),([],['198.18.0.2/32']))
        for n in range(4,len(packet)):
            with self.assertRaises(ValueError): wire.read_update(packet[:n])
        self.assertEqual(wire.read_open(wire.open_message('10.255.0.1')[19:]),30)
        with self.assertRaises(ValueError): wire.read_open(wire.open_message('10.255.0.1')[19:].replace(b'\x00\x46',b'\x00\x47'))


if __name__ == '__main__': unittest.main()
