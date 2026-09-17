#!/usr/bin/env python3
"""Assert that controller intent, actual FRR FIB, and OTG packets agree."""

import argparse
import json
from pathlib import Path


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def selected_path(response, intent):
    paths = response["plan"]["paths"][intent]
    if not paths:
        raise ValueError(f"plan has no path for {intent}")
    return paths[0]["nodes"]


def has_static_next_hop(fib, prefix, next_hop):
    for entry in fib.get(prefix, []):
        if entry.get("protocol") != "static" or not entry.get("installed"):
            continue
        for hop in entry.get("nexthops", []):
            if hop.get("ip") == next_hop:
                return True
    return False


parser = argparse.ArgumentParser()
parser.add_argument("--initial", required=True)
parser.add_argument("--recovered", required=True)
parser.add_argument("--fib-before", required=True)
parser.add_argument("--fib-after", required=True)
parser.add_argument("--traffic", required=True)
parser.add_argument("--convergence-ms", required=True, type=float)
parser.add_argument("--max-convergence-ms", default=3000.0, type=float)
parser.add_argument("--output", required=True)
args = parser.parse_args()

initial = load(args.initial)
recovered = load(args.recovered)
fib_before = load(args.fib_before)
fib_after = load(args.fib_after)
traffic = load(args.traffic)

checks = {
    "initial_plan_committed": initial["status"]["phase"] == "committed",
    "initial_forward_path": selected_path(initial, "n3-forward") == ["r1", "r2", "r4"],
    "initial_reverse_path": selected_path(initial, "n3-reverse") == ["r4", "r2", "r1"],
    "initial_frr_fib": has_static_next_hop(fib_before, "198.51.100.0/24", "10.255.0.2"),
    "recovery_plan_committed": recovered["status"]["phase"] == "committed",
    "recovered_forward_path": selected_path(recovered, "n3-forward") == ["r1", "r3", "r4"],
    "recovered_reverse_path": selected_path(recovered, "n3-reverse") == ["r4", "r3", "r1"],
    "recovered_frr_fib": has_static_next_hop(fib_after, "198.51.100.0/24", "10.255.0.3"),
    "otg_bidirectional_packets": traffic.get("success") is True and len(traffic.get("flows", [])) == 2,
    "convergence_slo": args.convergence_ms <= args.max_convergence_ms,
}
report = {
    "success": all(checks.values()),
    "evidence_level": "single-host software data plane",
    "initial_plan_id": initial["plan"]["id"],
    "recovered_plan_id": recovered["plan"]["id"],
    "convergence_ms": args.convergence_ms,
    "max_convergence_ms": args.max_convergence_ms,
    "checks": checks,
    "traffic": traffic,
}
Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, indent=2))
if not report["success"]:
    raise SystemExit("FAIL: controller/FIB/packet closed-loop evidence disagrees")
