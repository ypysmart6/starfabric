#!/usr/bin/env python3
"""Validate real SRv6, PCEP and BGP-LS behavior across one link failure."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


LAB = "clab-sf-advanced-control"
HEAD = f"{LAB}-head"
PRIMARY = f"{LAB}-primary"
BACKUP = f"{LAB}-backup"
TAIL = f"{LAB}-tail"
PCE = f"{LAB}-pce"
SERVICE = "2001:db8:feed::1"
SOURCE = "2001:db8:ffff::1"
PRIMARY_SID = "fc00:0:2::1"
BACKUP_SID = "fc00:0:3::1"
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


def isis_neighbors() -> int:
    return len(re.findall(r"\bUp\b", vty(HEAD, "show isis neighbor")))


def srv6_nodes() -> str:
    return vty(HEAD, "show isis segment-routing srv6 node")


def four_srv6_nodes_ready() -> bool:
    output = srv6_nodes()
    return len(re.findall(r"0000\.0000\.000[1-4]", output)) == 4 and "2001:db8:feed::1/128" in vty(HEAD, "show isis route")


def ted_ready() -> bool:
    output = vty(HEAD, "show isis mpls-te database detail")
    expected = {f"10.0.{link}.{end}" for link in (12, 13, 24, 34) for end in (0, 1)}
    observed = set(re.findall(r"^\s*Local IPv4 address: (\S+)", output, re.MULTILINE))
    # Counting unconnected IPv6 edges previously let an incomplete TED pass.
    return expected <= observed and "- (0.0.0.0)" not in output


def bgpls(container: str = PCE) -> dict:
    return json.loads(vty(container, "show bgp link-state link-state json"))


def bgpls_peer(container: str, address: str) -> dict:
    return json.loads(vty(container, f"show bgp neighbors {address} json")).get(address, {})


def bgpls_ready(container: str) -> bool:
    counts = nlri_counts(bgpls(container))
    return (counts.get("node", 0) >= 4 and counts.get("link", 0) >= 8
            and counts.get("ipv4Prefix", 0) >= 1 and counts.get("ipv6Prefix", 0) >= 1)


def configure_bgpls() -> None:
    # vtysh -f is a daemon-wide configuration-load transaction.  Loading a
    # BGP-only fragment that way can race/reset the running IS-IS TED export.
    # Apply this incremental change through the interactive command path.
    commands = ["configure terminal"]
    for line in (Path(__file__).parent / "configs/bgpls-head.conf").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("!"):
            commands.append(line)
    commands.append("end")
    dexec(HEAD, "vtysh", *(value for command in commands for value in ("-c", command)))


def bgpls_keys() -> set[str]:
    return set(bgpls().get("routes", {}))


def nlri_counts(state: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for paths in state.get("routes", {}).values():
        for path in paths:
            kind = path.get("nlri", {}).get("nlriType", "unknown")
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def pcep_session() -> dict:
    state = json.loads(vty(HEAD, "show sr-te pcep session json"))
    sessions = state.get("pcepSessions", [])
    return sessions[0] if sessions else {}


def configure_pathd() -> None:
    # pathd is intentionally attached only after Zebra is ready.  FRR's PCEP
    # module is dynamically loaded and receives its transaction via vtysh.
    run("docker", "exec", "-d", HEAD, "sh", "-c",
        "/usr/lib/frr/pathd -F traditional -M pathd_pcep --log stdout > /tmp/pathd.log 2>&1")
    wait_until(lambda: bool(dexec(HEAD, "pidof", "pathd", check=False).strip()), 10, "pathd start")
    dexec(
        HEAD, "vtysh",
        "-c", "configure terminal",
        "-c", "segment-routing",
        "-c", "traffic-eng",
        "-c", "pcep",
        "-c", "pce LAB-PCE",
        "-c", "address ip 10.0.15.1",
        "-c", "exit",
        "-c", "pcc",
        "-c", "peer LAB-PCE precedence 10",
        "-c", "end",
    )
    wait_until(lambda: pcep_session().get("sessionStatus") == "UP", 35, "PCEP session UP")
    dexec(
        HEAD, "vtysh",
        "-c", "configure terminal",
        "-c", "segment-routing",
        "-c", "traffic-eng",
        "-c", "policy color 100 endpoint 10.255.0.4",
        "-c", "name PCEP-COMPUTED-PATH",
        "-c", "candidate-path preference 200 name REMOTE dynamic",
        "-c", "end",
    )


def program_srv6(primary: bool) -> str:
    sid = PRIMARY_SID if primary else BACKUP_SID
    via = "2001:db8:12::2" if primary else "2001:db8:13::2"
    interface = "eth1" if primary else "eth2"
    dexec(
        HEAD, "ip", "-6", "route", "replace", f"{SERVICE}/128",
        "encap", "seg6", "mode", "inline", "segs", sid,
        "via", via, "dev", interface,
    )
    return dexec(HEAD, "ip", "-6", "route", "show", f"{SERVICE}/128").strip()


def srv6_ping_succeeds() -> bool:
    output = dexec(
        HEAD, "ping", "-6", "-n", "-I", SOURCE, "-c", "1", "-W", "1", SERVICE,
        check=False,
    )
    return "1 packets received" in output


def capture_srh(observer: str, interface: str) -> str:
    process = subprocess.Popen(
        ["docker", "run", "--rm", "--network", f"container:{observer}",
         "--entrypoint", "tcpdump", CAPTURE_IMAGE,
         "-nn", "-l", "-i", interface, "-c", "1", "ip6 and ip6[6] == 43"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    time.sleep(0.4)
    try:
        dexec(HEAD, "ping", "-6", "-n", "-I", SOURCE, "-c", "2", "-W", "2", SERVICE)
        output, _ = process.communicate(timeout=8)
    except Exception:
        process.kill()
        process.communicate()
        raise
    if "type=4" not in output or "segleft=" not in output:
        raise ProofError(f"SRH type 4 not observed on {observer}:{interface}:\n{output}")
    return output.strip()


def set_primary(up: bool) -> None:
    operation = "no shutdown" if up else "shutdown"
    for container in (HEAD, PRIMARY):
        dexec(container, "vtysh", "-c", "configure terminal", "-c", "interface eth1", "-c", operation, "-c", "end", check=False)


def start_ping():
    return subprocess.Popen(
        ["docker", "exec", HEAD, "ping", "-6", "-n", "-I", SOURCE,
         "-i", "0.05", "-c", "160", "-W", "1", SERVICE],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def finish_ping(process) -> dict:
    output, _ = process.communicate(timeout=15)
    match = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received", output)
    if not match:
        raise ProofError(f"cannot parse continuous SRv6 ping:\n{output}")
    transmitted, received = map(int, match.groups())
    return {
        "transmitted": transmitted,
        "received": received,
        "lost": transmitted - received,
        "loss_percent": (transmitted - received) * 100 / transmitted,
    }


def pcep_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pcep-events", required=True, type=Path)
    args = parser.parse_args()
    checks: dict[str, bool] = {}
    report = {
        "schema": 1,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "evidence_level": "single-host FRR control plane plus Linux SRv6 packet data plane",
        "fault": "administrative shutdown of both ends of the primary head--primary link",
        "checks": checks,
    }
    ping = None
    error: Exception | None = None
    try:
        for container in (HEAD, PRIMARY, BACKUP, TAIL, PCE):
            dexec(container, "pidof", "zebra")
            dexec(container, "vtysh", "-C", "-f", "/etc/frr/frr.conf")
        checks["base_frr_configs_and_daemons"] = True

        wait_until(lambda: isis_neighbors() == 2, 40, "two head IS-IS neighbors")
        wait_until(four_srv6_nodes_ready, 40, "four-node IS-IS SRv6 database and tail service route")
        wait_until(ted_ready, 40, "eight connected IPv4 IS-IS TED edges")
        checks["ted_has_eight_connected_ipv4_edges"] = True
        for container, sid in ((PRIMARY, PRIMARY_SID), (BACKUP, BACKUP_SID)):
            dexec(container, "ip", "-6", "route", "replace", "local", f"{sid}/128",
                  "encap", "seg6local", "action", "End", "dev", "sr0")
        locator = json.loads(vty(HEAD, "show segment-routing srv6 locator json"))
        nodes = srv6_nodes()
        checks["srv6_locator_up"] = bool(locator.get("locators", [{}])[0].get("statusUp"))
        checks["isis_advertises_four_srv6_nodes"] = len(re.findall(r"0000\.0000\.000[1-4]", nodes)) == 4

        initial_route = program_srv6(True)
        initial_capture = capture_srh(PRIMARY, "eth1")
        checks["srv6_primary_srh_packet"] = PRIMARY_SID in initial_capture

        # Register BGP-LS only after the IS-IS TED contains all lab edges.  The
        # pinned FRR build otherwise can snapshot nodes before link data exists.
        configure_bgpls()
        wait_until(
            lambda: bgpls_peer(HEAD, "10.0.15.1").get("bgpState") == "Established"
            and bgpls_peer(PCE, "10.0.15.0").get("bgpState") == "Established",
            30, "BGP-LS session Established on both endpoints",
        )
        checks["bgpls_session_established_both_ends"] = True
        wait_until(lambda: bgpls_ready(HEAD), 30, "complete IS-IS topology in producer BGP-LS RIB")
        checks["bgpls_producer_has_complete_ted"] = True
        wait_until(lambda: bgpls_ready(PCE), 30, "BGP-LS Node/Link/IPv4/IPv6 NLRI at collector")
        producer_before = bgpls(HEAD)
        peer_before = {"producer": bgpls_peer(HEAD, "10.0.15.1"),
                       "collector": bgpls_peer(PCE, "10.0.15.0")}
        before = bgpls()
        before_keys = set(before.get("routes", {}))
        before_counts = nlri_counts(before)
        checks["bgpls_real_ebgp_collection"] = all(before_counts.get(kind, 0) > 0 for kind in ("node", "link", "ipv4Prefix", "ipv6Prefix"))

        args.pcep_events.parent.mkdir(parents=True, exist_ok=True)
        args.pcep_events.unlink(missing_ok=True)
        run("docker", "exec", "-d", PCE, "python3", "/opt/advanced/pcep_server.py",
            "--listen", "10.0.15.1", "--output", "/var/log/advanced/pcep-events.jsonl")
        wait_until(lambda: any(row.get("event") == "listening" for row in pcep_events(args.pcep_events)), 8, "PCE listener")
        configure_pathd()
        wait_until(
            lambda: pcep_session().get("messageStatisticsSent", {}).get("messagePcReq", 0) >= 1
            and pcep_session().get("messageStatisticsReceived", {}).get("messagePcRep", 0) >= 1,
            15,
            "PCEP PCReq/PCRep exchange",
        )
        pcep_before_fault = pcep_session()
        events = pcep_events(args.pcep_events)
        checks["pcep_open_keepalive_session_up"] = pcep_before_fault.get("sessionStatus") == "UP"
        checks["pcep_real_pcreq_pcrep_objects"] = (
            any(row.get("message_type") == 3 and 2 in row.get("object_classes", []) and 4 in row.get("object_classes", []) for row in events)
            and any(row.get("event") == "pc_rep_no_path_sent" for row in events)
        )

        ping = start_ping()
        time.sleep(0.5)
        failure_started = time.monotonic()
        set_primary(False)
        change_ms = wait_until(lambda: bgpls_keys() != before_keys, 12, "BGP-LS withdrawal")
        after = bgpls()
        after_keys = set(after.get("routes", {}))
        withdrawn = before_keys - after_keys
        restoration_targets = {key for key in withdrawn if "p10.0.12.0/31" in key}
        backup_route = program_srv6(False)
        data_plane_ms = wait_until(
            lambda: dexec(HEAD, "ping", "-6", "-n", "-I", SOURCE, "-c", "1", "-W", "1", SERVICE, check=False).find("1 packets received") >= 0,
            4,
            "SRv6 backup forwarding",
        )
        event_to_forwarding_ms = (time.monotonic() - failure_started) * 1000
        backup_capture = capture_srh(BACKUP, "eth1")
        traffic = finish_ping(ping)
        ping = None
        checks["bgpls_withdraws_failed_adjacency_nlri"] = (
            bool(restoration_targets)
            and after.get("totalRoutes", 0) < before.get("totalRoutes", 0)
        )
        checks["srv6_backup_srh_packet"] = BACKUP_SID in backup_capture
        checks["srv6_failure_loss_under_20_percent"] = traffic["loss_percent"] <= 20
        checks["pcep_session_survives_data_link_fault"] = pcep_session().get("sessionStatus") == "UP"

        set_primary(True)
        restore_ms = wait_until(
            lambda: restoration_targets.issubset(bgpls_keys()) and isis_neighbors() == 2,
            25,
            "BGP-LS failed-adjacency NLRI and IS-IS neighbor restoration",
        )
        dexec(PRIMARY, "ip", "-6", "route", "replace", "local", f"{PRIMARY_SID}/128",
              "encap", "seg6local", "action", "End", "dev", "sr0")
        restored_route = program_srv6(True)
        wait_until(srv6_ping_succeeds, 8, "restored primary SRv6 forwarding")
        dexec(HEAD, "ping", "-6", "-n", "-I", SOURCE, "-c", "2", "-W", "1", SERVICE)
        checks["bgpls_and_srv6_primary_restored"] = True

        report.update({
            "srv6": {
                "locator": locator,
                "isis_nodes": nodes.strip(),
                "initial_kernel_route": initial_route,
                "initial_packet_evidence": initial_capture,
                "backup_kernel_route": backup_route,
                "backup_packet_evidence": backup_capture,
                "restored_kernel_route": restored_route,
                "traffic": traffic,
                "event_to_forwarding_ms": event_to_forwarding_ms,
                "backup_probe_ms": data_plane_ms,
            },
            "bgp_ls": {
                "peers": peer_before,
                "producer_initial_nlri_types": nlri_counts(producer_before),
                "producer_initial_rib": producer_before,
                "collector_initial_rib": before,
                "collector_fault_rib": after,
                "collector_restored_rib": bgpls(),
                "initial_total_routes": before.get("totalRoutes", 0),
                "initial_nlri_types": before_counts,
                "fault_total_routes": after.get("totalRoutes", 0),
                "withdrawn_nlri": sorted(withdrawn),
                "restoration_targets": sorted(restoration_targets),
                "new_nlri": sorted(after_keys - before_keys),
                "collector_change_ms": change_ms,
                "restore_ms": restore_ms,
            },
            "pcep": {
                "session": pcep_before_fault,
                "events": events,
                "computed_result": "NO-PATH (intentional negative PCRep; policy remains inactive)",
                "pathd_log_tail": "\n".join(dexec(HEAD, "sh", "-c", "tail -30 /tmp/pathd.log", check=False).splitlines()[-30:]),
            },
        })
    except Exception as exc:
        error = exc
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["diagnostics"] = {}
        for name, container, command in (
            ("ted", HEAD, "show isis mpls-te database detail"),
            ("neighbors", HEAD, "show isis neighbor"),
            ("producer_bgp_neighbor", HEAD, "show bgp neighbors 10.0.15.1 json"),
            ("collector_bgp_neighbor", PCE, "show bgp neighbors 10.0.15.0 json"),
            ("producer", HEAD, "show bgp link-state link-state json"),
            ("zebra_clients", HEAD, "show zebra client summary"),
            ("collector", PCE, "show bgp link-state link-state json"),
        ):
            try:
                report["diagnostics"][name] = vty(container, command)
            except Exception as diagnostic_error:
                report["diagnostics"][name] = str(diagnostic_error)
    finally:
        if ping is not None and ping.poll() is None:
            ping.terminate()
        set_primary(True)
        dexec(HEAD, "ip", "-6", "route", "del", f"{SERVICE}/128", check=False)

    report["success"] = error is None and bool(checks) and all(checks.values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["success"]:
        raise SystemExit("FAIL: SRv6/PCEP/BGP-LS closed-loop checks did not all pass")


if __name__ == "__main__":
    main()
