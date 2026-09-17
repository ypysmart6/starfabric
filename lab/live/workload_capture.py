#!/usr/bin/env python3
"""Bounded same-packet evidence from the run's actual satellite namespaces.

A single privileged helper opens AF_PACKET sockets after setns, then restores
its original namespace. No interfaces, routes or protocol settings are changed.
Only frames containing this run's workload token are recorded in the PCAP.
"""
from __future__ import annotations

import argparse
import ctypes
import ipaddress
import json
import os
from pathlib import Path
import select
import socket
import struct
import time

try:
    from .workload_traffic import identify
except ImportError:
    from workload_traffic import identify


def decode(data, protocol=0x6558, layers=(), labels=(), depth=0):
    if depth > 12:
        return None
    if protocol == 0x6558:
        if len(data) < 14:
            return None
        return decode(data[14:], int.from_bytes(data[12:14], 'big'), layers, labels, depth + 1)
    if protocol in (0x8100, 0x88a8):
        if len(data) < 4:
            return None
        return decode(data[4:], int.from_bytes(data[2:4], 'big'), layers, labels, depth + 1)
    if protocol in (0x8847, 0x8848):
        stack = []
        while len(data) >= 4:
            label = int.from_bytes(data[:4], 'big')
            stack.append(label >> 12)
            data = data[4:]
            if label & 0x100:
                break
        else:
            return None
        if not data:
            return None
        return decode(data, 0x86dd if data[0] >> 4 == 6 else 0x0800, layers + ('mpls',), labels + tuple(stack), depth + 1)
    if protocol == 0x0800:
        if len(data) < 20 or data[0] >> 4 != 4:
            return None
        size, total = (data[0] & 15) * 4, int.from_bytes(data[2:4], 'big')
        if size < 20 or total < size or len(data) < total or int.from_bytes(data[6:8], 'big') & 0x1fff:
            return None
        source, target = str(ipaddress.ip_address(data[12:16])), str(ipaddress.ip_address(data[16:20]))
        next_header, payload = data[9], data[size:total]
    elif protocol == 0x86dd:
        if len(data) < 40 or data[0] >> 4 != 6:
            return None
        total = 40 + int.from_bytes(data[4:6], 'big')
        if len(data) < total:
            return None
        source, target = str(ipaddress.ip_address(data[8:24])), str(ipaddress.ip_address(data[24:40]))
        next_header, payload = data[6], data[40:total]
        for _ in range(8):
            if next_header not in (0, 43, 60):
                break
            if len(payload) < 8:
                return None
            size = (payload[1] + 1) * 8
            if len(payload) < size:
                return None
            if next_header == 43 and payload[2] == 4:
                layers += ('srh',)
            next_header, payload = payload[0], payload[size:]
    else:
        return None
    if next_header in (4, 41):
        return decode(payload, 0x0800 if next_header == 4 else 0x86dd,
                      layers + (('ipip',) if protocol == 0x0800 else ()), labels, depth + 1)
    if next_header == 17:
        if len(payload) < 8:
            return None
        srcport, dstport, length = struct.unpack('!HHH', payload[:6])
        if length < 8 or len(payload) < length:
            return None
        payload = payload[8:length]
        if dstport == 4789 and len(payload) >= 8 and payload[0] & 8:
            return decode(payload[8:], 0x6558, layers + ('vxlan',), labels, depth + 1)
    elif next_header == 6:
        if len(payload) < 20:
            return None
        srcport, dstport = struct.unpack('!HH', payload[:4])
        size = (payload[12] >> 4) * 4
        if size < 20 or len(payload) < size:
            return None
        payload = payload[size:]
    else:
        return None
    return {'source': source, 'destination': target, 'source_port': srcport, 'destination_port': dstport,
            'family': 6 if protocol == 0x86dd else 4, 'transport': 'udp' if next_header == 17 else 'tcp',
            'layers': list(layers), 'labels': list(labels), 'payload': payload}


def matches(carrier, packet):
    layers = packet['layers']
    if carrier in ('ldp', 'sr-mpls', 'pcep'):
        if 'mpls' not in layers:
            return False
        if carrier in ('sr-mpls', 'pcep'):
            return any(16000 <= label < 24000 for label in packet['labels'])
        return any(15 < label < 16000 for label in packet['labels'])
    if carrier == 'srv6':
        return 'srh' in layers
    if carrier == 'evpn':
        return 'vxlan' in layers
    return not layers and packet['family'] == (6 if carrier == 'ospf6' else 4)


def capture(config):
    token = bytes.fromhex(config['token'])
    flows = {f['number']: f for f in config['flows']}
    original = os.open('/proc/self/ns/net', os.O_RDONLY)
    libc = ctypes.CDLL(None, use_errno=True)
    sockets, observations, errors = {}, {}, {}
    def enter(fd):
        if libc.setns(fd, 0x40000000):
            raise OSError(ctypes.get_errno(), 'setns failed')
    output = Path(config['pcap'])
    started = time.time()
    written = 0
    try:
        for node, pid in config['nodes'].items():
            try:
                fd = os.open(f'/proc/{int(pid)}/ns/net', os.O_RDONLY)
                try:
                    enter(fd)
                    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
                    # Exclude egress in the kernel where supported. Global
                    # catalogs otherwise spend half the capture budget reading
                    # duplicates that are immediately discarded below.
                    try:
                        sock.setsockopt(263, 23, 1)  # SOL_PACKET / PACKET_IGNORE_OUTGOING
                    except OSError:
                        pass
                    sock.setblocking(False)
                    sockets[sock] = node
                finally:
                    enter(original)
                    os.close(fd)
            except OSError as error:
                errors[node] = str(error)
        with output.open('wb') as stream:
            stream.write(struct.pack('<IHHIIII', 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1))
            deadline = time.monotonic() + config.get('seconds', 2)
            while sockets and time.monotonic() < deadline:
                ready, _, _ = select.select(list(sockets), [], [], min(.1, max(0, deadline - time.monotonic())))
                for sock in ready:
                    try:
                        raw, address = sock.recvfrom(65535)
                    except BlockingIOError:
                        continue
                    # Record ingress only. Observations across satellites still
                    # count the same packet more than once, intentionally.
                    if address[2] == socket.PACKET_OUTGOING:
                        continue
                    # A C-level scan skips routing chatter and bare TCP ACKs.
                    # Positive evidence still requires complete decoding and
                    # endpoint, run-token and carrier validation below.
                    if token not in raw:
                        continue
                    packet = decode(raw)
                    identity = identify(packet['payload'], token) if packet else None
                    if not identity or identity[0] not in flows:
                        continue
                    flow = flows[identity[0]]
                    endpoints = (packet['source'], packet['destination'])
                    forward = endpoints == (flow['source_address'], flow['destination_address'])
                    reverse = endpoints == (flow['destination_address'], flow['source_address'])
                    if not (forward or reverse) or not matches(flow['carrier'], packet):
                        continue
                    entry = observations.setdefault(flow['id'], {'forward': 0, 'reverse': 0, 'nodes': {}, 'layers': packet['layers'], 'labels': packet['labels']})
                    entry['forward' if forward else 'reverse'] += 1
                    node = sockets[sock]
                    entry['nodes'][node] = entry['nodes'].get(node, 0) + 1
                    if written < 2 * 1024 * 1024:
                        stamp = time.time()
                        stream.write(struct.pack('<IIII', int(stamp), int(stamp % 1 * 1000000), len(raw), len(raw)))
                        stream.write(raw)
                        written += 16 + len(raw)
        result = {'run_id': config['run_id'], 'token': config['token'], 'plan_id': config.get('plan_id'), 'started_at': started,
                  'signatures': config.get('signatures', {}),
                  'finished_at': time.time(), 'flows': observations, 'errors': errors,
                  'scope': 'same-packet encapsulation on satellite ingress; observations are not unique delivery counts'}
        destination = Path(config['output'])
        temporary = destination.with_suffix('.tmp')
        temporary.write_text(json.dumps(result))
        temporary.replace(destination)
        return result
    finally:
        enter(original)
        os.close(original)
        for sock in sockets:
            sock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    args = parser.parse_args()
    capture(json.loads(args.config.read_text()))
