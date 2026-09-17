#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def number(value):
    return int(value or 0)


parser = argparse.ArgumentParser()
parser.add_argument("metrics", type=Path)
parser.add_argument("--max-loss-percent", type=float, default=6.0)
parser.add_argument("--max-packet-outage-ms", type=float, default=600.0)
parser.add_argument("--flow-rate-pps", type=float, default=2500.0)
parser.add_argument("--convergence-ms", type=float, required=True)
parser.add_argument("--max-convergence-ms", type=float, default=2000.0)
parser.add_argument("--summary", type=Path, required=True)
args = parser.parse_args()
samples = [line for line in args.metrics.read_text(encoding="utf-8").splitlines() if line.strip()]
if not samples:
    raise SystemExit("FAIL: OTG returned no metric samples")
try:
    # otgen emits one JSON object per polling interval; only the final cumulative
    # sample has the complete fixed-packet result.
    payload = json.loads(samples[-1])
except json.JSONDecodeError as exc:
    raise SystemExit(f"FAIL: final OTG metric sample is invalid JSON: {exc}") from exc
flows = payload.get("flow_metrics", [])
if not flows:
    raise SystemExit("FAIL: OTG returned no flow metrics")
results = []
failed = False
for flow in flows:
    transmitted, received = number(flow.get("frames_tx")), number(flow.get("frames_rx"))
    lost = max(0, transmitted - received)
    loss = 100.0 if transmitted == 0 else lost * 100.0 / transmitted
    packet_outage_ms = lost * 1000.0 / args.flow_rate_pps
    passed = transmitted > 0 and loss <= args.max_loss_percent and packet_outage_ms <= args.max_packet_outage_ms
    failed = failed or not passed
    results.append({"name": flow.get("name"), "frames_tx": transmitted, "frames_rx": received, "loss_percent": loss, "packet_outage_equivalent_ms": packet_outage_ms, "passed": passed})
if args.convergence_ms > args.max_convergence_ms:
    failed = True
summary = {
    "success": not failed,
    "convergence_ms": args.convergence_ms,
    "max_convergence_ms": args.max_convergence_ms,
    "max_loss_percent": args.max_loss_percent,
    "max_packet_outage_ms": args.max_packet_outage_ms,
    "flow_rate_pps": args.flow_rate_pps,
    "flows": results,
}
args.summary.parent.mkdir(parents=True, exist_ok=True)
args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, indent=2))
if failed:
    raise SystemExit("FAIL: convergence or loss SLO exceeded")
