#!/usr/bin/env python3
"""Render the selected orbital contact end onto a short wall-clock replay."""

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--scenario", required=True, type=Path)
parser.add_argument("--output", required=True, type=Path)
parser.add_argument("--timing", required=True, type=Path)
parser.add_argument("--handover-delay-seconds", type=int, default=10)
parser.add_argument("--lead-seconds", type=int, default=5)
args = parser.parse_args()
if args.handover_delay_seconds <= args.lead_seconds or args.lead_seconds < 0:
    raise SystemExit("handover delay must be greater than non-negative lead")
scenario = json.loads(args.scenario.read_text(encoding="utf-8"))
by_id = {link["id"]: link for link in scenario["topology"]["links"]}
now = datetime.now(timezone.utc)
start = now - timedelta(minutes=1)
end = now + timedelta(seconds=args.handover_delay_seconds)


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


windows = []
for identifier in ("r2-r4", "r4-r2"):
    link = dict(by_id[identifier])
    link["admin_up"] = True
    link["operational_up"] = True
    windows.append({"link": link, "start": iso(start), "end": iso(end)})
request = {
    "windows": windows,
    "horizon_seconds": args.handover_delay_seconds + 20,
    "lead_seconds": args.lead_seconds,
}
timing = {
    "rendered_at": iso(now),
    "wall_clock_contact_end": iso(end),
    "lead_seconds": args.lead_seconds,
    "handover_delay_seconds": args.handover_delay_seconds,
}
args.output.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
args.timing.write_text(json.dumps(timing, indent=2) + "\n", encoding="utf-8")
