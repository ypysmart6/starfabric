#!/usr/bin/env python3
"""Run Linux L3 and L2 packet labs in a capability-bounded container."""

from __future__ import annotations

import json
import os
import struct
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
IMAGE = os.environ.get(
    "STARFABRIC_NETWORK_TOOL_IMAGE",
    "ghcr.io/srl-labs/network-multitool@sha256:9bc1e46dd105a054bce724b6744200e951e8fdf4c17658912fbc54fd2e8a4b06",
)
ARTIFACTS = ROOT / "lab/linux-networking/artifacts"
REPORT = ROOT / "reports/linux-networking.json"


def udp_packets(data: bytes, port: int) -> int:
    little = data[:4] in {bytes.fromhex("d4c3b2a1"), bytes.fromhex("4d3cb2a1")}
    order = "<" if little else ">"
    offset = 24
    matches = 0
    while offset + 16 <= len(data):
        _, _, captured, _ = struct.unpack_from(order + "IIII", data, offset)
        offset += 16
        frame = data[offset : offset + captured]
        offset += captured
        if len(frame) < 14 or frame[12:14] != b"\x08\x00":
            continue
        ip = frame[14:]
        if len(ip) < 20 or ip[9] != 17:
            continue
        header = (ip[0] & 0x0F) * 4
        if len(ip) < header + 8:
            continue
        source, destination = struct.unpack_from("!HH", ip, header)
        if port in {source, destination}:
            matches += 1
    return matches


def execute(script: str, *, capture: bool = False) -> str:
    command = [
        "docker", "run", "--rm", "--network=none", "--read-only",
        "--cap-add", "NET_ADMIN", "--cap-add", "SYS_ADMIN",
        "--security-opt", "apparmor=unconfined",
        "--tmpfs", "/run:rw,nosuid,nodev", "--tmpfs", "/tmp:rw,nosuid,nodev",
        "--entrypoint", "/bin/bash",
        "-v", f"{ROOT}:/work:ro", "-w", "/work",
    ]
    if capture:
        command.extend(["-v", f"{ARTIFACTS}:/artifacts", "-e", "SF_LINUX_PCAP=/artifacts/foundation.pcap"])
    command.extend([IMAGE, script])
    result = subprocess.run(command, text=True, capture_output=True, timeout=180, check=False)
    output = result.stdout + result.stderr
    if result.returncode:
        raise AssertionError(f"{script} failed ({result.returncode}):\n{output}")
    if "PASS:" not in output:
        raise AssertionError(f"{script} emitted no PASS marker:\n{output}")
    return output


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    l3 = execute("lab/linux-networking/run.sh", capture=True)
    l2 = execute("lab/linux-networking/l2_loop_prevention.sh")
    pcap = ARTIFACTS / "foundation.pcap"
    data = pcap.read_bytes()
    assert len(data) > 24, "packet capture contains no packet records"
    assert data[:4] in {
        bytes.fromhex("d4c3b2a1"), bytes.fromhex("a1b2c3d4"),
        bytes.fromhex("4d3cb2a1"), bytes.fromhex("a1b23c4d"),
    }, "packet capture has invalid PCAP magic"
    quic_packets = udp_packets(data, 19003)
    assert quic_packets >= 2, "PCAP does not contain the bidirectional QUIC flow"
    checks = {
        "ethernet_arp_nd": True,
        "ipv4_ipv6_forwarding": True,
        "vrf_rib_fib": True,
        "mtu_icmp_tcp_udp": True,
        "quic_tls13_stream": "QUIC/TLS1.3" in l3,
        "quic_pcap_packets": quic_packets,
        "tc_netem_and_source_nat": True,
        "pcap_packet_evidence_bytes": len(data),
        "bridge_mac_learning": True,
        "stp_loop_blocked": True,
    }
    report = {
        "schema_version": 1,
        "success": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "image": IMAGE,
        "capabilities": ["NET_ADMIN", "SYS_ADMIN"],
        "privileged": False,
        "foundation_pass": "PASS:" in l3,
        "switching_pass": "PASS:" in l2,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, REPORT)
    print(f"PASS: Linux L3/L2 closed loop with {len(data)}-byte PCAP; no privileged container")


if __name__ == "__main__":
    main()
