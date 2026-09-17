#!/usr/bin/env python3
"""Small deterministic PCE peer used only to prove a real PCEP exchange.

It implements OPEN/KEEPALIVE and answers every PCReq with a standards-shaped
PCRep carrying the original RP object plus a NO-PATH object.  This deliberately
tests the PCC's negative-result/fallback path without pretending that this lab
PCE is a production path-computation engine.
"""

from __future__ import annotations

import argparse
import json
import socket
import struct
from datetime import datetime, timezone
from pathlib import Path


OPEN = 1
KEEPALIVE = 2
PCREQ = 3
PCREP = 4
CLOSE = 7


def message(message_type: int, body: bytes = b"") -> bytes:
    return struct.pack("!BBH", 0x20, message_type, 4 + len(body)) + body


def open_message(session_id: int) -> bytes:
    # Advertise stateful and SR-PCE/MSD capabilities.  FRR pathd enables both
    # when loaded with pathd_pcep and expects a capable PCE for dynamic paths.
    capabilities = bytes.fromhex(
        "0010000400000001"  # Stateful PCE Capability TLV
        "002200100000000101000000001a000400000004"  # SR-PCE + Path MSD=4
    )
    object_length = 8 + len(capabilities)
    open_object = struct.pack("!BBHBBBB", 1, 0x10, object_length, 0x20, 30, 120, session_id) + capabilities
    return message(OPEN, open_object)


def objects(body: bytes):
    offset = 0
    while offset + 4 <= len(body):
        obj_class, obj_flags, obj_length = struct.unpack("!BBH", body[offset : offset + 4])
        if obj_length < 4 or offset + obj_length > len(body):
            return
        yield obj_class, obj_flags >> 4, body[offset : offset + obj_length]
        offset += obj_length


def no_path_reply(body: bytes) -> bytes | None:
    rp = next((raw for obj_class, _obj_type, raw in objects(body) if obj_class == 2), None)
    if rp is None:
        return None
    # NO-PATH object class 3/type 1, Nature-of-Issue 0 (unsatisfied request).
    no_path = struct.pack("!BBHBBBB", 3, 0x12, 8, 0, 0, 0, 0)
    return message(PCREP, rp + no_path)


def append_event(path: Path, event: str, **fields) -> None:
    row = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")
        stream.flush()


def receive_exact(connection: socket.socket, size: int) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = connection.recv(size - len(chunks))
        if not chunk:
            return None
        chunks.extend(chunk)
    return bytes(chunks)


def serve(listen: str, port: int, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("", encoding="utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((listen, port))
        server.listen(4)
        append_event(output, "listening", address=listen, port=port)
        session_id = 1
        while True:
            connection, peer = server.accept()
            with connection:
                append_event(output, "tcp_accepted", peer=peer[0], peer_port=peer[1])
                connection.sendall(open_message(session_id))
                session_id = session_id % 255 + 1
                while True:
                    header = receive_exact(connection, 4)
                    if header is None:
                        append_event(output, "tcp_closed")
                        break
                    version_flags, message_type, length = struct.unpack("!BBH", header)
                    if length < 4:
                        append_event(output, "invalid_length", length=length)
                        break
                    body = receive_exact(connection, length - 4)
                    if body is None:
                        append_event(output, "truncated_message", message_type=message_type)
                        break
                    append_event(
                        output,
                        "message_received",
                        message_type=message_type,
                        length=length,
                        version=version_flags >> 5,
                        object_classes=[obj_class for obj_class, _obj_type, _raw in objects(body)],
                        body_hex=body.hex(),
                    )
                    if message_type == OPEN:
                        connection.sendall(message(KEEPALIVE))
                        append_event(output, "keepalive_sent")
                    elif message_type == PCREQ:
                        reply = no_path_reply(body)
                        if reply is not None:
                            connection.sendall(reply)
                            append_event(output, "pc_rep_no_path_sent")
                    elif message_type == CLOSE:
                        append_event(output, "close_received")
                        break


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=4189)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    serve(args.listen, args.port, args.output)


if __name__ == "__main__":
    main()
