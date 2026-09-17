#!/usr/bin/env python3
"""Verify a live single-PC Open5GS/UERANSIM N2/N3 data path."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path


CONTAINERS = (
    "mongo",
    "webui",
    "nrf",
    "scp",
    "ausf",
    "udr",
    "udm",
    "pcf",
    "bsf",
    "amf",
    "smf",
    "upf",
    "nr_gnb",
    "nr_ue",
)


def command(*args, check=True, timeout=15):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"{' '.join(args)} failed: {detail}")
    return result


def container_ip(name):
    result = command(
        "docker",
        "inspect",
        name,
        "--format",
        "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
    )
    return result.stdout.strip()


def container_logs(name):
    result = command("docker", "logs", name, check=False)
    return result.stdout + result.stderr


def capture_gtpu_and_ping():
    capture = subprocess.Popen(
        ["docker", "exec", "upf", "tcpdump", "-l", "-nn", "-i", "eth0", "-c", "4", "udp", "port", "2152"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    time.sleep(0.75)
    ping = command("docker", "exec", "nr_ue", "ping", "-I", "uesimtun0", "-c", "2", "-W", "2", "192.168.100.1", check=False)
    try:
        capture_output, _ = capture.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        capture.kill()
        capture_output, _ = capture.communicate()
    return ping, capture.returncode, capture_output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/5g-sa-baseline.json"))
    args = parser.parse_args()

    states = {}
    for name in CONTAINERS:
        result = command("docker", "inspect", name, "--format", "{{.State.Running}}", check=False)
        states[name] = result.returncode == 0 and result.stdout.strip() == "true"

    gnb_logs = container_logs("nr_gnb") if states["nr_gnb"] else ""
    ue_logs = container_logs("nr_ue") if states["nr_ue"] else ""
    smf_logs = container_logs("smf") if states["smf"] else ""
    tunnel = command("docker", "exec", "nr_ue", "ip", "-brief", "addr", "show", "uesimtun0", check=False)
    gnb_ip = container_ip("nr_gnb") if states["nr_gnb"] else ""
    upf_ip = container_ip("upf") if states["upf"] else ""

    ping, capture_code, capture_output = capture_gtpu_and_ping() if all(states.values()) else (None, 1, "")
    packet_pairs = re.findall(r"IP (\d+\.\d+\.\d+\.\d+)\.2152 > (\d+\.\d+\.\d+\.\d+)\.2152", capture_output)
    directions = set(packet_pairs)
    checks = {
        "all_containers_running": all(states.values()),
        "n2_ng_setup": "NG Setup procedure is successful" in gnb_logs,
        "pfcp_associated": "PFCP associated" in smf_logs,
        "ue_registered": "MM-REGISTERED/NORMAL-SERVICE" in ue_logs and "Initial Registration is successful" in ue_logs,
        "pdu_session_established": "PDU Session establishment is successful" in ue_logs,
        "ue_tunnel_up": tunnel.returncode == 0 and "192.168.100." in tunnel.stdout,
        "ue_to_upf_ping": ping is not None and ping.returncode == 0 and re.search(r"\b0% packet loss", ping.stdout) is not None,
        "gtpu_uplink": (gnb_ip, upf_ip) in directions,
        "gtpu_downlink": (upf_ip, gnb_ip) in directions,
        "gtpu_capture_clean": capture_code == 0 and "0 packets dropped by kernel" in capture_output,
    }
    report = {
        "success": all(checks.values()),
        "evidence_level": "single-host 5G SA protocol and packet data plane",
        "scope": "Open5GS core plus UERANSIM gNB/UE; RF and Release 17 NTN PHY are not claimed",
        "components": {"open5gs": "v2.8.0-48-gf87da61", "ueransim": "v3.2.6"},
        "addresses": {"gnb_n3": gnb_ip, "upf_n3": upf_ip, "ue_tunnel": tunnel.stdout.strip()},
        "checks": checks,
        "ping": ping.stdout.strip() if ping else None,
        "gtpu_packets": [f"{source}:2152 > {target}:2152" for source, target in packet_pairs],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["success"]:
        raise SystemExit("FAIL: 5G SA N2/N3 acceptance checks did not all pass")


if __name__ == "__main__":
    main()
