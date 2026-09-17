#!/usr/bin/env python3
"""Real FRR / controller / TCP+UDP regression in an isolated bounded fabric.

Does not access the running 5G containers, live status files or kind cluster.
All resources carry a unique run label and are removed in finally. Results
are scoped to this small regression, never to the 120-satellite live session.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from lab.live.runtime import entry
from lab.live.model import LiveFabric
from lab.live.workloads import Workloads
from lab.live.workload_snapshot import forwarding_signature
from run import command, save, wait_for, now, FRR, TOOLS, Runtime
from physics_runtime import PhysicalReplay


class Regression(entry.Platform):
    # Hundreds of routes can share a core router in this deliberately small
    # topology. Let one configuration finish before starting any retry.
    frr_operation_timeout = 60
    plan_ttl = 600

    def __init__(self, copies, gateways=2):
        super().__init__(None)
        available = sorted(os.sched_getaffinity(0))
        self.fabric_cpus = ','.join(str(n) for n in (available[1:] or available)[:4])
        self.step = 2
        self.pce_network = self.identifier + '-workload-test'
        self.workloads = None
        nodes = [{'id': n, 'kind': 'gateway' if n.startswith('gw') else 'satellite', 'enabled': True}
                 for n in [f'gw-{i:03d}' for i in range(1, gateways+1)] + [f'sat-{i:04d}' for i in range(1, 7)]]
        pairs = [('gw-001', 'sat-0001', 1000), ('sat-0001', 'sat-0002', 1000), ('sat-0002', 'gw-002', 1000),
                 ('gw-001', 'sat-0003', 2000), ('sat-0003', 'sat-0004', 2000), ('sat-0004', 'gw-002', 2000),
                 ('gw-001', 'sat-0005', 3000), ('sat-0005', 'sat-0006', 3000), ('sat-0006', 'gw-002', 3000)]
        for index in range(3, gateways+1):
            pairs += [(f'gw-{index:03d}', f'sat-{base + (index+1)%2:04d}', delay)
                      for base, delay in ((1,1000),(3,2000),(5,3000))]
        links = [{'id': a + '--' + b, 'source': a, 'target': b, 'latency_us': delay,
                  'capacity_bps': 100000000, 'admin_up': True, 'operational_up': True}
                 for left, right, delay in pairs for a, b in [(left, right), (right, left)]]
        scenario = {'scenario_id': self.identifier, 'topology': {'version': 1, 'generated_at': now(), 'valid_from': now(),
                    'nodes': nodes, 'links': links}, 'intents': [{'id': 'base', 'source': 'gw-001', 'destination': 'gw-002',
                    'policy': 'latency', 'redundancy': 1, 'demand_bps': 1000}], 'timeline': []}
        # Exercise the same compact next-hop bindings and stable /30 pool as
        # the full live constellation, including controller-to-kernel agreement.
        self.fabric = LiveFabric(scenario, self.identifier)
        self.scenario = self.fabric.scenario
        config = json.loads((ROOT/'scenarios/constellations/live-workloads.json').read_text())
        config.update(copies_per_profile=copies, sample_seconds=1, capture_interval_seconds=10)
        self.workloads = Workloads(self, config)
        self.workloads.bind()
        save(self.art/'scenario.json', self.scenario)
        command(str(ROOT/'bin/sfctl'), 'scenario', 'validate', '--check-paths', '--file', str(self.art/'scenario.json'))

    def isolated_network(self):
        command('docker', 'network', 'create', '--label', 'starfabric.run=' + self.identifier, self.pce_network)
        self.networks.append(self.pce_network)
        network = json.loads(command('docker', 'network', 'inspect', self.pce_network).stdout)[0]
        self.cloud = SimpleNamespace(gateway=network['IPAM']['Config'][0]['Gateway'])
        self.pids = {}
        for node in self.fabric.nodes:
            folder = self.art/'routers'/node
            folder.mkdir(parents=True)
            (folder/'frr.conf').write_text(self.fabric.config(node))
            (folder/'daemons').write_text('zebra=yes\nstaticd=yes\nisisd=yes\nospfd=yes\nospf6d=yes\nldpd=yes\nbgpd=yes\nvtysh_enable=yes\n')
            name = self.router(node)
            command('docker', 'run', '-d', '--name', name, '--label', 'starfabric.run=' + self.identifier,
                    '--network=none', '--cpuset-cpus', self.fabric_cpus, '--cap-add=NET_ADMIN', '--cap-add=NET_RAW', '--cap-add=SYS_ADMIN',
                    '--sysctl', 'net.ipv4.ip_forward=1', '--sysctl', 'net.ipv4.conf.all.rp_filter=0',
                    '--sysctl', 'net.ipv4.conf.default.rp_filter=0', '--sysctl', 'net.ipv6.conf.all.forwarding=1',
                    '--sysctl', 'net.ipv6.conf.all.seg6_enabled=1', '--sysctl', 'net.ipv6.conf.default.seg6_enabled=1',
                    '--sysctl', 'net.mpls.platform_labels=32768', '--sysctl', 'net.mpls.conf.lo.input=1',
                    '-v', f'{folder}/frr.conf:/etc/frr/frr.conf:ro', '-v', f'{folder}/daemons:/etc/frr/daemons:ro',
                    '-v', f'{self.frr_config_dir}/vtysh.conf:/etc/frr/vtysh.conf:ro', FRR)
            self.containers.append(name)
            self.pids[node] = json.loads(command('docker', 'inspect', name).stdout)[0]['State']['Pid']
            wait_for(node + ' daemons', lambda: 'isisd' in self.vty(node, 'show daemons'), 60)
        for alias in ('nr_gnb', 'upf'):
            name = self.router('dummy-' + alias)
            command('docker', 'run', '-d', '--name', name, '--label', 'starfabric.run=' + self.identifier,
                    '--network=none', '--cap-add=NET_ADMIN', '--entrypoint', 'sleep', TOOLS, 'infinity')
            self.containers.append(name)
            self.pids[alias] = json.loads(command('docker', 'inspect', name).stdout)[0]['State']['Pid']
        self.workloads.jobs('isolated-wiring', self.fabric.wiring(self.pids))
        for node in self.fabric.nodes:
            wait_for(node + ' OSPF', lambda: self.vty(node, 'show ip ospf neighbor').count('Full') == len(self.fabric.adjacency[node]), 90)
        self.protocols.setup()

    def verify(self, phase, timeout=120):
        deadline = time.monotonic() + timeout
        start = time.time()
        verified = {}
        best = 0
        while time.monotonic() < deadline:
            self.workloads.update(self.current_plan)
            snapshot = self.workloads.snapshot()
            failures = {}
            simultaneous = 0
            for flow in snapshot['flows']:
                proof = flow['evidence']
                signature = forwarding_signature(list(flow['directions'].values()))
                if flow['id'] in verified and verified[flow['id']]['signature'] != signature:
                    del verified[flow['id']]
                good = (flow['fresh'] and flow['evidence_fresh'] and flow['metrics'].get('goodput_bps', 0) > 0
                        and all(d.get('status') == 'ready' for d in flow['directions'].values())
                        and (flow['evidence_at'] or 0) >= start and proof.get('forward', 0) and proof.get('reverse', 0))
                if good:
                    simultaneous += 1
                    verified[flow['id']] = {'signature': signature, 'evidence_at': flow['evidence_at'],
                                            'evidence': proof, 'goodput_bps': flow['metrics']['goodput_bps']}
                if (flow['id'] not in verified or not flow['fresh'] or flow['metrics'].get('goodput_bps', 0) <= 0
                        or not all(d.get('status') == 'ready' for d in flow['directions'].values())):
                    failures[flow['id']] = {'goodput_bps': flow['metrics'].get('goodput_bps'),
                                            'evidence': proof, 'directions': flow['directions']}
            best = max(best, simultaneous)
            if not failures:
                # Short captures sample the shared fabric; hundreds of flows
                # need not all occur in the same capture. Preserve each real
                # proof and its timestamp, without changing the latest snapshot.
                snapshot['verification'] = {'scope': 'per-flow evidence within this phase observation window',
                    'started_at': start, 'finished_at': time.time(), 'best_simultaneously_verified': best,
                    'flows': verified}
                save(self.art/('workloads-' + phase + '.json'), snapshot)
                self.event('workload_phase_passed', phase=phase, streams=len(snapshot['flows']))
                return snapshot
            save(self.art/'workload-pending.json', failures)
            time.sleep(2)
        raise AssertionError('workload ' + phase + ' timed out: ' + ', '.join(failures))

    def close_isolated(self):
        if self.workloads:
            self.workloads.close()
        Runtime.stop_controller(self)
        for name in reversed(self.containers):
            command('docker', 'rm', '-f', name, check=False)
        for name in reversed(self.networks):
            command('docker', 'network', 'rm', name, check=False)

    def change_contact(self, up, sequence):
        links = copy.deepcopy(self.fabric.scenario['topology']['links'])
        pair = {'sat-0001', 'sat-0002'}
        for link in links:
            if {link['source'], link['target']} == pair:
                link['operational_up'] = up
        driver = PhysicalReplay(self)
        driver.channels(links, 'test-' + str(sequence), readback=True)
        driver.publish(links, sequence)
        wait_for('BGP-LS contact change', lambda: all(((a, b) in self.protocols.edges()) == up
                 for a, b in [('sat-0001', 'sat-0002'), ('sat-0002', 'sat-0001')]), 60)
        self.current_plan = self.reconcile('contact-' + ('up' if up else 'down'))['plan']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--copies', type=int, default=4, choices=range(1, 65))
    parser.add_argument('--gateways', type=int, default=2, choices=range(2,33))
    parser.add_argument('--faults', action='store_true', help='also break and restore one owned satellite link')
    args = parser.parse_args()
    runtime = Regression(args.copies, args.gateways)
    print(str(runtime.art), flush=True)
    passed = False
    try:
        runtime.isolated_network()
        runtime.workloads.setup()
        Runtime.start_controller(runtime)
        runtime.current_plan = runtime.reconcile('initial')['plan']
        runtime.workloads.start(runtime.current_plan)
        runtime.verify('initial')
        if args.faults:
            runtime.change_contact(False, 100001)
            runtime.verify('link-down')
            runtime.change_contact(True, 100002)
            runtime.verify('link-restored')
        passed = True
    finally:
        runtime.close_isolated()
        save(runtime.art/'workload-regression.json', {'run_id': runtime.identifier, 'passed': passed,
             'streams': 8 * args.copies, 'gateways': args.gateways, 'faults_requested': args.faults,
             'scope': f'isolated {args.gateways+6}-router FRR regression; no live 120-satellite or 5G fault certification'})
    print(json.dumps({'passed': passed, 'artifacts': str(runtime.art)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
