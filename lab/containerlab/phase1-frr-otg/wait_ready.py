#!/usr/bin/env python3
import json
import subprocess
import time

deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    checks = []
    for router in ("r1", "r2", "r3", "r4"):
        result = subprocess.run(["docker", "exec", f"clab-sf-phase1-{router}", "vtysh", "-c", "show isis neighbor json"], capture_output=True, text=True)
        try:
            checks.append(result.returncode == 0 and bool(json.loads(result.stdout)))
        except json.JSONDecodeError:
            checks.append(False)
    bgp = subprocess.run(["docker", "exec", "clab-sf-phase1-r1", "vtysh", "-c", "show bgp summary json"], capture_output=True, text=True)
    if all(checks) and '"Established"' in bgp.stdout:
        raise SystemExit(0)
    time.sleep(1)
raise SystemExit("FRR IS-IS/BFD/BGP did not become ready within 60 seconds")
