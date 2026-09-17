#!/usr/bin/env python3

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import time


LAB = "clab-sf-l02-isis"
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
    output = run(
        "docker", "exec", R1, "vtysh", "-c", f"show ip route {PREFIX} json"
    )
    routes = json.loads(output).get(PREFIX, [])
    for route in routes:
        if not route.get("installed"):
            continue
        for hop in route.get("nexthops", []):
            if hop.get("active") and hop.get("fib"):
                return hop.get("ip")
    return None


def wait_for_nexthop(expected: str, timeout: float) -> float:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if route_nexthop() == expected:
            return time.monotonic() - started
        time.sleep(0.05)
    raise ExperimentError(
        f"route did not switch to {expected} within {timeout:.1f} seconds"
    )


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
        "mode": "isis_only",
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "isis_hello_interval_ms": 3000,
        "isis_hello_multiplier": 3,
        "nominal_detection_budget_ms": 9000,
        "fault": "bidirectional 100% netem loss on r1-r2 while interfaces remain up",
        "traffic": {"interval_ms": 100, "packet_count": 140},
    }
    try:
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
        fault_started = time.monotonic()
        switched_after = wait_for_nexthop(BACKUP, timeout=15.0)

        output, _ = ping.communicate(timeout=20)
        elapsed_ms = (time.monotonic() - fault_started) * 1000
        match = re.search(r"(\d+) packets transmitted, (\d+) received", output)
        if not match:
            raise ExperimentError(f"could not parse ping summary:\n{output}")
        transmitted, received = map(int, match.groups())
        lost = transmitted - received

        if route_nexthop() != BACKUP:
            raise ExperimentError("traffic did not remain on the backup path during the fault")

        print("PASS: silent primary-link blackhole injected")
        print(f"PASS: r1 FIB switched to backup nexthop {BACKUP}")
        print(f"MEASURE: topology-event-to-FIB = {switched_after * 1000:.1f} ms")
        print(f"MEASURE: ping transmitted={transmitted} received={received} lost={lost}")
        print(f"MEASURE: observation window after fault = {elapsed_ms:.1f} ms")
        report["failure"] = {"fib_switch_ms": switched_after * 1000}
        report["ping"] = {
            "transmitted": transmitted,
            "received": received,
            "lost": lost,
            "loss_percent": lost * 100 / transmitted,
        }
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
        restored_after = wait_for_nexthop(PRIMARY, timeout=45.0)
        run("docker", "exec", H1, "ping", "-c", "3", "-W", "1", DESTINATION)
        report["recovery"] = {"primary_fib_restored_ms": restored_after * 1000}
        write_report(report)
        print(f"PASS: primary path restored after {restored_after * 1000:.1f} ms")
        print(f"PASS: machine-readable report written to {REPORT}")
    except (ExperimentError, json.JSONDecodeError) as error:
        print(f"FAIL during restoration: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
