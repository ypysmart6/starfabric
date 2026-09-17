#!/usr/bin/env python3
"""Validate 5QI/S-NSSAI/DSCP policy and actual Linux packet classification."""

from __future__ import annotations

import json
import os
import struct
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "lab/qos/artifacts"
REPORT = ROOT / "reports/qos-slicing.json"
IMAGE = os.environ.get(
    "STARFABRIC_NETWORK_TOOL_IMAGE",
    "ghcr.io/srl-labs/network-multitool@sha256:9bc1e46dd105a054bce724b6744200e951e8fdf4c17658912fbc54fd2e8a4b06",
)


def dscp_counts(data: bytes, port: int) -> dict[int, int]:
    little = data[:4] in {bytes.fromhex("d4c3b2a1"), bytes.fromhex("4d3cb2a1")}
    order = "<" if little else ">"
    offset, counts = 24, {}
    while offset + 16 <= len(data):
        _, _, captured, _ = struct.unpack_from(order + "IIII", data, offset)
        offset += 16
        frame = data[offset : offset + captured]
        offset += captured
        if len(frame) < 42 or frame[12:14] != b"\x08\x00":
            continue
        ip = frame[14:]
        header = (ip[0] & 0x0F) * 4
        if ip[9] != 17 or len(ip) < header + 8:
            continue
        source, destination = struct.unpack_from("!HH", ip, header)
        if destination != port or source == port:
            continue
        dscp = ip[1] >> 2
        counts[dscp] = counts.get(dscp, 0) + 1
    return counts


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    command = [
        "docker", "run", "--rm", "--network=none", "--read-only",
        "--cap-add", "NET_ADMIN", "--cap-add", "SYS_ADMIN",
        "--security-opt", "apparmor=unconfined",
        "--tmpfs", "/run:rw,nosuid,nodev", "--tmpfs", "/tmp:rw,nosuid,nodev",
        "--entrypoint", "/bin/bash",
        "-v", f"{ROOT}:/work:ro", "-v", f"{ARTIFACTS}:/artifacts",
        "-e", "SF_QOS_PCAP=/artifacts/qos.pcap", "-w", "/work",
        IMAGE, "lab/qos/run.sh",
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=120, check=False)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and "PASS:" in output, output
    stats_line = next(line for line in output.splitlines() if line.startswith("TC_JSON="))
    classes = {item["handle"]: item for item in json.loads(stats_line.removeprefix("TC_JSON="))}
    assert classes["1:10"]["stats"]["packets"] >= 100, classes["1:10"]
    assert classes["1:20"]["stats"]["packets"] >= 100, classes["1:20"]
    pcap = ARTIFACTS / "qos.pcap"
    counts = dscp_counts(pcap.read_bytes(), 19100)
    assert counts.get(46, 0) >= 100 and counts.get(26, 0) >= 100, counts

    mapping = json.loads((ROOT / "ntn/qos-mapping.json").read_text(encoding="utf-8"))
    assert mapping["schema_version"] == 1 and len(mapping["mappings"]) == 2
    assert {entry["dscp"] for entry in mapping["mappings"]} == {26, 46}
    assert {entry["five_qi"] for entry in mapping["mappings"]} == {5, 9}
    slices = {(entry["snssai"]["sst"], entry["snssai"].get("sd")) for entry in mapping["mappings"]}
    assert slices == {(1, None), (2, "000001")}
    amf = (ROOT / "ntn/open5gs/amf.yaml").read_text(encoding="utf-8")
    gnb = (ROOT / "ntn/srsran/gnb_transport.yml").read_text(encoding="utf-8")
    assert "sst: 1" in amf and "sst: 2" in amf and 'sd: "000001"' in amf
    assert "sst: 1" in gnb and "sst: 2" in gnb and "sd: 1" in gnb

    report = {
        "schema_version": 1,
        "success": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "snssai_policy_mapping": True,
            "five_qi_policy_mapping": True,
            "open5gs_slice_support": True,
            "srsran_slice_support": True,
            "dscp_on_wire_pcap": True,
            "linux_tc_ef_class": True,
            "linux_tc_af31_class": True,
            "application_acknowledgements": True,
        },
        "dscp_packets": {str(key): value for key, value in sorted(counts.items())},
        "tc_packets": {key: classes[key]["stats"]["packets"] for key in ["1:10", "1:20"]},
        "pcap_bytes": pcap.stat().st_size,
        "image": IMAGE,
        "privileged": False,
    }
    temporary = REPORT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, REPORT)
    print(f"PASS: S-NSSAI/5QI policy to {sum(counts.values())} on-wire DSCP packets and HTB classes")


if __name__ == "__main__":
    main()
