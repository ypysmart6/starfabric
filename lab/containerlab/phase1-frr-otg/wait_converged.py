#!/usr/bin/env python3
import json
import subprocess
import sys
import time

failure_ns = int(sys.argv[1])
deadline = time.monotonic() + 10
while time.monotonic() < deadline:
    checks = (
        ("clab-sf-phase1-r1", "198.51.100.0/24", "eth3"),
        ("clab-sf-phase1-r4", "192.0.2.0/24", "eth2"),
    )
    converged = True
    try:
        for container, prefix, interface in checks:
            result = subprocess.run(
                ["docker", "exec", container, "vtysh", "-c", f"show ip route {prefix} json"],
                capture_output=True,
                text=True,
            )
            route = json.loads(result.stdout).get(prefix, [])
            if not route or not any(
                item.get("installed")
                and any(hop.get("interfaceName") == interface for hop in item.get("nexthops", []))
                for item in route
            ):
                converged = False
                break
        if converged:
            print((time.time_ns() - failure_ns) / 1_000_000)
            raise SystemExit(0)
    except json.JSONDecodeError:
        pass
    time.sleep(0.02)
raise SystemExit("FRR did not converge bidirectionally to the alternate path")
