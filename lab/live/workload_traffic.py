#!/usr/bin/env python3
"""Paced TCP/UDP echo traffic. Runs in a gateway's network namespace.

Only acknowledged, run-tagged application bytes count as goodput. Pending
packets are separate from timed-out packets; TCP framing survives partial
reads. One bounded statistics file per gateway is replaced atomically.
"""
from __future__ import annotations

import argparse
from collections import deque
import json
import math
from pathlib import Path
import signal
import socket
import struct
import threading
import time

HEADER = struct.Struct('!4s16sIQQ')
MAGIC = b'SFW1'


def packet(token, number, sequence, size):
    return HEADER.pack(MAGIC, token, number, sequence, time.monotonic_ns()) + bytes(size - HEADER.size)


def identify(data, token):
    if len(data) < HEADER.size:
        return None
    magic, actual, number, sequence, sent = HEADER.unpack_from(data)
    return (number, sequence, sent) if magic == MAGIC and actual == token else None


def receive_exact(sock, count, stopped):
    result = bytearray()
    while len(result) < count and not stopped.is_set():
        try:
            part = sock.recv(count - len(result))
        except socket.timeout:
            continue
        if not part:
            raise ConnectionError('TCP peer closed')
        result.extend(part)
    if len(result) != count:
        raise ConnectionError('worker stopped')
    return bytes(result)


class Counters:
    def __init__(self):
        self.lock = threading.Lock()
        self.values = {'tx_packets': 0, 'tx_bytes': 0, 'acked_packets': 0, 'acked_bytes': 0,
                       'received_packets': 0, 'received_bytes': 0, 'expired_packets': 0, 'errors': 0}
        self.pending = {}
        self.samples = deque(maxlen=512)
        self.last_rtt = None
        self.jitter = 0.0
        self.previous = (time.monotonic(), 0, 0)
        self.last_error = None

    def sent(self, sequence, size):
        with self.lock:
            if len(self.pending) >= 10000:
                self.pending.pop(next(iter(self.pending)))
                self.values['expired_packets'] += 1
            self.pending[sequence] = (time.monotonic(), size)
            self.values['tx_packets'] += 1
            self.values['tx_bytes'] += size

    def received(self, size):
        with self.lock:
            self.values['received_packets'] += 1
            self.values['received_bytes'] += size

    def acknowledged(self, sequence):
        with self.lock:
            value = self.pending.pop(sequence, None)
            if value is None:
                return
            sent, size = value
            rtt = (time.monotonic() - sent) * 1000
            self.values['acked_packets'] += 1
            self.values['acked_bytes'] += size
            self.samples.append(rtt)
            if self.last_rtt is not None:
                self.jitter += (abs(rtt - self.last_rtt) - self.jitter) / 16
            self.last_rtt = rtt

    def error(self, error):
        with self.lock:
            self.values['errors'] += 1
            self.last_error = str(error)[-200:]

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            for sequence, (sent, _) in list(self.pending.items()):
                if now - sent > 3:
                    del self.pending[sequence]
                    self.values['expired_packets'] += 1
            at, tx, rx = self.previous
            elapsed = max(0.001, now - at)
            ordered = sorted(self.samples)
            finished = self.values['acked_packets'] + self.values['expired_packets']
            result = {**self.values, 'pending_packets': len(self.pending),
                      'tx_bps': (self.values['tx_bytes'] - tx) * 8 / elapsed,
                      'goodput_bps': (self.values['acked_bytes'] - rx) * 8 / elapsed,
                      'loss_percent': self.values['expired_packets'] * 100 / finished if finished else None,
                      'rtt_p95_ms': ordered[math.ceil(len(ordered) * .95) - 1] if ordered else None,
                      'rtt_samples': len(ordered), 'jitter_ms': self.jitter if ordered else None,
                      'last_error': self.last_error}
            self.previous = (now, self.values['tx_bytes'], self.values['acked_bytes'])
            return result


class Worker:
    def __init__(self, config):
        self.config = config
        self.node = config['node']
        self.token = bytes.fromhex(config['token'])
        self.stopped = threading.Event()
        self.flows = config['flows']
        self.counters = {f['id']: Counters() for f in self.flows if self.node in (f['source'], f['destination'])}
        self.sockets = []
        self.socket_lock = threading.Lock()
        self.rate_scale = 1.0

    def reload_rate(self):
        try:
            value = json.loads(Path(self.config['rate_file']).read_text())
            scale = value.get('scale')
            if (value.get('run_id') == self.config['run_id'] and value.get('token') == self.config['token']
                    and type(scale) in (int, float) and math.isfinite(scale) and .1 <= scale <= 1):
                self.rate_scale = scale
        except (KeyError, OSError, ValueError):
            pass

    def sock(self, flow, address, listening=False):
        sock = socket.socket(socket.AF_INET6 if flow['family'] == 6 else socket.AF_INET,
                             socket.SOCK_STREAM if flow['transport'] == 'tcp' else socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if flow['family'] == 6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_TCLASS, flow['dscp'] << 2)
        else:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, flow['dscp'] << 2)
        if flow['transport'] == 'tcp':
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.bind((address, flow['port'] if listening else 0))
        sock.settimeout(.5)
        with self.socket_lock:
            self.sockets.append(sock)
        return sock

    def close_socket(self, sock):
        with self.socket_lock:
            if sock in self.sockets:
                self.sockets.remove(sock)
        sock.close()

    def accept_payload(self, flow, raw, ack=False):
        value = identify(raw, self.token)
        if not value or value[0] != flow['number'] or len(raw) != flow['payload_bytes']:
            return False
        if ack:
            self.counters[flow['id']].acknowledged(value[1])
        else:
            self.counters[flow['id']].received(len(raw))
        return True

    def serve(self, flow):
        counter = self.counters[flow['id']]
        try:
            sock = self.sock(flow, flow['destination_address'], True)
        except OSError as error:
            counter.error(error)
            return
        if flow['transport'] == 'udp':
            while not self.stopped.is_set():
                try:
                    data, peer = sock.recvfrom(2048)
                    if peer[0] == flow['source_address'] and self.accept_payload(flow, data):
                        sock.sendto(data, peer)
                except socket.timeout:
                    pass
                except OSError as error:
                    counter.error(error)
            return
        sock.listen(2)
        while not self.stopped.is_set():
            try:
                connection, peer = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            connection.settimeout(.5)
            try:
                if peer[0] != flow['source_address']:
                    continue
                while not self.stopped.is_set():
                    data = receive_exact(connection, flow['payload_bytes'], self.stopped)
                    if not self.accept_payload(flow, data):
                        raise ValueError('invalid workload frame')
                    connection.sendall(data)
            except (OSError, ValueError) as error:
                counter.error(error)
            finally:
                connection.close()

    def send(self, flow):
        counter = self.counters[flow['id']]
        sequence = 0
        while not self.stopped.is_set():
            try:
                sock = self.sock(flow, flow['source_address'])
            except OSError as error:
                counter.error(error)
                self.stopped.wait(1)
                continue
            disconnected = threading.Event()
            receiver = None
            try:
                sock.settimeout(2)
                sock.connect((flow['destination_address'], flow['port']))
                sock.settimeout(.5)
                def receive():
                    try:
                        while not self.stopped.is_set() and not disconnected.is_set():
                            try:
                                raw = (receive_exact(sock, flow['payload_bytes'], self.stopped)
                                       if flow['transport'] == 'tcp' else sock.recv(2048))
                            except socket.timeout:
                                continue
                            self.accept_payload(flow, raw, ack=True)
                    except OSError as error:
                        counter.error(error)
                    finally:
                        disconnected.set()
                receiver = threading.Thread(target=receive, daemon=True)
                receiver.start()
                deadline = time.monotonic()
                while not self.stopped.is_set() and not disconnected.is_set():
                    if self.stopped.wait(max(0, deadline - time.monotonic())):
                        break
                    sequence += 1
                    raw = packet(self.token, flow['number'], sequence, flow['payload_bytes'])
                    counter.sent(sequence, len(raw))
                    if flow['transport'] == 'tcp':
                        sock.sendall(raw)
                    else:
                        sock.send(raw)
                    interval = flow['payload_bytes'] * 8 / (flow['rate_bps'] * self.rate_scale)
                    deadline = max(deadline + interval, time.monotonic())
            except OSError as error:
                counter.error(error)
            finally:
                disconnected.set()
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.close_socket(sock)
                if receiver:
                    receiver.join(timeout=1)
            self.stopped.wait(1)

    def run(self):
        self.reload_rate()
        for flow in self.flows:
            if flow['destination'] == self.node:
                threading.Thread(target=self.serve, args=(flow,), daemon=True).start()
            if flow['source'] == self.node:
                threading.Thread(target=self.send, args=(flow,), daemon=True).start()
        path = Path(self.config['output'])
        while not self.stopped.wait(self.config.get('sample_seconds', 5)):
            self.reload_rate()
            value = {'run_id': self.config['run_id'], 'token': self.config['token'], 'node': self.node,
                     'rate_scale': self.rate_scale,
                     'observed_at': time.time(), 'flows': {name: c.snapshot() for name, c in self.counters.items()}}
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(value))
            temporary.replace(path)
        with self.socket_lock:
            for sock in self.sockets:
                sock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    args = parser.parse_args()
    worker = Worker(json.loads(args.config.read_text()))
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: worker.stopped.set())
    worker.run()


if __name__ == '__main__':
    main()
