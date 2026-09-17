#!/usr/bin/env python3
"""Validate BGP EVPN Type-2 to Linux VXLAN/FDB forwarding and failover."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


LAB = "clab-sf-advanced-evpn"
VTEP_A = f"{LAB}-vtep-a"
VTEP_B = f"{LAB}-vtep-b"
HOST_A = f"{LAB}-host-a"
HOST_B = f"{LAB}-host-b"
REMOTE_LOOPBACK = "10.255.1.2"
REMOTE_MAC_A = "02:00:00:00:02:02"
REMOTE_MAC_B = "02:00:00:00:01:01"
TARGET = "192.168.50.2"
CAPTURE_IMAGE = "ghcr.io/srl-labs/network-multitool@sha256:9bc1e46dd105a054bce724b6744200e951e8fdf4c17658912fbc54fd2e8a4b06"


class ProofError(RuntimeError):
    pass


def run(*args: str, check: bool = True, timeout: float = 20) -> str:
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise ProofError(f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}{result.stderr}")
    return result.stdout + result.stderr


def dexec(container: str, *args: str, check: bool = True, timeout: float = 20) -> str:
    return run("docker", "exec", container, *args, check=check, timeout=timeout)


def vty(container: str, command: str) -> str:
    return dexec(container, "vtysh", "-c", command)


def wait_until(predicate, timeout: float, description: str) -> float:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        try:
            if predicate():
                return (time.monotonic() - started) * 1000
        except (ProofError, json.JSONDecodeError, KeyError, IndexError):
            pass
        time.sleep(0.05)
    raise ProofError(f"{description} was not observed within {timeout:.1f}s")


def evpn_established(container: str, peer: str) -> bool:
    state = json.loads(vty(container, "show bgp l2vpn evpn summary json"))
    return state.get("peers", {}).get(peer, {}).get("state") == "Established"


def vni_state(container: str) -> dict:
    return json.loads(vty(container, "show evpn vni 100 json"))


def type2_state(container: str) -> dict:
    return json.loads(vty(container, "show bgp l2vpn evpn route type macip json"))


def fdb(container: str) -> list[dict]:
    return json.loads(dexec(container, "bridge", "-j", "fdb", "show", "dev", "vni100"))


def remote_fdb_present(container: str, mac: str, destination: str) -> bool:
    return any(
        row.get("mac") == mac
        and row.get("dst") == destination
        and "extern_learn" in row.get("flags", [])
        for row in fdb(container)
    )


def route_interface() -> str | None:
    output = dexec(VTEP_A, "ip", "route", "get", REMOTE_LOOPBACK)
    match = re.search(r"\bdev\s+(\S+)", output)
    return match.group(1) if match else None


def set_primary(up: bool) -> None:
    operation = "no shutdown" if up else "shutdown"
    for container in (VTEP_A, VTEP_B):
        dexec(container, "vtysh", "-c", "configure terminal", "-c", "interface eth1", "-c", operation, "-c", "end", check=False)


def capture_vxlan(interface: str) -> str:
    process = subprocess.Popen(
        ["docker", "run", "--rm", "--network", f"container:{VTEP_A}",
         "--entrypoint", "tcpdump", CAPTURE_IMAGE,
         "-nn", "-l", "-i", interface, "-c", "1", "udp port 4789"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    time.sleep(0.4)
    try:
        dexec(HOST_A, "ping", "-n", "-c", "2", "-W", "2", TARGET)
        output, _ = process.communicate(timeout=8)
    except Exception:
        process.kill()
        process.communicate()
        raise
    if "4789" not in output and "VXLAN" not in output:
        raise ProofError(f"VXLAN UDP/4789 not observed on {interface}:\n{output}")
    return output.strip()


def start_ping():
    return subprocess.Popen(
        ["docker", "exec", HOST_A, "ping", "-n", "-i", "0.05", "-c", "160", "-W", "1", TARGET],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def finish_ping(process) -> dict:
    output, _ = process.communicate(timeout=15)
    match = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received", output)
    if not match:
        raise ProofError(f"cannot parse continuous VXLAN ping:\n{output}")
    transmitted, received = map(int, match.groups())
    return {
        "transmitted": transmitted,
        "received": received,
        "lost": transmitted - received,
        "loss_percent": (transmitted - received) * 100 / transmitted,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    checks: dict[str, bool] = {}
    report = {
        "schema": 1,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "evidence_level": "single-host FRR BGP EVPN control plane plus Linux bridge/VXLAN packet data plane",
        "fault": "administrative shutdown of both ends of the primary VTEP underlay link",
        "checks": checks,
    }
    ping = None
    error: Exception | None = None
    try:
        for container in (VTEP_A, VTEP_B):
            for daemon in ("zebra", "ospfd", "bgpd"):
                dexec(container, "pidof", daemon)
            dexec(container, "vtysh", "-C", "-f", "/etc/frr/frr.conf")
            dexec(container, "ip", "-d", "link", "show", "vni100")
        checks["frr_configs_daemons_and_vxlan_netdevs"] = True

        wait_until(lambda: route_interface() == "eth1", 35, "primary VTEP underlay route")
        wait_until(lambda: evpn_established(VTEP_A, "10.255.1.2"), 35, "BGP EVPN session")
        dexec(HOST_A, "ping", "-n", "-c", "3", "-W", "2", TARGET)
        wait_until(lambda: remote_fdb_present(VTEP_A, REMOTE_MAC_A, "10.255.1.2"), 15, "remote host-b MAC in VTEP-A FDB")
        wait_until(lambda: remote_fdb_present(VTEP_B, REMOTE_MAC_B, "10.255.1.1"), 15, "remote host-a MAC in VTEP-B FDB")

        vni_a = vni_state(VTEP_A)
        vni_b = vni_state(VTEP_B)
        type2_a = type2_state(VTEP_A)
        type2_b = type2_state(VTEP_B)
        initial_fdb_a = fdb(VTEP_A)
        initial_fdb_b = fdb(VTEP_B)
        initial_capture = capture_vxlan("eth1")
        checks["vni_100_and_remote_vteps_up"] = (
            vni_a.get("vni") == 100 and vni_a.get("numRemoteVteps") == 1
            and vni_b.get("vni") == 100 and vni_b.get("numRemoteVteps") == 1
        )
        checks["evpn_type2_both_host_macs"] = (
            type2_a.get("numPrefix", 0) >= 2
            and REMOTE_MAC_A in json.dumps(type2_a)
            and REMOTE_MAC_B in json.dumps(type2_b)
        )
        checks["evpn_programs_linux_remote_fdb"] = (
            remote_fdb_present(VTEP_A, REMOTE_MAC_A, "10.255.1.2")
            and remote_fdb_present(VTEP_B, REMOTE_MAC_B, "10.255.1.1")
        )
        checks["vxlan_primary_udp_4789_packet"] = True

        ping = start_ping()
        time.sleep(0.5)
        failure_started = time.monotonic()
        set_primary(False)
        switch_ms = wait_until(lambda: route_interface() == "eth2", 8, "backup VTEP underlay route")
        backup_capture = capture_vxlan("eth2")
        event_to_packet_ms = (time.monotonic() - failure_started) * 1000
        traffic = finish_ping(ping)
        ping = None
        checks["bgp_evpn_session_survives_underlay_fault"] = evpn_established(VTEP_A, "10.255.1.2")
        checks["remote_fdb_survives_underlay_fault"] = remote_fdb_present(VTEP_A, REMOTE_MAC_A, "10.255.1.2")
        checks["vxlan_backup_udp_4789_packet"] = True
        checks["vxlan_failure_loss_under_10_percent"] = traffic["loss_percent"] <= 10

        set_primary(True)
        restore_ms = wait_until(lambda: route_interface() == "eth1", 20, "primary VTEP underlay restoration")
        dexec(HOST_A, "ping", "-n", "-c", "2", "-W", "1", TARGET)
        checks["primary_underlay_and_overlay_restored"] = evpn_established(VTEP_A, "10.255.1.2")

        report.update({
            "control_plane": {
                "vtep_a_summary": json.loads(vty(VTEP_A, "show bgp l2vpn evpn summary json")),
                "vtep_a_vni": vni_a,
                "vtep_b_vni": vni_b,
                "type2_route_count": type2_a.get("numPrefix", 0),
                "type2_routes_vtep_a": type2_a,
            },
            "linux_data_plane": {
                "initial_fdb_vtep_a": initial_fdb_a,
                "initial_fdb_vtep_b": initial_fdb_b,
                "initial_packet_evidence": initial_capture,
                "backup_packet_evidence": backup_capture,
                "traffic": traffic,
            },
            "failure": {
                "underlay_fib_switch_ms": switch_ms,
                "event_to_captured_backup_packet_ms": event_to_packet_ms,
                "primary_restore_ms": restore_ms,
            },
        })
    except Exception as exc:
        error = exc
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if ping is not None and ping.poll() is None:
            ping.terminate()
        set_primary(True)

    report["success"] = error is None and bool(checks) and all(checks.values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["success"]:
        raise SystemExit("FAIL: EVPN/VXLAN closed-loop checks did not all pass")


if __name__ == "__main__":
    main()
