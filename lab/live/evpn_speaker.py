#!/usr/bin/env python3
"""Gateway-local, restricted iBGP EVPN/SRv6 speaker for the live SIL fabric.

Peers exchange RFC 9252 Type-5 routes over real satellite IPv6 links. TCP 1179
isolates this experimental speaker from FRR's existing BGP-LS/VXLAN sessions.
Only explicitly configured peers, endpoint prefixes and EVIs are imported.
Type-2 MAC learning and Type-3 ingress replication serve single-homed sites.
No RR, AS transit, graceful restart or EVPN multihoming is claimed.
"""
import asyncio
import ctypes
import ipaddress
import json
import os
from pathlib import Path
import signal
import struct
import sys
import time

try:
    from .evpn_codec import message, open_message, read_open, read_update, update, l2_update
    from .evpn_l2 import Ethernet
    from .storage import atomic
except ImportError:
    from evpn_codec import message, open_message, read_open, read_update, update, l2_update
    from evpn_l2 import Ethernet
    from storage import atomic


async def receive(reader, timeout):
    async def packet():
        header = await reader.readexactly(19)
        size, kind = struct.unpack('!HB', header[16:])
        if header[:16] != b'\xff'*16 or not 19 <= size <= 4096 or kind not in (1,2,3,4):
            raise ValueError('invalid BGP header')
        return kind, await reader.readexactly(size-19)
    return await asyncio.wait_for(packet(), timeout)


class Speaker:
    def __init__(self, config):
        self.c = config
        self.routes, self.sessions, self.errors = {}, {}, {}
        self.stopping = asyncio.Event()
        self.tasks = set()
        self.ethernet = Ethernet(self)
        self.peer_routes = {}
        self.writers = {}
        self.advertised = {}
        self.fatal_error = None

    def select_routes(self):
        self.routes = {}
        for peer, routes in sorted(self.peer_routes.items(), reverse=True):
            for key, route in routes.items():
                if key not in self.routes or route.get('sequence',0) >= self.routes[key].get('sequence',0):
                    self.routes[key] = route

    def exports(self):
        result = {}
        for prefix in self.c['exports']:
            sid = self.c.get('export_sids',{}).get(prefix,self.c['sid'])
            result[prefix] = (update(self.c['router_id'],prefix,self.c['address'],sid),
                              update(self.c['router_id'],prefix,self.c['address'],sid,True))
        for evi, service in self.ethernet.services.items():
            args = (self.c['router_id'],evi,self.c['address'],service['sid'])
            result[f'imet:{evi}'] = (l2_update(*args),l2_update(*args,withdraw=True))
        for key, mac in self.ethernet.exports().items():
            args = (self.c['router_id'],mac['evi'],self.c['address'],self.ethernet.services[mac['evi']]['sid'])
            result[key] = (l2_update(*args,mac=mac['mac'],sequence=mac['sequence']),
                           l2_update(*args,mac=mac['mac'],withdraw=True))
        return result

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        def done(task):
            self.tasks.discard(task)
            if not task.cancelled() and task.exception():
                self.fatal_error=task.exception(); self.stopping.set()
        task.add_done_callback(done)
        return task

    async def session(self, reader, writer, peer):
        keepalive = None
        owned = False
        try:
            if peer not in self.c['peers'] or peer in self.sessions: return
            self.sessions[peer] = 'OpenSent'; owned = True
            writer.write(open_message(self.c['router_id'])); await writer.drain()
            kind, body = await receive(reader, 15)
            if kind != 1: raise ValueError('expected OPEN')
            hold = read_open(body)
            writer.write(message(4)); await writer.drain()
            kind, body = await receive(reader, hold)
            if kind != 4 or body: raise ValueError('expected KEEPALIVE')
            self.sessions[peer] = 'Established'; self.errors.pop(peer, None)
            self.writers[peer] = writer
            self.peer_routes[peer] = {}
            self.advertised[peer] = self.exports()
            for announced, _ in self.advertised[peer].values(): writer.write(announced)
            await writer.drain()
            async def keep():
                while True:
                    await asyncio.sleep(hold/3)
                    writer.write(message(4)); await writer.drain()
            keepalive = asyncio.create_task(keep())
            while not self.stopping.is_set():
                kind, body = await receive(reader, hold)
                if kind == 4 and not body: continue
                if kind != 2: raise ValueError('unexpected BGP message / NOTIFICATION')
                routes, withdrawn = read_update(body)
                for key in withdrawn: self.peer_routes[peer].pop(key,None)
                for route in routes:
                    key = route['key']
                    if route['route_type'] == 5:
                        expected = self.c['imports'].get(route['prefix'])
                        if not expected or expected['peer'] != peer: continue
                    else:
                        if route['evi'] not in self.ethernet.services: continue
                        expected = {'locator':str(ipaddress.IPv6Network(peer+'/64',strict=False))}
                    if route['nexthop'] != peer or ipaddress.IPv6Address(route['sid']) not in ipaddress.IPv6Network(expected['locator']):
                        self.peer_routes[peer].pop(key,None); continue
                    self.peer_routes[peer][key] = {**route, 'peer':peer}
                self.select_routes()
        except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError) as error:
            self.errors[peer] = str(error)[-300:]
        finally:
            if keepalive:
                keepalive.cancel()
                await asyncio.gather(keepalive, return_exceptions=True)
            if owned:
                self.sessions.pop(peer, None)
                self.writers.pop(peer,None); self.advertised.pop(peer,None); self.peer_routes.pop(peer,None)
                self.select_routes()
            writer.close()
            try: await writer.wait_closed()
            except OSError: pass

    async def connect(self, peer):
        while not self.stopping.is_set():
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(peer, self.c.get('port',1179),
                    local_addr=(self.c['address'],0)), 5)
                await self.session(reader, writer, peer)
            except (OSError, asyncio.TimeoutError) as error: self.errors[peer] = str(error)[-300:]
            try: await asyncio.wait_for(self.stopping.wait(), 3)
            except asyncio.TimeoutError: pass

    async def publish(self):
        while not self.stopping.is_set():
            exports = self.exports()
            for peer, writer in list(self.writers.items()):
                old = self.advertised.get(peer,{})
                try:
                    for key, (_, withdrawal) in old.items():
                        if key not in exports: writer.write(withdrawal)
                    for key, (announcement, _) in exports.items():
                        if old.get(key) != exports[key]: writer.write(announcement)
                    await asyncio.wait_for(writer.drain(), 2)
                    self.advertised[peer] = exports
                except (OSError, asyncio.TimeoutError):
                    # A blocked peer must not stop all local MAC publication.
                    writer.close()
            # Withdraw stale steering even if the central controller's tick is
            # delayed. The unreachable route cannot fall back to native IP.
            for prefix, value in self.c['imports'].items():
                if prefix in self.routes: continue
                for table in value['tables']:
                    proc = await asyncio.create_subprocess_exec('ip','route','replace','unreachable',prefix,'table',str(table),
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
                    _, err = await proc.communicate()
                    if proc.returncode: raise RuntimeError('EVPN withdrawal failed: '+err.decode())
            atomic(Path(self.c['output']), {'run_id':self.c['run_id'], 'observed_at':time.time(),
                'node':self.c['node'], 'sessions':self.sessions, 'routes':self.routes, 'errors':self.errors,
                'l2':{'local_macs':self.ethernet.local, 'counters':self.ethernet.counters,
                      'last_error':self.ethernet.last_error, 'evis':sorted(self.ethernet.services)}})
            try: await asyncio.wait_for(self.stopping.wait(), 1)
            except asyncio.TimeoutError: pass

    async def run(self):
        def accept(reader, writer):
            self.spawn(self.session(reader, writer, str(ipaddress.IPv6Address(writer.get_extra_info('peername')[0]))))
        server = await asyncio.start_server(accept, self.c['address'], self.c.get('port',1179))
        for service in self.ethernet.services.values(): self.spawn(self.ethernet.receive(service))
        for peer in self.c['peers']:
            if int(ipaddress.IPv6Address(self.c['address'])) < int(ipaddress.IPv6Address(peer)):
                self.spawn(self.connect(peer))
        publisher = self.spawn(self.publish())
        waiter = asyncio.create_task(self.stopping.wait())
        try:
            await asyncio.wait([publisher, waiter], return_when=asyncio.FIRST_COMPLETED)
            if publisher.done(): publisher.result()
        finally:
            self.stopping.set(); server.close(); await server.wait_closed()
            for task in self.tasks: task.cancel()
            waiter.cancel()
            await asyncio.gather(*list(self.tasks), waiter, return_exceptions=True)
            self.ethernet.close()
            atomic(Path(self.c['output']), {'run_id':self.c['run_id'], 'observed_at':time.time(),
                                          'node':self.c['node'], 'sessions':{}, 'routes':{}, 'stopped':True})
        if self.fatal_error: raise self.fatal_error


def main():
    config = json.loads(Path(sys.argv[1]).read_text())
    if config.get('pid'):
        with open(f"/proc/{config['pid']}/ns/net", 'rb') as ns:
            if ctypes.CDLL(None, use_errno=True).setns(ns.fileno(), 0x40000000):
                raise OSError(ctypes.get_errno(), 'setns')
    async def run():
        speaker = Speaker(config)
        for signum in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(signum, speaker.stopping.set)
        await speaker.run()
    asyncio.run(run())


if __name__ == '__main__': main()
