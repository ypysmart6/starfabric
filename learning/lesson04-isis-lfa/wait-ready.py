#!/usr/bin/env python3

import json
import subprocess
import sys
import time


R1 = "clab-sf-l04-lfa-r1"
PREFIX = "198.51.100.0/24"
PRIMARY = ("10.0.12.1", "eth2")
BACKUP = ("10.0.13.1", "eth3")
EXPECTED_PEERS = {"10.0.12.1", "10.0.13.1"}


def run(*args: str) -> str:
    result = subprocess.run(
        args,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def ready() -> bool:
    neighbors = run("docker", "exec", R1, "vtysh", "-c", "show isis neighbor json")
    peers = run("docker", "exec", R1, "vtysh", "-c", "show bfd peers brief json")
    route = run("docker", "exec", R1, "vtysh", "-c", f"show ip route {PREFIX} json")
    if not all((neighbors, peers, route)):
        return False

    neighbor_state = json.loads(neighbors)
    up_neighbors = {
        circuit["adj"]
        for area in neighbor_state.get("areas", [])
        for circuit in area.get("circuits", [])
        if circuit.get("state") == "Up"
    }
    up_peers = {
        peer.get("peer")
        for peer in json.loads(peers)
        if str(peer.get("status", "")).lower() == "up"
    }
    routes = json.loads(route).get(PREFIX, [])
    if len(routes) != 1:
        return False
    entry = routes[0]
    primary = {
        (hop.get("ip"), hop.get("interfaceName"))
        for hop in entry.get("nexthops", [])
        if hop.get("active") and hop.get("fib")
    }
    backups = {
        (hop.get("ip"), hop.get("interfaceName"))
        for hop in entry.get("backupNexthops", [])
        if hop.get("active")
    }
    return (
        up_neighbors == {"r2", "r3"}
        and up_peers == EXPECTED_PEERS
        and primary == {PRIMARY}
        and backups == {BACKUP}
        and entry.get("installed") is True
    )


def main() -> int:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if ready():
                print("PASS: IS-IS, BFD, primary FIB and precomputed LFA backup are ready")
                return 0
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        time.sleep(1)

    print("FAIL: the LFA topology did not become ready within 60 seconds", file=sys.stderr)
    logs = run("docker", "logs", R1)
    if logs:
        print(logs, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
