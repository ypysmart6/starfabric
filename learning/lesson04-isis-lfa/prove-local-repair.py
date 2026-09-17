#!/usr/bin/env python3

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import time


LAB = "clab-sf-l04-lfa"
H1 = f"{LAB}-h1"
R1 = f"{LAB}-r1"
R2 = f"{LAB}-r2"
PREFIX = "198.51.100.0/24"
DESTINATION = "198.51.100.2"
PRIMARY = "10.0.12.1"
BACKUP = "10.0.13.1"
SPF_DELAY_MS = 5000
REPORT = Path(__file__).resolve().parent / "reports" / "local-repair-proof.json"


class ProofError(RuntimeError):
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
        raise ProofError(
            f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}"
        )
    return result.stdout


def r1_vtysh(command: str) -> str:
    return run("docker", "exec", R1, "vtysh", "-c", command)


def route_entry() -> dict[str, object] | None:
    routes = json.loads(r1_vtysh(f"show ip route {PREFIX} json")).get(PREFIX, [])
    return routes[0] if len(routes) == 1 else None


def active_nexthop() -> str | None:
    entry = route_entry()
    if entry is None or not entry.get("installed"):
        return None
    for hop in entry.get("nexthops", []):
        if hop.get("active") and hop.get("fib"):
            return hop.get("ip")
    return None


def lfa_is_preinstalled() -> bool:
    entry = route_entry()
    if entry is None or active_nexthop() != PRIMARY:
        return False
    backups = {
        (hop.get("ip"), hop.get("interfaceName"))
        for hop in entry.get("backupNexthops", [])
        if hop.get("active")
    }
    return backups == {(BACKUP, "eth3")} and any(
        hop.get("backupIndex") == [0] for hop in entry.get("nexthops", [])
    )


def set_spf_delay() -> None:
    command = (
        f"spf-delay-ietf init-delay {SPF_DELAY_MS} "
        f"short-delay {SPF_DELAY_MS} long-delay {SPF_DELAY_MS} "
        "holddown 10000 time-to-learn 5000"
    )
    run(
        "docker", "exec", R1, "vtysh",
        "-c", "configure terminal",
        "-c", "router isis SF",
        "-c", command,
    )


def clear_spf_delay() -> None:
    run(
        "docker", "exec", R1, "vtysh",
        "-c", "configure terminal",
        "-c", "router isis SF",
        "-c", "no spf-delay-ietf",
        check=False,
    )


def set_blackhole() -> None:
    run(
        "docker", "exec", R1, "tc", "qdisc", "replace", "dev", "eth2",
        "root", "netem", "loss", "100%",
    )
    run(
        "docker", "exec", R2, "tc", "qdisc", "replace", "dev", "eth1",
        "root", "netem", "loss", "100%",
    )


def clear_blackhole() -> None:
    run(
        "docker", "exec", R1, "tc", "qdisc", "del", "dev", "eth2", "root",
        check=False,
    )
    run(
        "docker", "exec", R2, "tc", "qdisc", "del", "dev", "eth1", "root",
        check=False,
    )


def wait_until(predicate, timeout: float, description: str) -> float:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if predicate():
            return (time.monotonic() - started) * 1000
        time.sleep(0.02)
    raise ProofError(f"{description} was not observed within {timeout:.1f}s")


def traceroute_hops() -> tuple[list[str], str]:
    output = run(
        "docker", "exec", H1,
        "traceroute", "-n", "-m", "6", "-w", "1", "-q", "1", DESTINATION,
    )
    hops = []
    for line in output.splitlines():
        match = re.match(r"\s*\d+\s+(\d+\.\d+\.\d+\.\d+)", line)
        if match:
            hops.append(match.group(1))
    return hops, output


def write_report(report: dict[str, object]) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    report: dict[str, object] = {
        "schema": 1,
        "mode": "classic_lfa_with_deliberately_delayed_spf",
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "spf_delay_ms": SPF_DELAY_MS,
        "fault": "bidirectional 100% netem loss on r1-r2 while interfaces remain up",
    }
    result = 1
    clear_blackhole()
    clear_spf_delay()
    try:
        if active_nexthop() != PRIMARY or not lfa_is_preinstalled():
            raise ProofError("primary route or preinstalled LFA is not ready; run 'make test'")

        set_spf_delay()
        print(f"SETUP: r1 full SPF is deliberately delayed by {SPF_DELAY_MS} ms")
        time.sleep(6)
        if active_nexthop() != PRIMARY or not lfa_is_preinstalled():
            raise ProofError("route protection did not remain healthy after SPF-delay setup")

        fault_started = time.monotonic()
        set_blackhole()
        local_repair_ms = wait_until(
            lambda: active_nexthop() == BACKUP,
            timeout=(SPF_DELAY_MS - 500) / 1000,
            description="backup FIB activation before delayed SPF",
        )
        spf_status = r1_vtysh("show isis spf-delay-ietf")
        if "SPF delay status: Pending" not in spf_status:
            raise ProofError(
                "backup became active, but the full-SPF pending state was not observed; "
                "rerun the proof to avoid a scheduler outlier"
            )

        run("docker", "exec", H1, "ping", "-c", "3", "-W", "1", DESTINATION)
        hops, trace = traceroute_hops()
        expected = ["192.0.2.1", "10.0.13.1", "10.0.34.1", DESTINATION]
        if hops != expected:
            raise ProofError(f"traffic did not use the protected path:\n{trace}")

        elapsed_ms = (time.monotonic() - fault_started) * 1000
        status_line = next(
            line.strip() for line in spf_status.splitlines() if "SPF delay status:" in line
        )
        report.update(
            {
                "backup_fib_observed_ms": local_repair_ms,
                "proof_checked_ms": elapsed_ms,
                "full_spf_status_at_proof": status_line,
                "observed_path": hops,
                "data_plane_reachable": True,
            }
        )
        write_report(report)
        print(f"PASS: backup FIB became active after {local_repair_ms:.1f} ms")
        print("PASS: data plane uses h1 -> r1 -> r3 -> r4 -> h2")
        print(f"PASS: at that time r1 reported: {status_line}")
        print("CONCLUSION: local repair restored forwarding before r1 ran the delayed full SPF")
        print(f"PASS: proof written to {REPORT}")
        result = 0
    except (ProofError, json.JSONDecodeError, KeyError, TypeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
    finally:
        clear_blackhole()
        clear_spf_delay()

    try:
        restored_ms = wait_until(
            lambda: active_nexthop() == PRIMARY and lfa_is_preinstalled(),
            timeout=45.0,
            description="primary route and LFA re-arming during cleanup",
        )
        print(f"CLEANUP: primary path and LFA protection restored after {restored_ms:.1f} ms")
    except (ProofError, json.JSONDecodeError, TypeError) as error:
        print(f"FAIL during cleanup: {error}", file=sys.stderr)
        return 1
    return result


if __name__ == "__main__":
    raise SystemExit(main())
