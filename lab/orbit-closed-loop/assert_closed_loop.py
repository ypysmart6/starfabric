#!/usr/bin/env python3
"""Assert orbit prediction, OISL FIB state, physical failure and packets agree."""

import argparse
import json
from datetime import datetime
from pathlib import Path


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def path(response, intent):
    return response["plan"]["paths"][intent][0]["nodes"]


def has_static(fib, prefix, next_hop):
    return any(
        entry.get("protocol") == "static"
        and entry.get("installed")
        and any(hop.get("ip") == next_hop for hop in entry.get("nexthops", []))
        for entry in fib.get(prefix, [])
    )


parser = argparse.ArgumentParser(description=__doc__)
for name in ("initial", "predicted", "schedule", "scenario", "timing", "fib-before", "fib-after", "traffic", "output"):
    parser.add_argument(f"--{name}", required=True)
parser.add_argument("--convergence-ms", required=True, type=float)
parser.add_argument("--max-convergence-ms", default=500.0, type=float)
args = parser.parse_args()
initial, predicted = load(args.initial), load(args.predicted)
schedule, scenario, timing = load(args.schedule), load(args.scenario), load(args.timing)
fib_before, fib_after, traffic = load(args.fib_before), load(args.fib_after), load(args.traffic)
link_types = {link["id"]: link.get("link_type") for link in scenario["topology"]["links"]}
updated = datetime.fromisoformat(schedule["updated_at"].replace("Z", "+00:00"))
contact_end = datetime.fromisoformat(timing["wall_clock_contact_end"].replace("Z", "+00:00"))
checks = {
    "initial_plan_committed": initial["status"]["phase"] == "committed",
    "initial_forward_path": path(initial, "n3-forward") == ["r1", "r2", "r4"],
    "initial_reverse_path": path(initial, "n3-reverse") == ["r4", "r2", "r1"],
    "initial_frr_fib": has_static(fib_before, "198.51.100.0/24", "10.255.0.4"),
    "prediction_shadow_validated": len(schedule["activations"]) >= 2 and all(item["shadow_validated"] for item in schedule["activations"]),
    "prediction_completed_before_contact_end": schedule["state"] == "completed" and updated < contact_end,
    "predicted_plan_committed": predicted["status"]["phase"] == "committed",
    "predicted_forward_uses_oisl": path(predicted, "n3-forward") == ["r1", "r2", "r3", "r4"] and link_types.get("r2-r3") == "oisl",
    "predicted_reverse_uses_oisl": path(predicted, "n3-reverse") == ["r4", "r3", "r2", "r1"] and link_types.get("r3-r2") == "oisl",
    "oisl_frr_fib": has_static(fib_after, "198.51.100.0/24", "10.255.0.3"),
    "otg_bidirectional_packets": traffic.get("success") is True and len(traffic.get("flows", [])) == 2,
    "zero_touch_contact_end_slo": args.convergence_ms <= args.max_convergence_ms,
}
report = {
    "success": all(checks.values()),
    "evidence_level": "single-host predictive orbital/OISL software data plane",
    "initial_plan_id": initial["plan"]["id"],
    "predicted_plan_id": predicted["plan"]["id"],
    "schedule_id": schedule["id"],
    "convergence_ms": args.convergence_ms,
    "max_convergence_ms": args.max_convergence_ms,
    "checks": checks,
    "traffic": traffic,
}
Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, indent=2))
if not report["success"]:
    raise SystemExit("FAIL: predictive orbit/OISL/FIB/packet evidence disagrees")
