#!/usr/bin/env python3

import json
import re
import subprocess
import sys


LAB = "clab-sf-l04-lfa"
ROUTERS = ("r1", "r2", "r3", "r4")
NODES = ("h1", *ROUTERS, "h2")
PREFIX = "198.51.100.0/24"
EXPECTED_NEIGHBORS = {
    "r1": {"r2", "r3"},
    "r2": {"r1", "r4"},
    "r3": {"r1", "r4"},
    "r4": {"r2", "r3"},
}
EXPECTED_BFD_PEERS = {
    "r1": {"10.0.12.1", "10.0.13.1"},
    "r2": {"10.0.12.0", "10.0.24.1"},
    "r3": {"10.0.13.0", "10.0.34.1"},
    "r4": {"10.0.24.0", "10.0.34.0"},
}


class VerificationError(RuntimeError):
    pass


def run(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        args,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if check and result.returncode != 0:
        raise VerificationError(
            f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}"
        )
    return result.stdout


def node(name: str) -> str:
    return f"{LAB}-{name}"


def docker_exec(name: str, *args: str) -> str:
    return run("docker", "exec", node(name), *args)


def route_entry(router: str, prefix: str) -> dict[str, object]:
    rib = json.loads(
        docker_exec(router, "vtysh", "-c", f"show ip route {prefix} json")
    )
    routes = rib.get(prefix, [])
    if len(routes) != 1:
        raise VerificationError(
            f"{router} has {len(routes)} selected entries for {prefix}, expected one"
        )
    return routes[0]


def check_containers_and_daemons() -> None:
    for name in NODES:
        state = run(
            "docker", "inspect", "--format", "{{.State.Status}}", node(name)
        ).strip()
        if state != "running":
            raise VerificationError(f"{node(name)} is {state}, not running")
    for router in ROUTERS:
        docker_exec(router, "pidof", "isisd")
        docker_exec(router, "pidof", "bfdd")
    print("PASS: all six containers are running and every router has isisd + bfdd")


def check_configuration() -> None:
    for router in ROUTERS:
        logs = run("docker", "logs", node(router), check=False)
        for marker in ("Unknown command", "processing failure"):
            if marker in logs:
                raise VerificationError(f"{router} startup log contains: {marker}")
        docker_exec(router, "vtysh", "-C", "-f", "/etc/frr/frr.conf")

    running = docker_exec("r1", "vtysh", "-c", "show running-config")
    if "isis fast-reroute lfa level-2" not in running:
        raise VerificationError("r1 eth2 does not enable Level-2 classic LFA")
    print("PASS: all FRR configurations parse and r1 eth2 enables Level-2 classic LFA")


def check_management_isolation() -> None:
    for name in NODES:
        defaults = json.loads(docker_exec(name, "ip", "-j", "route", "show", "default"))
        if defaults:
            raise VerificationError(f"{name} still has a management-plane default route")
    print("PASS: the business data plane cannot escape through management defaults")


def check_neighbors_lsdb_and_bfd() -> None:
    for router, expected in EXPECTED_NEIGHBORS.items():
        state = json.loads(
            docker_exec(router, "vtysh", "-c", "show isis neighbor json")
        )
        up = {
            circuit["adj"]
            for area in state["areas"]
            for circuit in area["circuits"]
            if circuit.get("state") == "Up"
        }
        if up != expected:
            raise VerificationError(
                f"{router} neighbors are {sorted(up)}, expected {sorted(expected)}"
            )
        database = docker_exec(router, "vtysh", "-c", "show isis database")
        if "4 LSPs" not in database:
            raise VerificationError(f"{router} does not have the complete four-LSP LSDB")

        peers = json.loads(
            docker_exec(router, "vtysh", "-c", "show bfd peers brief json")
        )
        actual = {peer.get("peer") for peer in peers}
        if actual != EXPECTED_BFD_PEERS[router]:
            raise VerificationError(
                f"{router} BFD peers are {sorted(actual)}, "
                f"expected {sorted(EXPECTED_BFD_PEERS[router])}"
            )
        for peer in peers:
            if str(peer.get("status", "")).lower() != "up":
                raise VerificationError(f"{router} BFD peer is not Up: {peer}")
            if peer.get("profile") != "FAST":
                raise VerificationError(f"{router} BFD peer does not use FAST: {peer}")
    print("PASS: IS-IS adjacencies, four-LSP LSDB and all eight BFD sessions are healthy")


def check_lfa_protection() -> None:
    summary = docker_exec(
        "r1", "vtysh", "-c", "show isis fast-reroute summary level-2"
    )
    match = re.search(r"Classic LFA\s+(?:\d+\s+){4}(\d+)", summary)
    if not match or int(match.group(1)) == 0:
        raise VerificationError(f"r1 reports no classic-LFA-protected prefixes:\n{summary}")

    route = route_entry("r1", PREFIX)
    if not (
        route.get("protocol") == "isis"
        and route.get("selected") is True
        and route.get("installed") is True
        and route.get("metric") == 30
    ):
        raise VerificationError(f"unexpected protected route: {route}")

    primary = {
        (hop.get("ip"), hop.get("interfaceName"))
        for hop in route.get("nexthops", [])
        if hop.get("active") is True and hop.get("fib") is True
    }
    backups = {
        (hop.get("ip"), hop.get("interfaceName"))
        for hop in route.get("backupNexthops", [])
        if hop.get("active") is True
    }
    if primary != {("10.0.12.1", "eth2")}:
        raise VerificationError(f"unexpected primary nexthop(s): {sorted(primary)}")
    if backups != {("10.0.13.1", "eth3")}:
        raise VerificationError(f"unexpected LFA backup nexthop(s): {sorted(backups)}")
    if not any(hop.get("backupIndex") == [0] for hop in route.get("nexthops", [])):
        raise VerificationError("primary nexthop does not reference backup index 0")

    group_id = route.get("installedNexthopGroupId")
    if not isinstance(group_id, int):
        raise VerificationError(f"protected route lacks an installed nexthop group: {route}")
    group = docker_exec(
        "r1", "vtysh", "-c", f"show nexthop-group rib {group_id}"
    )
    for evidence in ("Installed", "Backups:", "via 10.0.13.1, eth3"):
        if evidence not in group:
            raise VerificationError(
                f"nexthop group {group_id} lacks {evidence!r}:\n{group}"
            )

    backup_table = docker_exec(
        "r1", "vtysh", "-c", "show isis route level-2 backup"
    )
    if not re.search(
        r"198\.51\.100\.0/24\s+50\s+eth3\s+10\.0\.13\.1", backup_table
    ):
        raise VerificationError(
            f"IS-IS backup table lacks the expected metric-50 LFA:\n{backup_table}"
        )
    print("PASS: h2 LAN has primary r2 plus a precomputed and installed r3 LFA backup")


def check_destination_specific_coverage() -> None:
    direct_neighbor = route_entry("r1", "10.255.0.2/32")
    if direct_neighbor.get("backupNexthops"):
        raise VerificationError(
            "r2 loopback unexpectedly has an LFA; the lesson's strict-inequality case changed"
        )
    print("PASS: r2 loopback is unprotected, proving LFA eligibility is per-prefix")


def check_data_plane() -> None:
    docker_exec("h1", "ping", "-c", "3", "-W", "1", "198.51.100.2")
    docker_exec("h2", "ping", "-c", "3", "-W", "1", "192.0.2.2")
    trace = docker_exec(
        "h1", "traceroute", "-n", "-m", "6", "-w", "1", "-q", "1", "198.51.100.2"
    )
    expected = ["192.0.2.1", "10.0.12.1", "10.0.24.1", "198.51.100.2"]
    observed = []
    for line in trace.splitlines():
        match = re.match(r"\s*\d+\s+(\d+\.\d+\.\d+\.\d+)", line)
        if match:
            observed.append(match.group(1))
    if observed != expected:
        raise VerificationError(f"healthy traffic did not use r1 -> r2 -> r4:\n{trace}")
    print("PASS: healthy traffic still uses the metric-30 primary path through r2")


def main() -> int:
    try:
        check_containers_and_daemons()
        check_configuration()
        check_management_isolation()
        check_neighbors_lsdb_and_bfd()
        check_lfa_protection()
        check_destination_specific_coverage()
        check_data_plane()
    except (VerificationError, json.JSONDecodeError, KeyError, TypeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: Lesson 04 healthy-state verification completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
