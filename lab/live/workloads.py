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
        for node in self.f.gateways:
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
        if carrier == 'srv6':
            segments = [self.f.locator(n) + '1' for n in nodes[1:-1]] + [self.f.locator(remote) + 'd']
            if len(segments) > 16:
                raise ValueError('SRv6 workload path exceeds configured 16-segment bound')
            return args + ['encap', 'seg6', 'mode', 'encap', 'segs', ','.join(segments), 'dev', interface]
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
                        if flow['carrier'] in ('sr-mpls', 'pcep', 'srv6'):
                            if not all((a, b) in self.edges() for a, b in zip(nodes, nodes[1:])):
                                raise ValueError(bgp_error or 'path has not converged in live BGP-LS')
                        if flow['carrier'] == 'pcep':
                            if not self.ensure_pcep(flow, d, nodes, self.observation(d['node'], 'show mpls table json')):
                                raise ValueError('waiting for PCRep and installed binding SID matching the path')
                        if flow['carrier'] != 'native':
                            args = self.route(flow, d, nodes)
                            if self.installed.get(key) != args:
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
        self.settle_pcep()
        self.protocol_states = {'bgpls': {'observed_at': self.bgp_observed_at, 'directed_links': len(self.bgp_edges), 'error': bgp_error},
                                'pcep': {'running': self.pce is not None, 'responses': len(self.pce.responses) if self.pce else 0}}
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
