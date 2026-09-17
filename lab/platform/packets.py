"""Decode encapsulation and GTP-U from the same captured packet, without deps."""
from __future__ import annotations

import ipaddress
import struct
from collections import Counter


def gtpu_packets(data, protocol, layers=(), labels=(), depth=0):
    if depth > 16:
        return []
    if protocol == 0x6558:  # Ethernet frame inside VXLAN
        if len(data) < 14:
            return []
        return gtpu_packets(data[14:], int.from_bytes(data[12:14], "big"), layers, labels, depth+1)
    if protocol in (0x8847, 0x8848):
        stack = []
        while len(data) >= 4:
            value = int.from_bytes(data[:4], "big")
            stack.append(value >> 12)
            data = data[4:]
            if value & 0x100:
                break
        else:
            return []
        if not data:
            return []
        return gtpu_packets(data, 0x86DD if data[0] >> 4 == 6 else 0x0800, layers+("mpls",), labels+tuple(stack), depth+1)
    if protocol == 0x0800:
        if len(data) < 20 or data[0] >> 4 != 4:
            return []
        length = (data[0] & 15) * 4
        if length < 20 or len(data) < length or int.from_bytes(data[6:8], "big") & 0x1FFF:
            return []
        source, destination = (str(ipaddress.ip_address(data[a:b])) for a, b in ((12, 16), (16, 20)))
        next_header, payload = data[9], data[length:]
    elif protocol == 0x86DD:
        if len(data) < 40 or data[0] >> 4 != 6:
            return []
        source, destination = (str(ipaddress.ip_address(data[a:b])) for a, b in ((8, 24), (24, 40)))
        next_header, payload = data[6], data[40:]
        layers += ("ipv6",)
        for _ in range(8):
            if next_header not in (0, 43, 60):
                break
            if len(payload) < 8:
                return []
            size = (payload[1] + 1) * 8
            if len(payload) < size:
                return []
            if next_header == 43 and payload[2] == 4:
                layers += ("srh",)
            next_header, payload = payload[0], payload[size:]
    else:
        return []
    if next_header in (4, 41):
        return gtpu_packets(payload, 0x0800 if next_header == 4 else 0x86DD,
                            layers+(("ipip",) if protocol == 0x0800 else ()), labels, depth+1)
    if next_header != 17 or len(payload) < 8:
        return []
    source_port, destination_port, length = struct.unpack("!HHH", payload[:6])
    if length < 8 or len(payload) < length:
        return []
    payload = payload[8:length]
    if destination_port == 4789 and len(payload) >= 8 and payload[0] & 8:
        return gtpu_packets(payload[8:], 0x6558, layers+("vxlan",), labels, depth+1)
    if source_port == destination_port == 2152 and len(payload) >= 8 and payload[0] >> 5 == 1 and payload[1] == 255:
        return [{"source": source, "destination": destination, "layers": list(layers), "labels": list(labels),
                 "teid": int.from_bytes(payload[4:8], "big")}]
    return []


def read(path):
    with path.open("rb") as stream:
        header = stream.read(24)
        if len(header) != 24:
            raise ValueError("truncated pcap header")
        endian = {b"\xd4\xc3\xb2\xa1": "<", b"\xa1\xb2\xc3\xd4": ">", b"\x4d\x3c\xb2\xa1": "<", b"\xa1\xb2\x3c\x4d": ">"}.get(header[:4])
        if endian is None:
            raise ValueError("unsupported pcap magic")
        linktype = struct.unpack(endian+"I", header[20:24])[0] & 0xFFFF
        scale = 1e9 if header[:4] in (b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d") else 1e6
        if linktype not in (1, 113, 276):
            raise ValueError("unsupported pcap link type " + str(linktype))
        result = []
        while frame := stream.read(16):
            if len(frame) != 16:
                raise ValueError("truncated packet header")
            sec, frac, captured, wire = struct.unpack(endian+"IIII", frame)
            if captured > 4*1024*1024:
                raise ValueError("oversize packet record")
            data = stream.read(captured)
            if len(data) != captured:
                raise ValueError("truncated packet")
            size, offset = {1: (14, 12), 113: (16, 14), 276: (20, 0)}[linktype]
            if len(data) >= size:
                for packet in gtpu_packets(data[size:], int.from_bytes(data[offset:offset+2], "big")):
                    result.append({"seconds": sec, "timestamp": sec + frac / scale, **packet})
        return result


def proof(path, carrier, source, destination, require_bidirectional=True, window=None):
    packets = read(path)
    if window is not None:
        packets = [p for p in packets if window[0] <= p["timestamp"] <= window[1]]
    required = {"native": None, "ospf": "ipip", "ldp": "mpls", "sr-mpls": "mpls", "pcep": "mpls", "srv6": "srh", "evpn": "vxlan"}[carrier]
    selected = [p for p in packets if (required is None or required in p["layers"]) and {p["source"], p["destination"]} == {source, destination}]
    if carrier in {"sr-mpls", "pcep"}:
        selected = [p for p in selected if any(16000 <= label < 24000 for label in p["labels"])]
    elif carrier == "ldp":
        selected = [p for p in selected if p["labels"] and all(16 <= label < 15000 for label in p["labels"])]
    directions = Counter((p["source"], p["destination"]) for p in selected)
    if require_bidirectional and (not directions[source, destination] or not directions[destination, source]):
        raise AssertionError(f"{path.name}: no bidirectional GTP-U inside {carrier}: {dict(directions)}")
    return {"carrier": carrier, "uplink": directions[source, destination], "downlink": directions[destination, source],
            "teids": sorted({p["teid"] for p in selected}), "label_stacks": sorted({tuple(p["labels"]) for p in selected}),
            "examples": selected[:6]}
