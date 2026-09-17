"""Topology binding must preserve scale, identities, connectivity and isolation."""
import copy
import ipaddress
import unittest
from fabric import Fabric


def scenario(satellites=360,gateways=8):
    ids=[f'sat-{i:04d}' for i in range(1,satellites+1)]+[f'gw-{i:03d}' for i in range(1,gateways+1)]
    nodes=[{'id':n,'kind':'satellite' if n.startswith('sat-') else 'gateway','enabled':True} for n in ids]
    links=[]
    for a,b in zip(ids,ids[1:]+ids[:1]):
        links += [{'id':x+'--'+y,'source':x,'target':y,'latency_us':2000} for x,y in [(a,b),(b,a)]]
    return {'topology':{'nodes':nodes,'links':links},'intents':[{'id':'flow-1','source':ids[-2],'destination':ids[-1]}]}

class FabricTests(unittest.TestCase):
    def test_physical_routes_bind_neighbors_and_port_rating_stays_separate(self):
        s=scenario(4,2)
        s['physical_model']={'config':{'links':{'queue_limit_packets':10000,
            'isl':{'max_capacity_bps':10000000000},'feeder':{'max_capacity_bps':1000000000}}}}
        for link in s['topology']['links']:
            link.update(capacity_bps=123456789,link_type='oisl' if all(n.startswith('sat-') for n in (link['source'],link['target'])) else 'feeder')
        f=Fabric(s,'sf-test')
        for node in f.nodes:
            for peer in f.adjacency[node]:
                self.assertEqual(f.nodes[node]['labels']['next_hop:'+peer],f.hop(node,peer))
                self.assertNotEqual(f.hop(node,peer),f.loopback(peer))
        links=copy.deepcopy(f.scenario['topology']['links'])
        for link in links:link.update(latency_us=98765,capacity_bps=87654321)
        before=f.config('sat-0001')
        f.set_links(links)
        self.assertEqual(f.config('sat-0001'),before)
        self.assertEqual(f.directed['sat-0001','sat-0002']['latency_us'],98765)
        self.assertEqual(f.port_capacity('sat-0001','sat-0002'),10000000000)
        self.assertEqual(f.port_capacity('sat-0001','gw-002'),1000000000)
        self.assertTrue(all(link['cost']==1 for link in f.links.values()))

    def test_360_scale_binding_preserves_graph_and_unique_addresses(self):
        source=scenario();before=copy.deepcopy(source);f=Fabric(source,'sf-test')
        self.assertEqual(source,before)
        self.assertEqual(len(f.nodes),368)
        self.assertEqual({n['id'] for n in source['topology']['nodes']},set(f.nodes))
        self.assertEqual(f.scenario['topology']['links'],source['topology']['links'])
        addresses=[f.loopback(n) for n in f.nodes]
        addresses += [address for link in f.links.values() for address in link['endpoints'].values()]
        self.assertEqual(len(addresses),len(set(addresses)))
        self.assertTrue(all(ipaddress.ip_address(a).version==4 for a in addresses))
        self.assertTrue(any(int(a.split('.')[2])>0 for a in [f.loopback(n) for n in f.nodes]))
        self.assertEqual(len({f.sid(n) for n in f.nodes}),368)
        for node in f.nodes:self.assertEqual(f.by_system_id[f.system_id(node)],node)

    def test_service_uses_configured_gateways_and_every_generated_intent(self):
        f=Fabric(scenario(120,4),'sf-test')
        self.assertEqual(f.pair,('gw-003','gw-004'))
        self.assertEqual(f.service()[0][0:2],f.pair)
        self.assertEqual(f.scenario['intents'][0]['destination_prefix'],'10.240.0.1/32')
        self.assertEqual(len(f.scenario['intents']),3)
        self.assertIn('frr_container',f.nodes['sat-0001']['labels'])
        self.assertEqual(f.nodes['sat-0001']['labels']['runtime'],'frr')

    def test_unpaired_links_rejected(self):
        s=scenario(4,2);s['topology']['links'].pop()
        with self.assertRaises(ValueError):Fabric(s,'sf-test')

    def test_stale_sr_next_hop_is_rejected_even_if_frr_marks_it_installed(self):
        f=Fabric(scenario(4,2),'sf-test');node,target='gw-001','gw-002'
        hop={'installed':True,'nexthop':f.hop(node,target),'outLabel':3}
        table={str(f.sid(target)):{'installed':True,'nexthops':[hop]}}
        self.assertEqual(f.sr_mismatches({node:table},[target]),[])
        table[str(f.sid(target))]['nexthops'].append({'installed':True,'nexthop':f.hop(node,'sat-0004'),'outLabel':f.sid(target)})
        self.assertEqual(len(f.sr_mismatches({node:table},[target])),1)
        self.assertNotIn(target,f.shortest_hops(target,(node,target))[node])
