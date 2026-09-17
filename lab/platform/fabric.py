"""Bind the existing constellation generator's graph to real FRR resources."""
from __future__ import annotations
import copy
import ipaddress
import json
import heapq
from pathlib import Path
from channel import qdisc_command, frr_cost, frr_bandwidth


def ip(base, offset):
    return str(ipaddress.ip_address(base) + offset)


class Fabric:
    def __init__(self, scenario, identifier):
        self.scenario = copy.deepcopy(scenario)
        self.nodes = {n['id']: n for n in self.scenario['topology']['nodes']}
        self.satellites = sorted(n for n,v in self.nodes.items() if v['kind']=='satellite')
        self.gateways = sorted(n for n,v in self.nodes.items() if v['kind']=='gateway')
        if len(self.gateways)<2 or not self.satellites or len(self.nodes)>7999:
            raise ValueError('live fabric requires satellites, at least two gateways and at most 7999 nodes')
        self.ordinal = {n:i+1 for i,n in enumerate(sorted(self.nodes))}
        self.by_system_id = {self.system_id(n):n for n in self.nodes}
        self.pair = (self.scenario['intents'][0]['source'], self.scenario['intents'][0]['destination'])
        self.links = {}
        self.adjacency = {n:{} for n in self.nodes}
        self.directed = {(l['source'], l['target']): l for l in self.scenario['topology']['links']}
        # Keep ephemerides/replay evidence outside the controller ConfigMap.
        self.physical_model = self.scenario.pop('physical_model', None)
        self.queue_limit = self.physical_model['config']['links']['queue_limit_packets'] if self.physical_model else 10000
        for link in self.scenario['topology']['links']:
            a,b=link['source'],link['target']
            pair=tuple(sorted((a,b)))
            if pair not in self.links:
                k=len(self.links)+1
                self.links[pair]={'index':k,'interface':f'sf{k}', 'cost':1 if self.physical_model else frr_cost(link),
                                 'endpoints':{p:ip('10.128.0.0',4*k+i+1) for i,p in enumerate(pair)}}
            self.adjacency[a][b]=self.links[pair]
        for a,b in self.links:
            if a not in self.adjacency[b] or b not in self.adjacency[a]:
                raise ValueError('live fabric requires paired directed links')
        for name,node in self.nodes.items():
            node['loopback']=self.loopback(name)
            node.setdefault('labels',{}).update(frr_container=identifier+'-'+name, probe_target=self.loopback(name), runtime='frr')
            if self.physical_model:
                node['labels'].update({'next_hop:'+peer:self.hop(name,peer) for peer in self.adjacency[name]})
        # Keep every configured gateway intent and bind each to a real /32 sink.
        self.service_addresses={}
        for i,intent in enumerate(self.scenario['intents']):
            address=ip('10.240.0.0',i+1)
            intent['destination_prefix']=address+'/32'
            intent['class']='live-gateway-traffic'
            self.service_addresses[intent['id']]=address
        self.gateway_intents=copy.deepcopy(self.scenario['intents'])
        for name,src,dst,prefix in [('n3-forward',*self.pair,'172.22.0.8/32'),
                                  ('n3-reverse',self.pair[1],self.pair[0],'172.22.0.23/32')]:
            self.scenario['intents'].append({'id':name,'source':src,'destination':dst,'destination_prefix':prefix,
                'policy':'latency','redundancy':1,'demand_bps':1000000,'priority':200,'class':'5g-n3'})
        description = 'Per-satellite physical contacts instantiated as live FRR with directional packet shaping' if self.physical_model else 'Generated constellation instantiated as live FRR; configured logical mesh'
        self.scenario.update(scenario_id=identifier,description=description,timeline=[])

    def loopback(self,node): return ip('10.255.0.0',self.ordinal[node])
    def system_id(self,node):
        raw=f'{self.ordinal[node]:012x}'
        return '.'.join(raw[i:i+4] for i in range(0,12,4))
    def sid(self,node): return 16000+self.ordinal[node]
    def locator(self,node): return f'fc00:0:{self.ordinal[node]:x}::'
    def svi(self,node): return ip('10.232.0.0',self.gateways.index(node)+1)
    def hop(self,node,peer): return self.adjacency[node][peer]['endpoints'][peer]
    def interface(self,node,peer): return self.adjacency[node][peer]['interface']
    def active(self,node,peer):
        link=self.directed[node,peer]
        return link.get('admin_up',True) and link.get('operational_up',True) and link.get('acquisition_state','locked') in ('locked','degraded','')
    def active_pairs(self):
        return {pair for pair in self.links if self.active(*pair) and self.active(*pair[::-1])}
    def set_links(self,links):
        self.directed = {(l['source'],l['target']):l for l in links}
        self.scenario['topology']['links']=list(links)
        for (a,b),link in self.links.items():link['cost']=1 if self.physical_model else frr_cost(self.directed[a,b])

    def port_capacity(self,node,peer):
        if not self.physical_model:return self.directed[node,peer].get('capacity_bps',1000000000)
        kind='isl' if self.directed[node,peer]['link_type']=='oisl' else 'feeder'
        return self.physical_model['config']['links'][kind]['max_capacity_bps']
    def shortest_hops(self,target,excluded=()):
        excluded=set(excluded);dist={target:0};queue=[(0,target)]
        while queue:
            cost,node=heapq.heappop(queue)
            if cost!=dist[node]:continue
            for peer,link in self.adjacency[node].items():
                if {node,peer}==excluded or not self.active(node,peer):continue
                value=cost+link['cost']
                if value<dist.get(peer,float('inf')):
                    dist[peer]=value;heapq.heappush(queue,(value,peer))
        return {node:{peer for peer,link in self.adjacency[node].items()
                if {node,peer}!=excluded and self.active(node,peer) and peer in dist and dist.get(node)==link['cost']+dist[peer]}
                for node in self.nodes if node!=target}
    def sr_mismatches(self,tables,targets,excluded=()):
        bad=[]
        for target in targets:
            hops=self.shortest_hops(target,excluded)
            for node,table in tables.items():
                if node==target:continue
                entry=table.get(str(self.sid(target)),{})
                installed=[h for h in entry.get('nexthops',[]) if h.get('installed')]
                allowed={self.hop(node,p):3 if p==target else self.sid(target) for p in hops[node]}
                if not entry.get('installed') or not installed or any(allowed.get(h.get('nexthop'))!=h.get('outLabel') for h in installed):
                    bad.append({'node':node,'target':target,'allowed_nexthops':allowed,'actual':entry})
        return bad
    def service(self):
        return [(self.pair[0],self.pair[1],'172.22.0.8','n3-forward'),
                (self.pair[1],self.pair[0],'172.22.0.23','n3-reverse')]

    def bgp_config(self,node):
        peers=[n for n in self.gateways if n!=node]
        lines=['router bgp 65000',f'bgp router-id {self.loopback(node)}']
        for peer in peers:
            lines += [f'neighbor {self.loopback(peer)} remote-as 65000',f'neighbor {self.loopback(peer)} update-source lo']
        for family in ['link-state link-state','l2vpn evpn']:
            lines += ['address-family '+family]
            lines += [f'neighbor {self.loopback(p)} activate' for p in peers]
            if family=='l2vpn evpn': lines += ['advertise-all-vni','advertise-svi-ip']
            lines += ['exit-address-family']
        return lines+['exit']

    def config(self,node):
        lines=['frr defaults traditional','hostname '+node,'service integrated-vtysh-config','log stdout warnings',
               'interface lo',f'ip address {self.loopback(node)}/32','ip router isis SF','isis passive',
               'ip ospf area 0.0.0.0','ip ospf passive','exit',
               'router ospf',f'ospf router-id {self.loopback(node)}','timers throttle spf 0 50 500','exit',
               'router ospf6',f'ospf6 router-id {self.loopback(node)}','timers throttle spf 0 50 500','exit',
               'router isis SF',f'net 49.0001.{self.system_id(node)}.00','is-type level-2-only','metric-style wide',
               'lsp-gen-interval 1','spf-interval 1','segment-routing on','segment-routing global-block 16000 23999',
               f'segment-routing prefix {self.loopback(node)}/32 index {self.ordinal[node]}',
               'mpls-te on',f'mpls-te router-address {self.loopback(node)}']
        if node==self.pair[0]: lines+=['mpls-te export']
        lines += ['exit']
        for peer,link in self.adjacency[node].items():
            lines += ['interface '+link['interface'],'ip router isis SF','isis network point-to-point',
                      'isis hello-interval 5','isis hello-multiplier 3',f"isis metric level-2 {link['cost']}",
                      'ip ospf area 0.0.0.0','ip ospf network point-to-point','ip ospf hello-interval 5','ip ospf dead-interval 15',
                      f"ip ospf cost {link['cost']}",'ipv6 ospf6 area 0.0.0.0','ipv6 ospf6 network point-to-point',
                      'ipv6 ospf6 hello-interval 5','ipv6 ospf6 dead-interval 15',f"ipv6 ospf6 cost {link['cost']}",
                      'link-params','max-bw '+frr_bandwidth({'capacity_bps':self.port_capacity(node,peer)}),'exit-link-params','exit']
        lines += ['access-list SF-LDP-TRANSPORT permit 10.255.0.0/16','mpls ldp',f'router-id {self.loopback(node)}',
                  'address-family ipv4','label local allocate for SF-LDP-TRANSPORT','label local advertise for SF-LDP-TRANSPORT',
                  f'discovery transport-address {self.loopback(node)}']
        for link in self.adjacency[node].values(): lines+=['interface '+link['interface'],'exit']
        lines += ['exit-address-family','exit','interface sr0',f'ipv6 address {self.locator(node)}ffff/64','ipv6 ospf6 area 0.0.0.0','exit']
        if node in self.gateways: lines += self.bgp_config(node)
        if node==self.pair[0]: lines += ['ip route 172.22.0.23/32 10.230.0.2']
        if node==self.pair[1]: lines += ['ip route 172.22.0.8/32 10.230.0.6']
        return '\n'.join(lines)+'\n'

    def wiring(self,pids,pairs=None):
        jobs=[]
        def add(node,*args): jobs.append({'pid':pids[node],'args':list(args)})
        for (a,b),link in self.links.items():
            if pairs is not None and (a,b) not in pairs:
                continue
            interface=link['interface']; peer='p'+str(link['index'])
            add(a,'ip','link','add',interface,'type','veth','peer','name',peer)
            add(a,'ip','link','set',peer,'netns',str(pids[b]))
            add(b,'ip','link','set',peer,'name',interface)
            for i,node in enumerate((a,b)):
                add(node,'ip','address','add',link['endpoints'][node]+'/30','dev',interface)
                # A service's return path may traverse nodes with no route for
                # its source prefix. all=0 alone leaves inherited loose RPF on.
                add(node,'sysctl','-qw',f'net.ipv4.conf.{interface}.rp_filter=0')
                add(node,'ip','-6','address','add',f"2001:db8:{link['index']:x}::{i+1}/64",'dev',interface,'nodad')
                direction=self.directed[node, b if node==a else a]
                if 'capacity_bps' in direction:
                    add(node,*qdisc_command(interface,direction,self.queue_limit))
                add(node,'ip','link','set',interface,'up' if self.active(node,b if node==a else a) else 'down')
                add(node,'sysctl','-qw',f'net.mpls.conf.{interface}.input=1',f'net.ipv6.conf.{interface}.seg6_enabled=1')
        if pairs is not None:
            return jobs
        for node in self.nodes:
            add(node,'ip','link','set','lo','up')
            add(node,'ip','link','add','sr0','type','dummy'); add(node,'ip','link','set','sr0','up')
            add(node,'ip','-6','route','replace',self.locator(node)+'1/128','encap','seg6local','action','End','dev','sr0')
            add(node,'ip','route','add','blackhole','172.22.0.0/16','metric','42799')
        for node in self.gateways:
            # Service sinks must not be advertised by the loopback's IGP.
            # Their reachability is owned by the controller's intent routes.
            add(node,'ip','link','add','svc0','type','dummy'); add(node,'ip','link','set','svc0','up')
            add(node,'ip','link','add','br100','type','bridge'); add(node,'ip','link','set','br100','up')
            add(node,'ip','address','add',self.svi(node)+'/16','dev','br100')
            add(node,'ip','link','add','vni100','type','vxlan','id','100','local',self.loopback(node),'dstport','4789','nolearning')
            add(node,'ip','link','set','vni100','master','br100'); add(node,'ip','link','set','vni100','up')
        for intent in self.gateway_intents:
            add(intent['destination'],'ip','address','add',self.service_addresses[intent['id']]+'/32','dev','svc0')
        for i,(node,peer,destination,intent) in enumerate(self.service()):
            endpoint='nr_gnb' if i==0 else 'upf'; interface='n3'; base=4*i
            add(node,'ip','link','add',interface,'type','veth','peer','name','n3peer')
            add(node,'ip','link','set','n3peer','netns',str(pids[endpoint]))
            add(endpoint,'ip','link','set','n3peer','name','sf-n3')
            for owner,iface,offset in [(node,interface,1),(endpoint,'sf-n3',2)]:
                add(owner,'ip','address','add',ip('10.230.0.0',base+offset)+'/30','dev',iface)
                add(owner,'sysctl','-qw',f'net.ipv4.conf.{iface}.rp_filter=0')
                add(owner,'ip','link','set',iface,'up')
            add(node,'ip','-6','route','replace',self.locator(node)+'4/128','encap','seg6local','action','End.DX4',
                'nh4',ip('10.230.0.0',base+2),'dev',interface)
            add(node,'ip','tunnel','add','sf-ospf','mode','ipip','local',self.loopback(node),'remote',self.loopback(peer))
            add(node,'ip','link','set','sf-ospf','up')
            add(node,'ip','rule','add','pref','100','to',destination+'/32','lookup','300')
        return jobs
