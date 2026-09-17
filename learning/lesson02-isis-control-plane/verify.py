#!/usr/bin/env python3

import json
import re
import subprocess
import sys


LAB = "clab-sf-l02-isis"
ROUTERS = ("r1", "r2", "r3", "r4")
NODES = ("h1", *ROUTERS, "h2")
EXPECTED_NEIGHBORS = {
    "r1": {"r2", "r3"},
    "r2": {"r1", "r4"},
    "r3": {"r1", "r4"},
    "r4": {"r2", "r3"},
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


def check_containers() -> None:
    for name in NODES:
        state = run(
            "docker", "inspect", "--format", "{{.State.Status}}", node(name)
        ).strip()
        if state != "running":
            raise VerificationError(f"{node(name)} is {state}, not running")
    print("PASS: all six lab containers are running")


def check_configuration() -> None:
    for router in ROUTERS:
        logs = run("docker", "logs", node(router), check=False)
        for marker in ("Unknown command", "processing failure"):
            if marker in logs:
                raise VerificationError(f"{router} startup log contains: {marker}")
        docker_exec(router, "vtysh", "-C", "-f", "/etc/frr/frr.conf")
    print("PASS: all FRR configurations parse cleanly")


def check_management_isolation() -> None:
    for name in NODES:
        default_routes = json.loads(docker_exec(name, "ip", "-j", "route", "show", "default"))
        if default_routes:
            raise VerificationError(f"{name} still has a management-plane default route")
    print("PASS: the business data plane cannot escape through management defaults")


def check_neighbors_and_lsdb() -> None:
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
    print("PASS: every router has two Up adjacencies and a complete four-LSP LSDB")


def check_rib_and_fib() -> None:
    rib = json.loads(
        docker_exec("r1", "vtysh", "-c", "show ip route 198.51.100.0/24 json")
    )
    routes = rib.get("198.51.100.0/24", [])
    if len(routes) != 1:
        raise VerificationError("r1 does not have exactly one selected route to h2's LAN")
    route = routes[0]
    if not (
        route.get("protocol") == "isis"
        and route.get("selected") is True
        and route.get("installed") is True
        and route.get("metric") == 30
    ):
        raise VerificationError(f"unexpected r1 FRR RIB entry: {route}")

    active = {
        (hop.get("ip"), hop.get("interfaceName"))
        for hop in route.get("nexthops", [])
        if hop.get("active") is True and hop.get("fib") is True
    }
    if active != {("10.0.12.1", "eth2")}:
        raise VerificationError(f"unexpected active FRR nexthop(s): {sorted(active)}")

    fib = json.loads(
        docker_exec("r1", "ip", "-j", "route", "show", "198.51.100.0/24")
    )
    if len(fib) != 1:
        raise VerificationError(f"unexpected kernel FIB entry count: {len(fib)}")
    kernel_route = fib[0]
    if not (
        kernel_route.get("gateway") == "10.0.12.1"
        and kernel_route.get("dev") == "eth2"
        and kernel_route.get("protocol") == "isis"
    ):
        raise VerificationError(f"unexpected kernel FIB entry: {kernel_route}")
    print("PASS: IS-IS selected the metric-30 primary path in both FRR RIB and Linux FIB")


def check_data_plane() -> None:
    docker_exec("h1", "ping", "-c", "3", "-W", "1", "198.51.100.2")
    docker_exec("h2", "ping", "-c", "3", "-W", "1", "192.0.2.2")
    trace = docker_exec(
        "h1", "traceroute", "-n", "-m", "6", "-w", "1", "-q", "1", "198.51.100.2"
    )
    expected_hops = ["192.0.2.1", "10.0.12.1", "10.0.24.1", "198.51.100.2"]
    observed_hops = []
    for line in trace.splitlines():
        match = re.match(r"\s*\d+\s+(\d+\.\d+\.\d+\.\d+)", line)
        if match:
            observed_hops.append(match.group(1))
    if observed_hops != expected_hops:
        raise VerificationError(f"traceroute did not traverse r1 -> r2 -> r4 -> h2:\n{trace}")
    print("PASS: bidirectional traffic and traceroute use r1 -> r2 -> r4")


def main() -> int:
    try:
        check_containers()
        check_configuration()
        check_management_isolation()
        check_neighbors_and_lsdb()
        check_rib_and_fib()
        check_data_plane()
    except (VerificationError, json.JSONDecodeError, KeyError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: Lesson 02 healthy-state verification completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
