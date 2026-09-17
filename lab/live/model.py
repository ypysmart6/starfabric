"""Streaming orbit propagation and stable dynamic fabric addressing.

No archived frames or finite-horizon compilation are used by the live clock.
"""
from __future__ import annotations

import copy
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'lab/platform'))
from fabric import Fabric, ip
from tools.physical_constellation import Orbits, Contacts


class OrbitClock:
    def __init__(self, nodes, config):
        self.config = copy.deepcopy(config)
        self.nodes = [n for n in nodes if n['kind'] == 'satellite']
        self.orbits = Orbits(self.nodes, self.config)
        self.contacts = Contacts(self.nodes, self.config)
        self.last_offset = None

    def sample(self, at):
        offset = (at - self.orbits.epoch).total_seconds()
        if self.last_offset is not None and offset <= self.last_offset:
            raise ValueError('live model time must increase')
        if self.last_offset is None:
            warmup = max(self.config['step_seconds'], self.config['links']['acquisition_seconds'])
            start = offset - warmup
            self.contacts.frame(self.orbits.states(start), start)
        else:
            # Keep acquisition and terminal-selection history on the model's
            # sampling clock even if device programming skipped wall-clock
            # intervals. Only the final frame is applied to the packet fabric;
            # these internal samples never claim packets traversed old frames.
            cursor = self.last_offset + self.config['step_seconds']
            while cursor < offset - 1e-8:
                self.contacts.frame(self.orbits.states(cursor), cursor)
                cursor += self.config['step_seconds']
        frame = self.contacts.frame(self.orbits.states(offset), offset)
        self.last_offset = offset
        return frame


def pair_index(a, b, ids):
    """A pair keeps the same /30 and interface name after retirement/recreation."""
    i, j = sorted((ids.index(a), ids.index(b)))
    if i == j:
        raise ValueError('self link')
    return i * (2 * len(ids) - i - 1) // 2 + j - i


class LiveFabric(Fabric):
    def __init__(self, scenario, identifier):
        super().__init__(scenario, identifier)
        self.ids = sorted(self.nodes)
        if len(self.ids) > 255:
            raise ValueError('live address pool currently supports at most 255 nodes')
        for pair in list(self.links):
            value = self.binding(*pair)
            self.links[pair] = value
            a, b = pair
            self.adjacency[a][b] = self.adjacency[b][a] = value
        # The controller derives the same stable /30 endpoints from these
        # indices. An all-pairs label map exceeds Kubernetes' ConfigMap budget
        # once hundreds of globally distributed services are included.
        for index, name in enumerate(self.ids):
            node = self.nodes[name]
            node['labels'] = {k:v for k,v in node['labels'].items() if not k.startswith('next_hop:')}
            node['labels'].update(next_hop_scheme='live-pair-v1', next_hop_index=str(index),
                                  next_hop_nodes=str(len(self.ids)))
        self.last_present = {}

    def config(self, node):
        # Coalesce fleet-wide contact changes before SPF. The small fault
        # fixture's 50 ms hold time creates repeated full-fleet recalculation
        # on a shared host with 144 routing namespaces.
        return super().config(node).replace('timers throttle spf 0 50 500',
            'timers throttle spf 2000 5000 15000').replace('spf-interval 1\n', 'spf-interval 5\n')

    def bgp_config(self, node):
        lines = super().bgp_config(node)
        result = []
        family = None
        for line in lines:
            if line.startswith('address-family '):
                family = line[len('address-family '):]
            elif line == 'exit-address-family':
                family = None
            # The live controller reads BGP-LS at its one collector. EVPN
            # retains the complete gateway mesh, without replicating the
            # entire satellite TED into every gateway's BGP-LS RIB.
            if family == 'link-state link-state' and line.endswith(' activate'):
                peer = self.pair[1] if node == self.pair[0] else self.pair[0]
                if node not in self.pair or line != f'neighbor {self.loopback(peer)} activate':
                    continue
            result.append(line)
            if line.endswith(' update-source lo'):
                result.append(' '.join(line.split()[:2]) + ' timers connect 5')
        return result

    def binding(self, a, b):
        pair = tuple(sorted((a, b)))
        k = pair_index(a, b, self.ids)
        return {'index': k, 'interface': f'sf{k}', 'cost': 1,
                'endpoints': {node: ip('10.128.0.0', 4*k+i+1) for i, node in enumerate(pair)}}

    def register(self, frame, tick):
        """Add new pairs in DOWN state before any packet can use them."""
        added = set()
        for link in frame['links']:
            a, b = link['source'], link['target']
            pair = tuple(sorted((a, b)))
            self.last_present[pair] = tick
            if pair not in self.links:
                value = self.binding(a, b)
                self.links[pair] = value
                self.adjacency[a][b] = self.adjacency[b][a] = value
                added.add(pair)
            if (a, b) not in self.directed:
                self.directed[a, b] = dict(link, operational_up=False, acquisition_state='unavailable')
        self.scenario['topology']['links'] = list(self.directed.values())
        return added

    def desired(self, frame):
        values = {v['id']: v for v in frame['links']}
        return [copy.deepcopy(values.get(v['id'], dict(v, operational_up=False, acquisition_state='unavailable')))
                for v in self.directed.values()]

    def expired(self, tick, retention=120):
        return [pair for pair in self.links if tick - self.last_present.get(pair, tick) > retention
                and not any(self.directed[p]['operational_up'] for p in (pair, pair[::-1]))]

    def forget(self, pairs):
        for a, b in pairs:
            self.links.pop((a, b))
            self.adjacency[a].pop(b)
            self.adjacency[b].pop(a)
            self.directed.pop((a, b))
            self.directed.pop((b, a))
            self.last_present.pop((a, b), None)
        self.scenario['topology']['links'] = list(self.directed.values())

    def interface_commands(self, a, b):
        link = self.links[tuple(sorted((a, b)))]
        interface = link['interface']
        return ['interface '+interface, 'ip router isis SF', 'isis network point-to-point',
            'isis hello-interval 5', 'isis hello-multiplier 3', 'isis metric level-2 1',
            'ip ospf area 0.0.0.0', 'ip ospf network point-to-point',
            'ip ospf hello-interval 5', 'ip ospf dead-interval 15', 'ip ospf cost 1',
            'ipv6 ospf6 area 0.0.0.0', 'ipv6 ospf6 network point-to-point',
            'ipv6 ospf6 hello-interval 5', 'ipv6 ospf6 dead-interval 15', 'ipv6 ospf6 cost 1',
            'link-params', 'max-bw '+str(self.port_capacity(a, b)/8), 'exit-link-params', 'exit',
            'mpls ldp', 'address-family ipv4', 'interface '+interface, 'exit', 'exit-address-family', 'exit']
