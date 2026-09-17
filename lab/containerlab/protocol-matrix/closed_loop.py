#!/usr/bin/env python3
"""Prove dual-stack OSPF and real LDP/SR-MPLS forwarding across a failure."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


LAB = "clab-sf-protocol-matrix"
SOURCE = f"{LAB}-sat-a"
PRIMARY = f"{LAB}-sat-b"
BACKUP = f"{LAB}-sat-c"
GATEWAY = f"{LAB}-gw-a"
V4_PREFIX = "10.255.0.4/32"
V6_PREFIX = "2001:db8:ffff::4/128"
V4_TARGET = "10.255.0.4"
V6_TARGET = "2001:db8:ffff::4"
SR_TARGET = "203.0.113.4"
LDP_TARGET = "203.0.113.5"
LDP_PREFIX = f"{LDP_TARGET}/32"
SR_LABEL = 16004
CAPTURE_IMAGE = "ghcr.io/srl-labs/network-multitool@sha256:9bc1e46dd105a054bce724b6744200e951e8fdf4c17658912fbc54fd2e8a4b06"


class ProofError(RuntimeError):
    pass


def run(*args: str, check: bool = True, timeout: float = 15) -> str:
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise ProofError(f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}{result.stderr}")
    return result.stdout + result.stderr


def docker_exec(container: str, *args: str, check: bool = True, timeout: float = 15) -> str:
    return run("docker", "exec", container, *args, check=check, timeout=timeout)


def vty(container: str, command: str) -> str:
    return docker_exec(container, "vtysh", "-c", command)


def route_entry(prefix: str, ipv6: bool, protocol: str) -> dict | None:
    command = f"show {'ipv6' if ipv6 else 'ip'} route {prefix} json"
    routes = json.loads(vty(SOURCE, command)).get(prefix, [])
    for route in routes:
        if route.get("protocol") == protocol and route.get("installed") is True:
            return route
    return None


def route_interface(prefix: str, ipv6: bool, protocol: str) -> str | None:
    route = route_entry(prefix, ipv6, protocol)
    if route is None:
        return None
    for hop in route.get("nexthops", []):
        if hop.get("active") and hop.get("fib"):
            return hop.get("interfaceName")
    return None


def wait_until(predicate, timeout: float, description: str) -> float:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        try:
            if predicate():
                return (time.monotonic() - started) * 1000
        except (json.JSONDecodeError, KeyError, ProofError):
            pass
        time.sleep(0.02)
    raise ProofError(f"{description} was not observed within {timeout:.1f}s")


def full_neighbors(command: str) -> int:
    output = vty(SOURCE, command)
    return len(re.findall(r"\bFull\b", output, re.IGNORECASE))


def ldp_operational_neighbors() -> int:
    output = vty(SOURCE, "show mpls ldp neighbor")
    return len(re.findall(r"OPERATIONAL", output, re.IGNORECASE))


def ldp_remote_label(peer_id: str) -> int:
    output = vty(SOURCE, f"show mpls ldp ipv4 binding {LDP_PREFIX}")
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 5 and fields[0].lower() == "ipv4" and fields[1] == LDP_PREFIX and fields[2] == peer_id and fields[4].isdigit():
            return int(fields[4])
        if peer_id in line:
            match = re.search(rf"{re.escape(peer_id)}\s+\S+\s+(\d+)\b", line)
            if match:
                return int(match.group(1))
    raise ProofError(f"no numeric LDP label from {peer_id} for {LDP_PREFIX}:\n{output}")


def program_mpls(peer_id: str, next_hop: str, interface: str) -> int:
    ldp_label = ldp_remote_label(peer_id)
    docker_exec(SOURCE, "ip", "route", "replace", f"{SR_TARGET}/32", "encap", "mpls", str(SR_LABEL), "via", "inet", next_hop, "dev", interface)
    docker_exec(SOURCE, "ip", "route", "replace", f"{LDP_TARGET}/32", "encap", "mpls", str(ldp_label), "via", "inet", next_hop, "dev", interface)
    return ldp_label


def capture_label(observer: str, interface: str, target: str, expected_label: int) -> str:
    capture = subprocess.Popen(
        ["docker", "run", "--rm", "--network", f"container:{observer}",
         "--entrypoint", "tcpdump", CAPTURE_IMAGE,
         "-l", "-nn", "-i", interface, "-c", "1",
         "mpls", "and", "ip", "and", "dst", "host", target],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    time.sleep(0.35)
    try:
        docker_exec(SOURCE, "ping", "-n", "-I", "10.255.0.1", "-c", "2", "-W", "2", target, timeout=8)
        output, _ = capture.communicate(timeout=7)
    except Exception:
        capture.kill()
        output, _ = capture.communicate()
        raise
    if not re.search(rf"label\s+{expected_label}\b", output, re.IGNORECASE):
        raise ProofError(f"tcpdump did not observe MPLS label {expected_label} toward {target}:\n{output}")
    return output.strip()


def start_ping(ipv6: bool):
    command = ["docker", "exec", SOURCE, "ping"]
    if ipv6:
        command.append("-6")
    command.extend(["-n", "-i", "0.05", "-c", "160", "-W", "1", V6_TARGET if ipv6 else V4_TARGET])
    return subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def ping_loss(process) -> dict:
    output, _ = process.communicate(timeout=15)
    match = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received", output)
    if not match:
        raise ProofError(f"could not parse continuous ping:\n{output}")
    transmitted, received = map(int, match.groups())
    return {"transmitted": transmitted, "received": received, "lost": transmitted - received,
            "loss_percent": (transmitted - received) * 100 / transmitted}


def set_primary(up: bool):
    operation = "no shutdown" if up else "shutdown"
    for container, interface in ((SOURCE, "eth1"), (PRIMARY, "eth1")):
        docker_exec(container, "vtysh", "-c", "configure terminal", "-c", f"interface {interface}", "-c", operation, "-c", "end")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    checks = {}
    report = {"schema": 1, "measured_at": datetime.now(timezone.utc).isoformat(),
              "evidence_level": "single-host real FRR control plane, Linux FIB/LFIB and MPLS packet data plane",
              "fault": "administrative shutdown of both ends of the primary sat-a--sat-b link", "checks": checks}
    v4_ping = v6_ping = None
    error = None
    try:
        for container in (SOURCE, PRIMARY, BACKUP, GATEWAY):
            for daemon in ("ospfd", "ospf6d", "isisd", "ldpd", "zebra"):
                docker_exec(container, "pidof", daemon)
            docker_exec(container, "vtysh", "-C", "-f", "/etc/frr/frr.conf")
        checks["all_protocol_daemons_and_configs"] = True

        wait_until(lambda: full_neighbors("show ip ospf neighbor") == 2, 60, "two OSPFv2 Full neighbors")
        wait_until(lambda: full_neighbors("show ipv6 ospf6 neighbor") == 2, 60, "two OSPFv3 Full neighbors")
        wait_until(lambda: ldp_operational_neighbors() == 2, 60, "two LDP operational neighbors")
        wait_until(lambda: route_interface(V4_PREFIX, False, "ospf") == "eth1", 60, "primary OSPFv2 route")
        wait_until(lambda: route_interface(V6_PREFIX, True, "ospf6") == "eth1", 60, "primary OSPFv3 route")
        checks.update({"ospfv2_two_full_neighbors": True, "ospfv3_two_full_neighbors": True,
                       "ldp_two_operational_neighbors": True, "dual_stack_primary_fib": True})
        report["initial_control_plane"] = {
            "ospfv2": vty(SOURCE, "show ip ospf neighbor").strip(),
            "ospfv3": vty(SOURCE, "show ipv6 ospf6 neighbor").strip(),
            "isis": vty(SOURCE, "show isis neighbor").strip(),
            "ldp": vty(SOURCE, "show mpls ldp neighbor").strip(),
        }

        sr_state = vty(SOURCE, "show isis route")
        lfib = vty(SOURCE, "show mpls table")
        checks["sr_mpls_prefix_sid_16004"] = V4_PREFIX in sr_state and str(SR_LABEL) in sr_state and str(SR_LABEL) in lfib
        if not checks["sr_mpls_prefix_sid_16004"]:
            raise ProofError(f"SR-MPLS Prefix-SID/LFIB missing:\n{sr_state}\n{lfib}")

        initial_ldp = program_mpls("10.255.0.2", "10.0.12.1", "eth1")
        report["initial_labels"] = {"sr_mpls": SR_LABEL, "ldp": initial_ldp}
        report["initial_packet_evidence"] = {
            "sr_mpls": capture_label(PRIMARY, "eth1", SR_TARGET, SR_LABEL),
            "ldp": capture_label(PRIMARY, "eth1", LDP_TARGET, initial_ldp),
        }
        checks.update({"initial_sr_mpls_packets": True, "initial_ldp_mpls_packets": True})

        v4_ping, v6_ping = start_ping(False), start_ping(True)
        time.sleep(0.6)
        failure_started = time.monotonic()
        set_primary(False)
        v4_ms = wait_until(lambda: route_interface(V4_PREFIX, False, "ospf") == "eth2", 5, "OSPFv2 backup FIB")
        v6_ms = wait_until(lambda: route_interface(V6_PREFIX, True, "ospf6") == "eth2", 5, "OSPFv3 backup FIB")
        convergence_ms = (time.monotonic() - failure_started) * 1000
        report["failure"] = {"ospfv2_fib_ms": v4_ms, "ospfv3_fib_ms": v6_ms, "dual_stack_convergence_ms": convergence_ms}
        checks["dual_stack_convergence_under_3000ms"] = convergence_ms <= 3000

        recovered_ldp = program_mpls("10.255.0.3", "10.0.13.1", "eth2")
        report["recovered_labels"] = {"sr_mpls": SR_LABEL, "ldp": recovered_ldp}
        report["recovered_packet_evidence"] = {
            "sr_mpls": capture_label(BACKUP, "eth1", SR_TARGET, SR_LABEL),
            "ldp": capture_label(BACKUP, "eth1", LDP_TARGET, recovered_ldp),
        }
        checks.update({"recovered_sr_mpls_packets": True, "recovered_ldp_mpls_packets": True})
        report["traffic"] = {"ipv4": ping_loss(v4_ping), "ipv6": ping_loss(v6_ping)}
        v4_ping = v6_ping = None
        checks["dual_stack_ping_loss_under_20_percent"] = all(item["loss_percent"] <= 20 for item in report["traffic"].values())

        set_primary(True)
        restored_ms = wait_until(lambda: route_interface(V4_PREFIX, False, "ospf") == "eth1" and route_interface(V6_PREFIX, True, "ospf6") == "eth1", 30, "dual-stack primary restoration")
        report["recovery"] = {"dual_stack_primary_fib_ms": restored_ms}
        checks["primary_restored"] = True
        docker_exec(SOURCE, "ping", "-c", "2", "-W", "1", V4_TARGET)
        docker_exec(SOURCE, "ping", "-6", "-c", "2", "-W", "1", V6_TARGET)
    except Exception as exc:
        error = exc
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        for process in (v4_ping, v6_ping):
            if process is not None and process.poll() is None:
                process.terminate()
        set_primary(True)
        docker_exec(SOURCE, "ip", "route", "del", f"{SR_TARGET}/32", check=False)
        docker_exec(SOURCE, "ip", "route", "del", f"{LDP_TARGET}/32", check=False)

    report["success"] = error is None and bool(checks) and all(checks.values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if error is not None or not report["success"]:
        raise SystemExit("FAIL: protocol closed-loop checks did not all pass")


if __name__ == "__main__":
    main()
