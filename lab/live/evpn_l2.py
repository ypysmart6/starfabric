"""Ethernet ingress replication and MAC learning for single-homed EVPN sites.

Each EVI has its own bridge and End.DX2 attachment. Only frames arriving from
the local bridge enter the overlay. Frames delivered by End.DX2 are outgoing
on this socket and cannot re-enter the overlay (split horizon). Transit and
decapsulation execute in the Linux SRv6 data plane; ingress encapsulation is
userspace and its counters are exposed, not presented as line-rate hardware.
"""
import asyncio
import ipaddress
import socket
import struct
import time


class Ethernet:
    def __init__(self, speaker):
        self.s = speaker
        self.services = {v['evi']:v for v in speaker.c.get('l2',[])}
        self.local = {}
        self.sockets = []
        self.counters = {'local_frames':0, 'unicast_copies':0, 'bum_copies':0, 'dropped':0}
        self.last_error = None

    def learned(self, evi, mac):
        key = f'mac:{evi}:{mac}'
        remote = self.s.routes.get(key,{})
        old = self.local.get(key)
        if old is None or remote.get('sequence',0) > old['sequence']:
            sequence = remote['sequence']+1 if remote else 0
            self.local[key] = {'evi':evi,'mac':mac,'sequence':sequence,'last_seen':time.monotonic()}
        else:
            old['last_seen'] = time.monotonic()

    def exports(self):
        now = time.monotonic()
        self.local = {k:v for k,v in self.local.items() if now-v['last_seen'] < 300}
        return self.local

    async def receive(self, service):
        ingress = socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(3))
        ingress.bind((service['interface'],0)); ingress.setblocking(False)
        transmit = socket.socket(socket.AF_INET6,socket.SOCK_RAW,socket.IPPROTO_ROUTING)
        transmit.bind((self.s.c['address'],0)); transmit.setblocking(False)
        transmit.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_UNICAST_HOPS,64)
        self.sockets.extend([ingress,transmit])
        loop = asyncio.get_running_loop()
        while True:
            # The pinned tools image runs Python 3.8 (no sock_recvfrom API).
            future=loop.create_future()
            def readable():
                if not future.done(): future.set_result(None)
            loop.add_reader(ingress.fileno(),readable)
            try: await future
            finally: loop.remove_reader(ingress.fileno())
            try: frame,address=ingress.recvfrom(65535)
            except BlockingIOError: continue
            if address[2] == socket.PACKET_OUTGOING or len(frame) < 14: continue
            # Link-local bridge control protocols must never leave the site.
            if frame[:5] == b'\x01\x80\xc2\x00\x00': continue
            if frame[6]&1 or frame[6:12] == bytes(6): continue
            self.counters['local_frames'] += 1
            source = ':'.join(f'{b:02x}' for b in frame[6:12])
            dest = ':'.join(f'{b:02x}' for b in frame[:6])
            evi = service['evi']
            self.learned(evi,source)
            remote = self.s.routes.get(f'mac:{evi}:{dest}') if not frame[0]&1 else None
            routes = [remote] if remote else [r for r in self.s.routes.values() if r['route_type']==3 and r['evi']==evi]
            if not routes: self.counters['dropped'] += 1
            for route in routes:
                # SRH next header 143 is an unmodified Ethernet frame.
                header = struct.pack('!BBBBBBH',143,2,4,0,0,0,0)+ipaddress.IPv6Address(route['sid']).packed
                try:
                    transmit.sendto(header+frame,(route['sid'],0))
                    self.counters['unicast_copies' if remote else 'bum_copies'] += 1
                except OSError as error:
                    self.counters['dropped'] += 1; self.last_error = str(error)

    def close(self):
        for sock in self.sockets: sock.close()
