#!/usr/bin/env python3
"""Wait for a scheduled activation and capture its reconciler result."""

import argparse
import json
import time
import urllib.request
from pathlib import Path


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--endpoint", required=True)
parser.add_argument("--created", required=True, type=Path)
parser.add_argument("--schedule-output", required=True, type=Path)
parser.add_argument("--reconcile-output", required=True, type=Path)
parser.add_argument("--timeout-seconds", type=float, default=20)
args = parser.parse_args()
created = json.loads(args.created.read_text(encoding="utf-8"))
identifier = created["id"]
deadline = time.monotonic() + args.timeout_seconds
while time.monotonic() < deadline:
    with urllib.request.urlopen(f"{args.endpoint}/api/v1/predictive/schedules?id={identifier}", timeout=2) as response:
        schedule = json.load(response)
    if schedule["state"] == "failed":
        raise SystemExit(f"predictive activation failed: {schedule.get('error')}")
    if schedule["state"] == "completed":
        with urllib.request.urlopen(f"{args.endpoint}/api/v1/status", timeout=2) as response:
            status = json.load(response)
        args.schedule_output.write_text(json.dumps(schedule, indent=2) + "\n", encoding="utf-8")
        args.reconcile_output.write_text(
            json.dumps({"plan": status["committed_plan"], "status": status["reconcile"]}, indent=2) + "\n",
            encoding="utf-8",
        )
        raise SystemExit(0)
    time.sleep(0.05)
raise SystemExit("predictive activation did not complete before timeout")
