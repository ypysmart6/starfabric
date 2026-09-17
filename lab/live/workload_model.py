"""Validated workload catalog shared by runtime, traffic workers and the UI.

These are actual socket workloads, not additional UE/PDU sessions. Each pair
owns two /32 endpoints (or /128 for distributed IPv6), so traffic selection
does not change the existing N3 routes. Rates are application payload rates.
"""
from __future__ import annotations

import copy
import ipaddress
import math
import re

CARRIERS = {'ospf', 'ospf6', 'native', 'ldp', 'sr-mpls', 'pcep', 'srv6', 'evpn'}
CONTROLS = {'ospf': 'distributed', 'ospf6': 'distributed', 'native': 'centralized',
            'ldp': 'hybrid', 'sr-mpls': 'hybrid', 'pcep': 'hybrid', 'srv6': 'hybrid', 'evpn': 'hybrid'}


def catalog(config, gateways):
    config = copy.deepcopy(config)
    if config.get('schema_version') != 1 or not isinstance(config.get('enabled'), bool):
        raise ValueError('workload schema_version=1 and boolean enabled are required')
    if not config['enabled']:
        return []
    for name, default, minimum, maximum in [('sample_seconds', 5, 1, 30),
                                           ('capture_seconds', 2, 1, 10),
                                           ('capture_interval_seconds', 30, 10, 300),
                                           ('initial_rate_fraction', 1, .1, 1)]:
        value = config.get(name, default)
        if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f'{name} must be finite and within {minimum}..{maximum}')
    if len(gateways) < 2:
        raise ValueError('workloads require at least two gateways')
    copies = config.get('copies_per_profile', 4)
    if type(copies) is not int or not 1 <= copies <= 64:
        raise ValueError('copies_per_profile must be 1..64')
    pairing = config.get('pairing', 'ring')
    if pairing not in ('ring', 'balanced_mesh'):
        raise ValueError('pairing must be ring or balanced_mesh')
    if len(set(gateways)) != len(gateways):
        raise ValueError('gateway ids must be unique')
    scale = config.get('rate_scale', 1)
    if not isinstance(scale, (int, float)) or isinstance(scale, bool) or not math.isfinite(scale) or not 0.01 <= scale <= 20:
        raise ValueError('rate_scale must be finite and within 0.01..20')
    profiles = config.get('profiles', [])
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= 16:
        raise ValueError('supply 1..16 workload profiles')
    flows, ids = [], set()
    for profile_index, profile in enumerate(profiles):
        identifier = profile.get('id', '')
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,23}', identifier) or identifier in ids:
            raise ValueError('workload profile ids must be unique short names')
        ids.add(identifier)
        carrier = profile.get('carrier')
        if carrier not in CARRIERS or profile.get('control') != CONTROLS[carrier]:
            raise ValueError('carrier/control mismatch for ' + identifier)
        if profile.get('transport') not in ('tcp', 'udp'):
            raise ValueError('transport must be tcp or udp')
        for name, minimum, maximum in [('rate_bps', 1000, 100000000), ('payload_bytes', 96, 1200),
                                       ('dscp', 0, 63), ('priority', 0, 199), ('max_rtt_ms', 1, 60000)]:
            value = profile.get(name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f'{identifier}.{name} must be {minimum}..{maximum}')
        for index in range(copies):
            number = len(flows) + 1
            # Each complete round uses every gateway once as source and once
            # as destination. Subsequent profiles use different offsets, so
            # adding services broadens the mesh instead of repeating one ring.
            offset = 1
            source_index = index
            if pairing == 'balanced_mesh':
                rounds = math.ceil(copies / len(gateways))
                offset += (profile_index * rounds + index // len(gateways)) % (len(gateways) - 1)
                # Partial rounds must rotate their origins between profiles;
                # otherwise a small capacity trial only exercises the first
                # few gateways even though the full global fleet is deployed.
                source_index += profile_index * copies
                if len(profiles) * copies < len(gateways):
                    # With fewer streams than sites, use the remaining sites
                    # as destinations before reusing a gateway endpoint.
                    offset = len(profiles) * copies
            source, target = gateways[source_index % len(gateways)], gateways[(source_index + offset) % len(gateways)]
            ipv6 = carrier == 'ospf6'
            addresses = [str(ipaddress.ip_address('fd42:5354::' if ipv6 else '198.18.0.0') + 2 * number + side)
                         for side in (0, 1)]
            flow = {**profile, 'id': f'{identifier}-{index + 1:02d}', 'number': number,
                    'source': source, 'destination': target, 'source_address': addresses[0],
                    'destination_address': addresses[1], 'family': 6 if ipv6 else 4,
                    'prefix_length': 128 if ipv6 else 32, 'port': 20000 + number,
                    'rate_bps': round(profile['rate_bps'] * scale), 'table': 10000 + number,
                    'binding_sid': 30000 + number, 'pcep_color': 10000 + number}
            flows.append(flow)
    if sum(f['rate_bps'] for f in flows) > 500000000:
        raise ValueError('aggregate offered payload is limited to 500 Mbit/s per SIL run')
    return flows


def directions(flow):
    for reverse in (False, True):
        yield {'id': flow['id'] + ('-reverse' if reverse else '-forward'),
               'node': flow['destination'] if reverse else flow['source'],
               'remote': flow['source'] if reverse else flow['destination'],
               'source': flow['destination_address'] if reverse else flow['source_address'],
               'target': flow['source_address'] if reverse else flow['destination_address']}


def controller_intents(flows):
    result = []
    for flow in flows:
        if flow['control'] == 'distributed':
            continue
        for direction in directions(flow):
            result.append({'id': 'work-' + direction['id'], 'source': direction['node'],
                           'destination': direction['remote'], 'destination_prefix': direction['target'] + '/32',
                           'policy': 'latency', 'redundancy': 1, 'demand_bps': flow['rate_bps'],
                           'priority': flow['priority'], 'class': flow['id'],
                           'labels': {'workload': flow['id'], 'carrier': flow['carrier']}})
    return result


def summary(flows):
    gateways = sorted({n for f in flows for n in (f['source'], f['destination'])})
    return {'streams': len(flows), 'offered_payload_bps': sum(f['rate_bps'] for f in flows),
            'controller_intents': len(controller_intents(flows)),
            'gateways': len(gateways),
            'gateway_pairs': len({tuple(sorted((f['source'], f['destination']))) for f in flows}),
            'gateway_distribution': {node: {
                'originating': sum(f['source'] == node for f in flows),
                'terminating': sum(f['destination'] == node for f in flows),
                'peers': sorted({f['destination'] if f['source'] == node else f['source']
                                 for f in flows if node in (f['source'], f['destination'])})}
                for node in gateways},
            'control_modes': {mode: sum(f['control'] == mode for f in flows) for mode in set(CONTROLS.values())},
            'scope': 'real TCP/UDP application traffic on the satellite fabric; separate from the existing 5G PDU'}
