#!/usr/bin/env python3

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import subprocess
import sys
import time


LAB = "clab-sf-l03-bfd"
H1 = f"{LAB}-h1"
R1 = f"{LAB}-r1"
R2 = f"{LAB}-r2"
DESTINATION = "198.51.100.2"
PREFIX = "198.51.100.0/24"
PRIMARY = "10.0.12.1"
BACKUP = "10.0.13.1"
REPORT = Path(__file__).resolve().parent / "reports" / "latest.json"


class ExperimentError(RuntimeError):
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
        raise ExperimentError(
            f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}"
        )
    return result.stdout


def route_nexthop() -> str | None:
    output = run("docker", "exec", R1, "vtysh", "-c", f"show ip route {PREFIX} json")
    routes = json.loads(output).get(PREFIX, [])
    for route in routes:
        if not route.get("installed"):
            continue
        for hop in route.get("nexthops", []):
            if hop.get("active") and hop.get("fib"):
                return hop.get("ip")
    return None


def bfd_primary_is_down_or_removed() -> bool:
    output = run("docker", "exec", R1, "vtysh", "-c", "show bfd peers brief json")
    peers = json.loads(output)
    primary = next((peer for peer in peers if peer.get("peer") == PRIMARY), None)
    return primary is None or str(primary.get("status", "")).lower() != "up"


def bfd_primary_is_up() -> bool:
    output = run("docker", "exec", R1, "vtysh", "-c", "show bfd peers brief json")
    return any(
        peer.get("peer") == PRIMARY and str(peer.get("status", "")).lower() == "up"
        for peer in json.loads(output)
    )


def isis_primary_is_down_or_removed() -> bool:
    output = run("docker", "exec", R1, "vtysh", "-c", "show isis neighbor json")
    state = json.loads(output)
    return not any(
        circuit.get("adj") == "r2" and circuit.get("state") == "Up"
        for area in state.get("areas", [])
        for circuit in area.get("circuits", [])
    )


def isis_primary_is_up() -> bool:
    output = run("docker", "exec", R1, "vtysh", "-c", "show isis neighbor json")
    state = json.loads(output)
    return any(
        circuit.get("adj") == "r2" and circuit.get("state") == "Up"
        for area in state.get("areas", [])
        for circuit in area.get("circuits", [])
    )


def observe_transition(predicate, started: float, timeout: float) -> float:
    deadline = started + timeout
    while time.monotonic() < deadline:
        if predicate():
            return (time.monotonic() - started) * 1000
        time.sleep(0.02)
    raise ExperimentError(f"transition not observed within {timeout:.1f}s")


def wait_for_failure_transitions(timeout: float) -> dict[str, float]:
    started = time.monotonic()
    checks = {
        "bfd_down_or_removed_ms": bfd_primary_is_down_or_removed,
        "isis_adjacency_down_ms": isis_primary_is_down_or_removed,
        "fib_switch_ms": lambda: route_nexthop() == BACKUP,
    }
    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        futures = {
            name: pool.submit(observe_transition, predicate, started, timeout)
            for name, predicate in checks.items()
        }
        return {name: future.result() for name, future in futures.items()}


def wait_for_recovery(timeout: float) -> dict[str, float]:
    started = time.monotonic()
    checks = {
        "bfd_up_ms": bfd_primary_is_up,
        "isis_adjacency_up_ms": isis_primary_is_up,
        "primary_fib_restored_ms": lambda: route_nexthop() == PRIMARY,
    }
    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        futures = {
            name: pool.submit(observe_transition, predicate, started, timeout)
            for name, predicate in checks.items()
        }
        return {name: future.result() for name, future in futures.items()}


def set_blackhole() -> None:
    run("docker", "exec", R1, "tc", "qdisc", "replace", "dev", "eth2", "root", "netem", "loss", "100%")
    run("docker", "exec", R2, "tc", "qdisc", "replace", "dev", "eth1", "root", "netem", "loss", "100%")


def clear_blackhole() -> None:
    run("docker", "exec", R1, "tc", "qdisc", "del", "dev", "eth2", "root", check=False)
    run("docker", "exec", R2, "tc", "qdisc", "del", "dev", "eth1", "root", check=False)


def write_report(report: dict[str, object]) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    ping: subprocess.Popen[str] | None = None
    report: dict[str, object] = {
        "schema": 1,
        "mode": "isis_bfd",
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "bfd_profile": {
            "transmit_interval_ms": 100,
            "receive_interval_ms": 100,
            "detect_multiplier": 3,
            "nominal_detection_budget_ms": 300,
        },
        "isis_hello_interval_ms": 3000,
        "isis_hello_multiplier": 3,
        "fault": "bidirectional 100% netem loss on r1-r2 while interfaces remain up",
        "traffic": {"interval_ms": 100, "packet_count": 140},
        "observer": "three independent concurrent docker/vtysh polling loops; values include observer latency",
    }
    try:
        clear_blackhole()
        if route_nexthop() != PRIMARY:
            raise ExperimentError("the primary path is not active; run 'make test' first")

        ping = subprocess.Popen(
            [
                "docker", "exec", H1,
                "ping", "-n", "-i", "0.1", "-c", "140", "-W", "1", DESTINATION,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        time.sleep(0.5)
        set_blackhole()
        transitions = wait_for_failure_transitions(timeout=5.0)
        output, _ = ping.communicate(timeout=20)
        ping = None

        match = re.search(r"(\d+) packets transmitted, (\d+) received", output)
        if not match:
            raise ExperimentError(f"could not parse ping summary:\n{output}")
        transmitted, received = map(int, match.groups())
        lost = transmitted - received
        if route_nexthop() != BACKUP:
            raise ExperimentError("traffic did not remain on the backup path during the fault")

        report["failure"] = transitions
        report["ping"] = {
            "transmitted": transmitted,
            "received": received,
            "lost": lost,
            "loss_percent": lost * 100 / transmitted,
        }
        print("PASS: silent primary-link blackhole injected; interfaces stayed administratively Up")
        print(f"MEASURE: BFD Down/removed observed = {transitions['bfd_down_or_removed_ms']:.1f} ms")
        print(f"MEASURE: IS-IS adjacency Down observed = {transitions['isis_adjacency_down_ms']:.1f} ms")
        print(f"MEASURE: backup FIB installed = {transitions['fib_switch_ms']:.1f} ms")
        print(f"MEASURE: ping transmitted={transmitted} received={received} lost={lost}")
    except (ExperimentError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    finally:
        if ping is not None and ping.poll() is None:
            ping.terminate()
            try:
                ping.wait(timeout=2)
            except subprocess.TimeoutExpired:
                ping.kill()
        clear_blackhole()

    try:
        recovery = wait_for_recovery(timeout=45.0)
        run("docker", "exec", H1, "ping", "-c", "3", "-W", "1", DESTINATION)
        report["recovery"] = recovery
        write_report(report)
        print(f"PASS: BFD primary session Up after {recovery['bfd_up_ms']:.1f} ms")
        print(f"PASS: IS-IS primary adjacency Up after {recovery['isis_adjacency_up_ms']:.1f} ms")
        print(f"PASS: primary FIB restored after {recovery['primary_fib_restored_ms']:.1f} ms")
        print(f"PASS: machine-readable report written to {REPORT}")
    except (ExperimentError, json.JSONDecodeError) as error:
        print(f"FAIL during restoration: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
