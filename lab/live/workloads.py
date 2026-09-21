"""Concurrent workloads on the same live fabric, with per-service steering.

The controller owns normal IPv4 intent routes. Only the workload endpoint
pairs have policy rules for encapsulated carriers. A failed carrier is
blackholed in its own table, never silently sent over the native route.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import threading
import time
import uuid

from lab.live.storage import atomic
from lab.live.workload_model import catalog, controller_intents, directions, summary
from lab.live.workload_snapshot import observe, forwarding_signature

ROOT = Path(__file__).resolve().parents[2]


class Workloads:
    def __init__(self, runtime, config):
        self.r = runtime
        self.config = config
        self.f = runtime.fabric
        self.flows = catalog(config, self.f.gateways)
        self.folder = runtime.art / 'workloads'
        self.folder.mkdir(exist_ok=True)
        self.token = uuid.uuid4().hex
        self.plan = None
        self.bgp_edges = set()
        self.bgp_observed_at = 0
        self.installed = {}
        self.states = {}
        self.pce = None
        self.last_capture = 0
        self.capture_thread = None
        self.capture_error = None
        self.stopped = threading.Event()
        self.helpers = []
        self.pcep_policy = {}
        self.pcep_requested_at = {}
        self.pce_targets = {d['target']: 'work-' + d['id'] for f in self.flows if f['carrier'] == 'pcep' for d in directions(f)}
        self.protocol_states = {}
        self.observations = {}
        self.evpn_ribs = {}
        self.evpn_l2 = {}
        self.n3_states = {}
        self.n3_evpn = 'evpn-srv6' in (getattr(self.f,'network_profile',None) or {}).get('carriers',[])
        atomic(self.folder / 'catalog.json', {'run_id': runtime.identifier, 'token': self.token,
                                             'flows': self.flows, 'summary': summary(self.flows)})
        atomic(self.folder / 'rates.json', {'run_id': runtime.identifier, 'token': self.token,
                                          'scale': config.get('initial_rate_fraction', 1.0)})

    def bind(self):
        additions = controller_intents(self.flows)
        existing = {i['id'] for i in self.r.scenario['intents']}
        if any(i['id'] in existing for i in additions):
            raise ValueError('workload intent already exists')
        self.r.scenario['intents'].extend(additions)

    def command(self, *args, **kwargs):
        from run import command
        return command(*args, **kwargs)

    def helper(self, script, filename, detached=False, name=None):
        from run import TOOLS
        args = ['docker', 'run', '--rm', '--privileged', '--pid=host', '--network=none']
        if detached:
            args += ['-d', '--name', name]
        if getattr(self.r, 'fabric_cpus', None):
            args += ['--cpuset-cpus', self.r.fabric_cpus]
        args += ['--label', 'starfabric.run=' + self.r.identifier,
                 '-v', f'{self.folder}:/work', '-v', f'{ROOT}/lab/live:/scripts:ro',
                 '--entrypoint', 'python3', TOOLS, '/scripts/' + script, '/work/' + filename]
        return self.command(*args, timeout=60)

    def jobs(self, name, operations):
        if not operations:
            return
        from run import TOOLS
        path = self.folder / (name + '.json')
        atomic(path, operations)
        args = ['docker', 'run', '--rm', '--privileged', '--pid=host', '--network=none',
                '--label', 'starfabric.run=' + self.r.identifier,
                '-v', f'{self.folder}:/work:ro', '-v', f'{ROOT}/lab/platform/wire.py:/wire.py:ro',
                '--entrypoint', 'python3', TOOLS, '/wire.py', '/work/' + path.name]
        self.command(*args, timeout=120)

    def setup(self):
        jobs = []
        def add(node, *args):
            jobs.append({'pid': self.r.pids[node], 'args': list(args)})
        distributed = {d['node'] for f in self.flows if f['control'] == 'distributed' for d in directions(f)}
        for node in distributed:
            add(node, 'ip', 'link', 'add', 'wl-igp', 'type', 'dummy')
            add(node, 'ip', 'link', 'set', 'wl-igp', 'up')
        srv6 = any(f['carrier'] in ('srv6','evpn-srv6') for f in self.flows)
        for node in self.f.gateways if srv6 else ():
            # A zero DX4 next hop looks up the inner destination. These
            # destinations are local service addresses on this gateway.
            add(node, 'ip', '-6', 'route', 'replace', self.f.locator(node) + 'd/128',
                'encap', 'seg6local', 'action', 'End.DX4', 'nh4', '0.0.0.0', 'dev', 'svc0')
        for flow in self.flows:
            for d in directions(flow):
                address = d['source'] + '/' + str(flow['prefix_length'])
                interface = 'wl-igp' if flow['control'] == 'distributed' else 'svc0'
                add(d['node'], 'ip', '-6' if flow['family'] == 6 else '-4', 'address', 'add', address, 'dev', interface)
                if flow['carrier'] not in ('native', 'ospf', 'ospf6'):
                    add(d['node'], 'ip', 'route', 'replace', 'blackhole', 'default', 'table', str(flow['table']), 'metric', '32760')
                    add(d['node'], 'ip', 'rule', 'add', 'pref', str(1000 + flow['number']),
                        'from', d['source'] + '/32', 'to', d['target'] + '/32', 'lookup', str(flow['table']))
        self.jobs('endpoint-setup', jobs)
        for node in distributed:
            self.r.configure(node, 'interface wl-igp', 'ip ospf area 0.0.0.0', 'ip ospf passive',
                             'ipv6 ospf6 area 0.0.0.0', 'ipv6 ospf6 passive')
        self.r.event('workload_endpoints_configured', **summary(self.flows))
        if any(f['carrier'] == 'evpn-srv6' for f in self.flows) or self.n3_evpn:
            self.setup_l2()
            self.setup_evpn_srv6()

    def setup_l2(self):
        """Real customer namespaces, isolated bridges and kernel End.DX2 per EVI."""
        from run import TOOLS
        evis = self.config.get('evpn_l2_evis',[201])
        if not isinstance(evis,list) or not evis or len(set(evis)) != len(evis) or any(type(e) is not int or not 200 <= e <= 4094 for e in evis):
            raise ValueError('evpn_l2_evis must be unique integers 200..4094')
        jobs = []
        for node in self.f.gateways:
            self.evpn_l2[node] = []
            for evi in evis:
                name = self.r.router(f'ce-{evi}-{node}')
                self.command('docker','run','--rm','-d','--name',name,'--network=none',
                    '--label','starfabric.run='+self.r.identifier,'--cap-drop=ALL',
                    '-v',f'{self.folder}:/work','-v',f'{ROOT}/lab/live:/scripts:ro',
                    '--cap-add=NET_ADMIN','--cap-add=NET_RAW','--entrypoint','sleep',TOOLS,'infinity')
                self.helpers.append(name)
                pid = json.loads(self.command('docker','inspect',name).stdout)[0]['State']['Pid']
                def add(owner,*args): jobs.append({'pid':owner,'args':list(args)})
                gw = self.r.pids[node]; bridge=f'evb{evi}'; access=f'eva{evi}'; port=f'evp{evi}'; io=f'evi{evi}'
                add(gw,'ip','link','add',bridge,'type','bridge')
                add(gw,'ip','link','set',bridge,'up')
                add(gw,'ip','link','add',access,'type','veth','peer','name','cepeer')
                add(gw,'ip','link','set','cepeer','netns',str(pid))
                add(pid,'ip','link','set','cepeer','name','eth0')
                add(gw,'ip','link','add',port,'type','veth','peer','name',io)
                for iface in (access,port):
                    add(gw,'ip','link','set',iface,'master',bridge)
                    add(gw,'ip','link','set',iface,'up')
                add(gw,'ip','link','set',io,'up')
                # No provider-generated IPv6 ND/RA may be learned as a CE MAC.
                for iface in (bridge,access,port,io): add(gw,'sysctl','-qw',f'net.ipv6.conf.{iface}.disable_ipv6=1')
                ordinal=self.f.gateways.index(node)+1
                add(pid,'ip','link','set','eth0','address',f'02:53:{evi>>8:02x}:{evi&255:02x}:00:{ordinal:02x}')
                add(pid,'ip','link','set','eth0','mtu','1280','up')
                # Identical prefixes in different EVIs intentionally test isolation.
                ipv4=f'192.0.2.{ordinal}'; ipv6=f'fd42:201::{ordinal:x}'
                add(pid,'ip','address','add',ipv4+'/24','dev','eth0')
                add(pid,'ip','-6','address','add',ipv6+'/64','dev','eth0','nodad')
                sid=self.f.locator(node)+f'{0x2000+evi:x}'
                add(gw,'ip','-6','route','replace',sid+'/128','encap','seg6local','action','End.DX2','oif',io,'dev','sr0')
                self.evpn_l2[node].append({'evi':evi,'interface':io,'bridge':bridge,'sid':sid,
                                         'container':name,'ipv4':ipv4,'ipv6':ipv6})
        self.jobs('evpn-l2-setup',jobs)
        atomic(self.folder/'evpn-l2-catalog.json',{'run_id':self.r.identifier,'gateways':self.evpn_l2})

    def setup_evpn_srv6(self):
        """One actual MP-BGP speaker in each participating gateway namespace."""
        flows = [f for f in self.flows if f['carrier'] == 'evpn-srv6']
        nodes = sorted(set(self.evpn_l2) | {n for f in flows for n in (f['source'], f['destination'])})
        self.evpn_nodes = nodes
        for node in nodes:
            exports, imports, export_sids = set(), {}, {}
            for flow in flows:
                for d in directions(flow):
                    if d['node'] != node: continue
                    exports.add(d['source']+'/32')
                    imports[d['target']+'/32'] = {'peer': self.f.locator(d['remote'])+'ffff',
                        'locator': self.f.locator(d['remote'])+'/64', 'tables': [flow['table']]}
            if self.n3_evpn:
                for src,dst,target,intent in self.f.service():
                    if node == dst:
                        exports.add(target+'/32'); export_sids[target+'/32']=self.f.locator(node)+'4'
                    if node == src:
                        imports[target+'/32']={'peer':self.f.locator(dst)+'ffff',
                                              'locator':self.f.locator(dst)+'/64','tables':[300]}
            filename = 'evpn-'+node+'.json'
            atomic(self.folder/filename, {'run_id':self.r.identifier, 'node':node, 'pid':self.r.pids[node],
                'address':self.f.locator(node)+'ffff', 'router_id':self.f.loopback(node),
                'sid':self.f.locator(node)+'d', 'exports':sorted(exports), 'imports':imports,
                'export_sids':export_sids, 'l2':self.evpn_l2.get(node,[]),
                'peers':[self.f.locator(n)+'ffff' for n in nodes if n != node],
                'output':'/work/evpn-'+node+'-rib.json'})
            name = self.r.router('evpn-'+node)
            self.helper('evpn_speaker.py', filename, detached=True, name=name)
            self.helpers.append(name)
        self.r.event('evpn_srv6_speakers_started', gateways=nodes, route_types=[2,3,5], tcp_port=1179)

    def update_n3(self, plan):
        if not self.n3_evpn: return
        operations=[]
        for node,remote,target,intent in self.f.service():
            state={'carrier':'evpn-srv6','status':'pending','observed_at':time.time()}
            try:
                nodes=plan['paths'][intent][0]['nodes']
                if not all(self.f.active(a,b) for a,b in zip(nodes,nodes[1:])): raise ValueError('inactive N3 contact')
                route=self.evpn_ribs.get(node,{}).get('routes',{}).get(target+'/32')
                if not route or route['peer'] != self.f.locator(remote)+'ffff': raise ValueError('N3 EVPN route absent')
                segments=[self.f.locator(n)+'1' for n in nodes[1:-1]]+[route['sid']]
                if len(segments)>16: raise ValueError('N3 SRH exceeds 16 segments')
                args=['ip','route','replace',target+'/32','table','300','encap','seg6','mode','encap',
                      'segs',','.join(segments),'dev','sr0']
                state.update(status='ready',nodes=nodes,service_sid=route['sid'],steering=args)
            except (ValueError,KeyError,TypeError,IndexError) as error:
                state['error']=str(error)
                args=['ip','route','replace','unreachable',target+'/32','table','300']
            operations.append({'pid':self.r.pids[node],'args':args})
            self.n3_states[intent]=state
        self.jobs('evpn-n3-steering',operations)

    def read_evpn_ribs(self):
        self.evpn_ribs = {}
        for node in getattr(self, 'evpn_nodes', []):
            try:
                rib = json.loads((self.folder/('evpn-'+node+'-rib.json')).read_text())
                if rib.get('run_id') == self.r.identifier and 0 <= time.time()-rib['observed_at'] < 15 and not rib.get('stopped'):
                    self.evpn_ribs[node] = rib
            except (OSError, ValueError, KeyError):
                pass

    def resolve_pcep_target(self, target):
        return self.pce_targets.get(target)

    def path(self, intent):
        if self.plan is None:
            raise ValueError('no committed workload plan')
        return self.plan, self.plan['paths'][intent][0]['nodes']

    def edges(self):
        return self.bgp_edges if time.time() - self.bgp_observed_at < max(30, 3 * getattr(self.r, 'step', 10)) else set()

    def start_pce(self):
        from run import TOOLS, wait_for
        from pce import PCE
        host = self.r.cloud.gateway
        port = getattr(self.r, 'workload_pce_port', 4189)
        self.pce = PCE(self, host, port=port, artifact_dir=self.folder)
        relay = self.r.router('workload-pce')
        self.command('docker', 'run', '-d', '--name', relay, '--label', 'starfabric.run=' + self.r.identifier,
                     '--network', getattr(self.r, 'pce_network', 'kind'), '--cap-add=NET_ADMIN',
                     '--sysctl', 'net.ipv4.ip_forward=1', '--entrypoint', 'sleep', TOOLS, 'infinity')
        self.r.containers.append(relay)
        self.r.exec(relay, 'iptables', '-t', 'nat', '-A', 'POSTROUTING', '-s', '10.234.0.0/16', '-o', 'eth0', '-j', 'MASQUERADE')
        pid = json.loads(self.command('docker', 'inspect', relay).stdout)[0]['State']['Pid']
        nodes = sorted({d['node'] for f in self.flows if f['carrier'] == 'pcep' for d in directions(f)})
        jobs = []
        for i, node in enumerate(nodes):
            left, right = f'wlp{i}', f'wlr{i}'
            def add(owner, *args):
                jobs.append({'pid': owner, 'args': list(args)})
            add(pid, 'ip', 'link', 'add', left, 'type', 'veth', 'peer', 'name', right)
            add(pid, 'ip', 'link', 'set', right, 'netns', str(self.r.pids[node]))
            add(self.r.pids[node], 'ip', 'link', 'set', right, 'name', 'wl-pce')
            for owner, iface, last in ((pid, left, 1), (self.r.pids[node], 'wl-pce', 2)):
                add(owner, 'ip', 'address', 'add', f'10.234.{i}.{last}/30', 'dev', iface)
                add(owner, 'ip', 'link', 'set', iface, 'up')
            add(self.r.pids[node], 'ip', 'route', 'replace', host + '/32', 'via', f'10.234.{i}.1', 'dev', 'wl-pce')
        self.jobs('pce-management', jobs)
        for i, node in enumerate(nodes):
            if 'pathd' not in self.r.vty(node, 'show daemons'):
                self.command('docker', 'exec', '-d', self.r.router(node), '/usr/lib/frr/pathd', '-F', 'traditional',
                             '-M', 'pathd_pcep', '--log', 'file:/tmp/workload-pathd.log')
                wait_for(node + ' workload pathd', lambda: 'pathd' in self.r.vty(node, 'show daemons'), 30)
            self.r.configure(node, 'segment-routing', 'traffic-eng', 'pcep', 'pce-config WORKLOAD-MGMT',
                             f'source-address ip 10.234.{i}.2', 'exit', 'pce WORKLOAD', 'config WORKLOAD-MGMT',
                             f'address ip {host}' + (f' port {port}' if port != 4189 else ''), 'exit',
                             'pcc', 'msd 32', 'peer WORKLOAD precedence 20')
            wait_for(node + ' workload PCEP session', lambda: 'UP' in self.r.vty(node, 'show sr-te pcep session json'), 60)

    def start(self, plan):
        self.plan = plan
        self.bgp_edges = self.r.protocols.edges()
        self.bgp_observed_at = time.time()
        if any(f['carrier'] == 'pcep' for f in self.flows):
            self.start_pce()
        self.update(plan)
        self.start_l2()
        self.start_traffic()

    def start_l2(self):
        for node, services in self.evpn_l2.items():
            peer=self.f.gateways[(self.f.gateways.index(node)+1)%len(self.f.gateways)]
            for service in services:
                remote=next(s for s in self.evpn_l2[peer] if s['evi']==service['evi'])
                filename=f"l2-probe-{service['evi']}-{node}.json"
                atomic(self.folder/filename,{'run_id':self.r.identifier,'node':node,'evi':service['evi'],'token':self.token,
                    'ipv4':remote['ipv4'],'ipv6':remote['ipv6'],'output':'/work/'+filename.replace('probe','metrics')})
                self.command('docker','exec','-d','--user',f'{os.getuid()}:{os.getgid()}',
                             service['container'],'python3','/scripts/l2_probe.py','/work/'+filename)
        # Detached exec succeeding only means the process was created. Require
        # a fresh heartbeat to catch permission and startup failures immediately.
        from run import wait_for
        for node,services in self.evpn_l2.items():
            for service in services:
                def ready():
                    try:
                        value=json.loads((self.folder/f"l2-metrics-{service['evi']}-{node}.json").read_text())
                        return (value.get('run_id')==self.r.identifier and time.time()-value['observed_at']<10
                                and set(value.get('families',{}))=={'4','6'}
                                and not any(v.get('error') for v in value['families'].values()))
                    except (OSError,ValueError,KeyError): return False
                wait_for(f"{node} EVI {service['evi']} probe heartbeat",ready,15)

    def start_traffic(self):
        from run import TOOLS
        for node in sorted({d['node'] for f in self.flows for d in directions(f)}):
            filename = 'traffic-' + node + '.json'
            atomic(self.folder / filename, {'run_id': self.r.identifier, 'token': self.token,
                                           'node': node, 'flows': self.flows, 'sample_seconds': self.config.get('sample_seconds', 5),
                                           'rate_file': '/work/rates.json',
                                           'output': '/work/metrics-' + node + '.json'})
            name = self.r.router(node) + '-workloads'
            args = ['docker', 'run', '-d', '--name', name, '--label', 'starfabric.run=' + self.r.identifier,
                    *(['--cpuset-cpus', self.r.fabric_cpus] if getattr(self.r, 'fabric_cpus', None) else []),
                    '--network', 'container:' + self.r.router(node), '--cap-drop=ALL',
                    '--user', f'{os.getuid()}:{os.getgid()}',
                    '--log-opt', 'max-size=1m', '--log-opt', 'max-file=2',
                    '-v', f'{self.folder}:/work', '-v', f'{ROOT}/lab/live:/scripts:ro',
                    '--entrypoint', 'python3', TOOLS, '/scripts/workload_traffic.py', '/work/' + filename]
            self.command(*args)
            self.r.containers.append(name)
            self.helpers.append(name)
        self.r.event('workloads_started', **summary(self.flows))

    def route(self, flow, d, nodes):
        node, remote = d['node'], d['remote']
        args = ['ip', 'route', 'replace', 'table', str(flow['table']), d['target'] + '/32']
        first = nodes[1]
        interface, hop = self.f.interface(node, first), self.f.hop(node, first)
        carrier = flow['carrier']
        if carrier == 'evpn':
            return args + ['via', self.f.svi(remote), 'dev', 'br100']
        if carrier in ('srv6', 'evpn-srv6'):
            sid = self.f.locator(remote) + 'd'
            if carrier == 'evpn-srv6':
                route = self.evpn_ribs.get(node, {}).get('routes', {}).get(d['target']+'/32')
                if not route or route.get('peer') != self.f.locator(remote)+'ffff':
                    raise ValueError('EVPN Type-5 route with live SRv6 Service SID not received')
                sid = route['sid']
            segments = [self.f.locator(n) + '1' for n in nodes[1:-1]] + [sid]
            if len(segments) > 16:
                raise ValueError('SRv6 workload path exceeds configured 16-segment bound')
            # A node SID is reachable through the IPv6 IGP. Binding the LWT
            # route to a feeder interface makes a link-down kill the service
            # before the outer lookup can use the surviving uplink.
            return args + ['encap', 'seg6', 'mode', 'encap', 'segs', ','.join(segments), 'dev', 'sr0']
        if carrier == 'pcep':
            return args + ['encap', 'mpls', str(flow['binding_sid']), 'dev', 'lo']
        if carrier == 'ldp':
            text = self.observation(node, 'show mpls ldp ipv4 binding')
            label = next((line.split()[4] for line in text.splitlines() if len(line.split()) >= 5
                          and line.split()[0] == 'ipv4' and line.split()[1] == self.f.loopback(remote) + '/32'
                          and line.split()[2] == self.f.loopback(first)
                          and line.split()[4].isdigit()), None)
            if label is None:
                raise ValueError('LDP label for the next hop is not ready')
            labels = label
        else:
            labels = '/'.join(str(self.f.sid(n)) for n in nodes[2:])
            if not labels or len(nodes) > 32:
                raise ValueError('SR-MPLS workload path has unsupported label depth')
        return args + ['encap', 'mpls', labels, 'via', 'inet', hop, 'dev', interface]

    def pcep_installed(self, flow, d, nodes, lfib):
        expected = [self.f.sid(n) for n in nodes[2:]]
        entry = lfib.get(str(flow['binding_sid']), {})
        response = next((v for v in reversed(self.pce.responses) if v['intent'] == 'work-' + d['id'] and v['nodes'] == nodes), None)
        return bool(response and entry.get('installed') and any(n.get('installed') and n.get('type') == 'SR-TE'
                         and n.get('outLabelStack') == expected
                         and n.get('nexthop') == self.f.hop(d['node'], nodes[1])
                         and n.get('interface') == self.f.interface(d['node'], nodes[1])
                         for n in entry.get('nexthops', [])))

    def ensure_pcep(self, flow, d, nodes, lfib):
        key = d['id']
        signature = tuple(nodes)
        installed = self.pcep_installed(flow, d, nodes, lfib)
        # An initial negative PCRep during IGP convergence must be retried
        # even if the committed path itself has not changed.
        if self.pcep_policy.get(key) != signature or (not installed and time.monotonic() - self.pcep_requested_at.get(key, 0) > 20):
            prefix = ['segment-routing', 'traffic-eng']
            policy = f'policy color {flow["pcep_color"]} endpoint {d["target"]}'
            self.r.configure(d['node'], *prefix, 'no ' + policy)
            self.r.configure(d['node'], *prefix, policy, 'name WORK-' + flow['id'],
                             'binding-sid ' + str(flow['binding_sid']),
                             'candidate-path preference 200 name CONTROLLER dynamic')
            self.pcep_policy[key] = signature
            self.pcep_requested_at[key] = time.monotonic()
            return False
        return installed

    def settle_pcep(self, timeout=8):
        # PCRep and zebra's BSID installation arrive after the request. Check
        # them in this held frame, rather than blackholing until another full
        # orbital/controller update. Never accept a different label stack.
        pending = [(flow, d) for flow in self.flows if flow['carrier'] == 'pcep'
                   for d in directions(flow)
                   if self.states[d['id']].get('error') ==
                   'waiting for PCRep and installed binding SID matching the path']
        deadline = time.monotonic() + timeout
        def read(node):
            try:
                return node, json.loads(self.r.vty(node, 'show mpls table json'))
            except (ValueError, RuntimeError, OSError):
                return node, {}
        while pending and time.monotonic() < deadline and not self.stopped.wait(.5):
            with ThreadPoolExecutor(max_workers=8) as pool:
                tables = dict(pool.map(read, sorted({d['node'] for _, d in pending})))
            ready, operations = [], []
            for flow, d in pending:
                state = self.states[d['id']]
                if self.pcep_installed(flow, d, state['nodes'], tables[d['node']]):
                    args = self.route(flow, d, state['nodes'])
                    ready.append((flow, d, args))
                    operations.append({'pid':self.r.pids[d['node']], 'args':args})
            if operations:
                self.apply_steering(operations, [(d['id'],args) for _,d,args in ready])
                for flow, d, args in ready:
                    self.states[d['id']].update(status='ready', observed_at=time.time(), steering=args)
                    self.states[d['id']].pop('error', None)
                    pending.remove((flow,d))

    def read_observations(self, plan):
        queries = set()
        for flow in self.flows:
            for d in directions(flow):
                if flow['control'] == 'distributed':
                    queries.add((d['node'], 'show ipv6 route ospf6 json' if flow['family'] == 6 else 'show ip route ospf json'))
                elif plan and 'work-' + d['id'] in plan['paths']:
                    if flow['carrier'] == 'ldp':
                        queries.add((d['node'], 'show mpls ldp ipv4 binding'))
                    elif flow['carrier'] == 'pcep':
                        queries.add((d['node'], 'show mpls table json'))
        def read(query):
            try:
                text = self.r.vty(*query)
                return query, json.loads(text) if query[1].endswith(' json') else text
            except Exception as error:
                return query, RuntimeError(str(error))
        # Each table is collected once per gateway and tick, not once per flow.
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.observations = dict(pool.map(read, sorted(queries)))

    def observation(self, node, query):
        value = self.observations[node, query]
        if isinstance(value, Exception):
            raise value
        return value

    def update(self, plan):
        self.plan = plan
        self.read_evpn_ribs()
        self.read_observations(plan)
        try:
            self.bgp_edges = self.r.protocols.edges()
            self.bgp_observed_at = time.time()
            bgp_error = None
        except (ValueError, RuntimeError, OSError) as error:
            self.bgp_edges = set()
            bgp_error = str(error)
        operations, applied = [], []
        for flow in self.flows:
            for d in directions(flow):
                key = d['id']
                state = {'carrier': flow['carrier'], 'control': flow['control'], 'observed_at': time.time(), 'status': 'pending'}
                try:
                    if flow['control'] == 'distributed':
                        query = 'show ipv6 route ospf6 json' if flow['family'] == 6 else 'show ip route ospf json'
                        routes = self.observation(d['node'], query)
                        entries = routes.get(d['target'] + '/' + str(flow['prefix_length']), [])
                        family = 'ospf6' if flow['family'] == 6 else 'ospf'
                        if not any(entry.get('protocol') == family and (entry.get('installed') or entry.get('selected')) for entry in entries):
                            raise ValueError(family + ' service route is not installed')
                        state.update(status='ready', route_source=family, routes=entries)
                    else:
                        if not plan or 'work-' + key not in plan['paths']:
                            raise ValueError('controller has no committed service path')
                        paths = plan['paths']['work-' + key]
                        nodes = paths[0]['nodes']
                        state.update(plan_id=plan['id'], nodes=nodes, backup_nodes=paths[1]['nodes'] if len(paths) > 1 else [])
                        if not all(self.f.active(a, b) for a, b in zip(nodes, nodes[1:])):
                            raise ValueError('committed path contains an inactive contact')
                        if flow['carrier'] in ('sr-mpls', 'pcep'):
                            if not all((a, b) in self.edges() for a, b in zip(nodes, nodes[1:])):
                                raise ValueError(bgp_error or 'path has not converged in live BGP-LS')
                        if flow['carrier'] == 'pcep':
                            if not self.ensure_pcep(flow, d, nodes, self.observation(d['node'], 'show mpls table json')):
                                raise ValueError('waiting for PCRep and installed binding SID matching the path')
                        if flow['carrier'] != 'native':
                            args = self.route(flow, d, nodes)
                            if self.installed.get(key) != args or flow['carrier'] == 'evpn-srv6':
                                operations.append({'pid': self.r.pids[d['node']], 'args': args})
                                applied.append((key, args))
                            state['steering'] = args
                        state['status'] = 'ready'
                except (ValueError, RuntimeError, OSError, KeyError, IndexError) as error:
                    state.update(status='pending', error=str(error)[-300:])
                    if key in self.installed:
                        # Replacing the exact workload destination with an
                        # unreachable route removes stale encapsulation without
                        # opening a fallback to the controller's native route.
                        args = ['ip', 'route', 'replace', 'unreachable', d['target'] + '/32', 'table', str(flow['table'])]
                        operations.append({'pid': self.r.pids[d['node']], 'args': args})
                        applied.append((key, None))
                self.states[key] = state
        self.apply_steering(operations, applied)
        self.update_n3(plan)
        self.settle_pcep()
        self.protocol_states = {'bgpls': {'observed_at': self.bgp_observed_at, 'directed_links': len(self.bgp_edges), 'error': bgp_error},
                                'pcep': {'running': self.pce is not None, 'responses': len(self.pce.responses) if self.pce else 0}}
        if getattr(self, 'evpn_nodes', []):
            self.protocol_states['evpn-srv6'] = {'route_types':[2,3,5], 'gateways':self.evpn_ribs,
                'n3':self.n3_states, 'l2':self.evpn_l2,
                'missing_gateways': [n for n in self.evpn_nodes if n not in self.evpn_ribs]}
        self.maybe_capture()

    def apply_steering(self, operations, applied):
        try:
            self.jobs('steering-latest', operations)
        except (ValueError, RuntimeError, OSError) as error:
            for key, _ in applied:
                self.states[key].update(status='error', error='steering write failed: ' + str(error)[-250:])
            raise
        else:
            for key, args in applied:
                if args is None:
                    self.installed.pop(key, None)
                else:
                    self.installed[key] = args

    def maybe_capture(self):
        if self.stopped.is_set() or (self.capture_thread and self.capture_thread.is_alive()) or time.monotonic() - self.last_capture < self.config.get('capture_interval_seconds', 30):
            return
        self.last_capture = time.monotonic()
        def run():
            try:
                atomic(self.folder / 'capture-request.json', {'run_id': self.r.identifier, 'token': self.token,
                       'plan_id': self.plan.get('id') if self.plan else None,
                       'signatures': {f['id']: self.proof_signature(f) for f in self.flows},
                       'nodes': {n: self.r.pids[n] for n in self.f.satellites}, 'flows': self.flows,
                       'seconds': self.config.get('capture_seconds', 2), 'pcap': '/work/capture-latest.pcap', 'output': '/work/capture-latest.json'})
                self.helper('workload_capture.py', 'capture-request.json')
                self.capture_error = None
            except (ValueError, RuntimeError, OSError) as error:
                self.capture_error = str(error)[-500:]
        self.capture_thread = threading.Thread(target=run, daemon=True)
        self.capture_thread.start()

    def proof_signature(self, flow):
        states = [self.states.get(d['id'], {}) for d in directions(flow)]
        return forwarding_signature(states)

    def snapshot(self):
        records = [{**f, 'directions': {d['id']:self.states.get(d['id'],{}) for d in directions(f)},
                    'fault_matrix':'not_run'} for f in self.flows]
        value = {'run_id': self.r.identifier, 'enabled': bool(self.flows),
                 'summary': summary(self.flows), 'flows': records, 'protocols': self.protocol_states,
                 'sampling': {key:self.config.get(key, default) for key,default in [('sample_seconds',5),('capture_interval_seconds',30)]},
                 'capture_error': self.capture_error, 'evidence_file': str((self.folder / 'capture-latest.pcap').relative_to(ROOT))}
        value = observe(value, self.folder)
        value['l2_probes']={}
        for path in self.folder.glob('l2-metrics-*.json'):
            try:
                probe=json.loads(path.read_text())
                if probe.get('run_id')==self.r.identifier:
                    probe['fresh']=0 <= time.time()-probe['observed_at'] < 10
                    value['l2_probes'][f"{probe['evi']}:{probe['node']}"]=probe
            except (OSError,ValueError,KeyError): pass
        atomic(self.folder / 'snapshot.json', value)
        return value

    def close(self):
        self.stopped.set()
        if self.capture_thread:
            self.capture_thread.join(timeout=65)
        if self.pce:
            self.pce.close()
        for name in self.helpers:
            self.command('docker', 'stop', '-t', '3', name, check=False)
