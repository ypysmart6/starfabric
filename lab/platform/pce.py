"""Small SR PCE for this platform's committed paths and live BGP-LS graph.

PCRep ERO is RFC 8664 SR-ERO (type 36), using explicit MPLS SIDs.
This is a bounded SIL PCE; it is not an implementation of every PCEP extension.
"""
from __future__ import annotations

import json
import socket
import struct
import threading
from datetime import datetime, timezone


def message(kind, body=b""):
    return struct.pack("!BBH", 0x20, kind, 4 + len(body)) + body


def objects(body):
    offset = 0
    while offset < len(body):
        if len(body) - offset < 4:
            raise ValueError("truncated PCEP object")
        cls, flags, length = struct.unpack("!BBH", body[offset:offset+4])
        if length < 4 or length % 4 or offset + length > len(body):
            raise ValueError("invalid PCEP object length")
        yield cls, flags >> 4, body[offset:offset+length]
        offset += length


def ero(labels):
    if not labels or len(labels) > 32 or any(not 16 <= label < 1048576 for label in labels):
        raise ValueError("invalid SR SID list")
    # NT=0; F=1 (NAI absent), M=1 (SID is a full MPLS stack entry).
    body = b"".join(struct.pack("!BBBBI", 36, 8, 0, 9, label << 12) for label in labels)
    return struct.pack("!BBH", 7, 0x12, 4 + len(body)) + body


def tlvs(data):
    while data:
        if len(data) < 4:
            raise ValueError('truncated PCEP TLV')
        kind, size = struct.unpack('!HH', data[:4])
        end = 4 + ((size + 3) // 4) * 4
        if len(data) < end:
            raise ValueError('truncated PCEP TLV value')
        yield kind, data[4:4+size]
        data = data[end:]


def pcc_sid_depth(body):
    opening = next(raw for cls, typ, raw in objects(body) if cls == 1 and typ == 1)
    for kind, value in tlvs(opening[8:]):
        if kind != 34 or len(value) < 4:
            continue
        offset = 4 + ((value[3] + 3) // 4) * 4
        for subkind, capability in tlvs(value[offset:]):
            if subkind == 26 and len(capability) == 4:
                if capability[2] & 1:
                    return 32
                if capability[3]:
                    return min(32, capability[3])
                raise ValueError('PCC advertised zero SID depth without X flag')
    raise ValueError('PCC did not advertise SR SID depth')


class PCE:
    def __init__(self, protocols, host, port=4189, artifact_dir=None):
        self.protocols, self.runtime = protocols, protocols.r
        self.artifact_dir = artifact_dir or self.runtime.art
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.responses = []
        self.connections = []
        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((host, port))
        self.server.listen(4)
        self.server.settimeout(1)
        self.thread = threading.Thread(target=self.accept, daemon=True)
        self.thread.start()

    def log(self, event, **data):
        with self.lock:
            path = self.artifact_dir / 'pcep.jsonl'
            if path.exists() and path.stat().st_size > 4 * 1024 * 1024:
                path.replace(path.with_suffix('.jsonl.1'))
            with path.open("a") as stream:
                stream.write(json.dumps({"run_id": self.runtime.identifier, "at": datetime.now(timezone.utc).isoformat(), "event": event, **data}) + "\n")

    def accept(self):
        while not self.stopped.is_set():
            try:
                conn, peer = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self.lock:
                self.connections.append(conn)
            threading.Thread(target=self.serve, args=(conn, peer), daemon=True).start()

    def serve(self, conn, peer):
        # RFC 8664 section 5.1: a PCE sends X=1, MSD=0. The PCC's own
        # advertisement below is the limit on responses in this session.
        caps = bytes.fromhex("0010000400000001002200100000000101000000001a000400000100")
        max_sids = None
        conn.settimeout(10)
        buffer = bytearray()
        try:
            conn.sendall(message(1, struct.pack("!BBHBBBB", 1, 0x10, 8 + len(caps), 0x20, 10, 40, 1) + caps))
            while not self.stopped.is_set():
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    conn.sendall(message(2))
                    continue
                if not chunk:
                    break
                buffer.extend(chunk)
                while len(buffer) >= 4:
                    version, kind, length = struct.unpack("!BBH", buffer[:4])
                    if version >> 5 != 1 or length < 4:
                        raise ValueError("invalid PCEP header")
                    if len(buffer) < length:
                        break
                    body = bytes(buffer[4:length])
                    del buffer[:length]
                    self.log("received", peer=peer[0], type=kind, body_hex=body.hex())
                    if kind == 1:
                        max_sids = pcc_sid_depth(body)
                        self.log('pcc_sid_depth', peer=peer[0], maximum=max_sids)
                        conn.sendall(message(2))
                    elif kind == 3:
                        if max_sids is None:
                            raise ValueError('PCReq received before SR capability negotiation')
                        reply = self.reply(body, max_sids)
                        conn.sendall(reply)
                    elif kind == 7:
                        return
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            self.log("session_error", error=str(error))
        finally:
            conn.close()
            with self.lock:
                if conn in self.connections:
                    self.connections.remove(conn)

    def reply(self, body, max_sids):
        values = list(objects(body))
        rp = next(raw for cls, _, raw in values if cls == 2)
        endpoints = next(raw for cls, typ, raw in values if cls == 4 and typ == 1)
        target = socket.inet_ntoa(endpoints[8:12])
        fabric = self.protocols.f
        if hasattr(self.protocols, 'resolve_pcep_target'):
            intent = self.protocols.resolve_pcep_target(target)
        else:
            intent = {fabric.loopback(remote): name for _, remote, _, name in fabric.service()}.get(target)
        if intent is None:
            raise ValueError("PCEP target is outside platform service")
        plan, path = self.protocols.path(intent)
        live_edges = self.protocols.edges()
        if not all((a, b) in live_edges for a, b in zip(path, path[1:])):
            self.log("no_path", reason="committed path not present in live BGP-LS", plan_id=plan["id"])
            return message(4, rp + struct.pack("!BBHBBBB", 3, 0x12, 8, 0, 0, 0, 0))
        labels = [fabric.sid(node) for node in path[1:]]
        if len(labels) > max_sids:
            self.log('no_path', reason='committed path exceeds negotiated PCC SID depth', labels=labels, maximum=max_sids)
            return message(4, rp + struct.pack('!BBHBBBB', 3, 0x12, 8, 0, 0, 0, 0))
        record = {"intent": intent, "plan_id": plan["id"], "topology_version": plan["topology_version"], "nodes": path,
                  "labels": labels, "bgpls_edges": sorted(live_edges)}
        self.responses.append(record)
        # Long-lived workload mode must not retain an unbounded request history.
        if hasattr(self.protocols, 'resolve_pcep_target'):
            self.responses[:] = self.responses[-512:]
        self.log("positive_pc_rep", **record)
        return message(4, rp + ero(labels))

    def close(self):
        self.stopped.set()
        self.server.close()
        with self.lock:
            connections = list(self.connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self.thread.join(timeout=3)
