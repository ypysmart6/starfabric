#!/usr/bin/env python3
"""Generate BMv2 file-port input PCAPs and validate routed/drop behavior."""
import argparse
import ipaddress
import json
import struct
from pathlib import Path


PCAP_HEADER = struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)


def checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def ethernet(ether_type: int, payload: bytes) -> bytes:
    return bytes.fromhex("0200000001ff020000000101") + struct.pack("!H", ether_type) + payload


def ipv4(destination: str, ttl: int, marker: bytes) -> bytes:
    source = ipaddress.IPv4Address("192.0.2.10").packed
    target = ipaddress.IPv4Address(destination).packed
    initial = struct.pack("!BBHHHBBH4s4s", 0x45, 184, 20 + len(marker), 7, 0, ttl, 253, 0, source, target)
    header = initial[:10] + struct.pack("!H", checksum(initial)) + initial[12:]
    return ethernet(0x0800, header + marker)


def ipv6(destination: str, hop_limit: int, marker: bytes) -> bytes:
    version_class_flow = (6 << 28) | (184 << 20)
    header = struct.pack(
        "!IHBB16s16s",
        version_class_flow,
        len(marker),
        59,
        hop_limit,
        ipaddress.IPv6Address("2001:db8:100::10").packed,
        ipaddress.IPv6Address(destination).packed,
    )
    return ethernet(0x86DD, header + marker)


def write_pcap(path: Path, packets: list[bytes]) -> None:
    data = bytearray(PCAP_HEADER)
    for index, packet in enumerate(packets, 1):
        data.extend(struct.pack("<IIII", index, 0, len(packet), len(packet)))
        data.extend(packet)
    path.write_bytes(data)


def read_pcap(path: Path) -> list[bytes]:
    data = path.read_bytes()
    if len(data) < 24 or data[:4] != PCAP_HEADER[:4]:
        raise RuntimeError(f"invalid PCAP {path}")
    packets, offset = [], 24
    while offset < len(data):
        if offset + 16 > len(data):
            raise RuntimeError(f"truncated PCAP record in {path}")
        _, _, captured, _ = struct.unpack_from("<IIII", data, offset)
        offset += 16
        packets.append(data[offset : offset + captured])
        offset += captured
    return packets


def generate(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    packets = [
        ipv4("198.51.100.42", 64, b"STARFABRIC-IPV4"),
        ipv4("198.51.100.42", 1, b"DROP-TTL"),
        ipv6("2001:db8:200::42", 64, b"STARFABRIC-IPV6"),
        ipv6("2001:db8:200::42", 1, b"DROP-HOP"),
        ipv4("192.0.2.200", 64, b"DROP-NO-ROUTE"),
    ]
    write_pcap(directory / "port1_in.pcap", packets)
    write_pcap(directory / "port2_in.pcap", [])


def verify(directory: Path, p4runtime_report: Path, output: Path) -> None:
    packets = read_pcap(directory / "port2_out.pcap")
    ipv4_packets = [packet for packet in packets if packet[12:14] == b"\x08\x00"]
    ipv6_packets = [packet for packet in packets if packet[12:14] == b"\x86\xdd"]
    runtime = json.loads(p4runtime_report.read_text(encoding="utf-8"))
    checks = {
        "only_routable_packets_forwarded": len(packets) == 2,
        "ipv4_forwarded": len(ipv4_packets) == 1 and b"STARFABRIC-IPV4" in ipv4_packets[0],
        "ipv6_forwarded": len(ipv6_packets) == 1 and b"STARFABRIC-IPV6" in ipv6_packets[0],
        "mac_rewritten": all(packet[:6] == bytes.fromhex("020000000201") for packet in packets),
        "ipv4_ttl_decremented": len(ipv4_packets) == 1 and ipv4_packets[0][22] == 63,
        "ipv6_hop_limit_decremented": len(ipv6_packets) == 1 and ipv6_packets[0][21] == 63,
        "ipv4_ef_dscp_preserved": len(ipv4_packets) == 1 and ipv4_packets[0][15] == 184,
        "ipv6_ef_dscp_preserved": len(ipv6_packets) == 1
        and (((ipv6_packets[0][14] & 0x0F) << 4) | (ipv6_packets[0][15] >> 4)) == 184,
        "qos_classification_programmed": runtime["table_entries"]["ipv4_qos"] == 1
        and runtime["table_entries"]["ipv6_qos"] == 1,
        "service_meter_programmed": runtime["meter_configured"],
        "p4runtime_readback": runtime["pipeline_installed"]
        and all(value == 1 for value in runtime["table_entries"].values()),
        "counters_observed": runtime["direct_counters"]["ipv4_packets"] >= 1
        and runtime["direct_counters"]["ipv6_packets"] >= 1,
    }
    report = {
        "success": all(checks.values()),
        "evidence_level": "p4c compile + P4Runtime 1.4.1 + BMv2 packet execution",
        "scope": "software v1model target; ASIC/FPGA SDK and physical ports are excluded",
        "checks": checks,
        "forwarded_packets": len(packets),
        "direct_counters": runtime["direct_counters"],
    }
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["success"]:
        raise SystemExit("P4 closed-loop assertions failed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("generate", "verify"))
    parser.add_argument("--directory", type=Path, default=Path("artifacts"))
    parser.add_argument("--p4runtime-report", type=Path, default=Path("artifacts/p4runtime.json"))
    parser.add_argument("--output", type=Path, default=Path("../../reports/p4-closed-loop.json"))
    args = parser.parse_args()
    if args.mode == "generate":
        generate(args.directory)
    else:
        verify(args.directory, args.p4runtime_report, args.output)


if __name__ == "__main__":
    main()
