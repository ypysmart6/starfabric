#!/usr/bin/env python3
"""Combine the two advanced-protocol proof reports into one acceptance result."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", required=True, type=Path)
    parser.add_argument("--evpn", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    control = json.loads(args.control.read_text(encoding="utf-8"))
    evpn = json.loads(args.evpn.read_text(encoding="utf-8"))
    report = {
        "schema": 1,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "scope": ["SRv6", "PCEP", "BGP-LS", "EVPN", "VXLAN"],
        "evidence_level": "one-computer real FRR control protocols and Linux kernel packet data planes",
        "control_loop": control,
        "evpn_vxlan_loop": evpn,
        "success": control.get("success") is True and evpn.get("success") is True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "success": report["success"],
        "scope": report["scope"],
        "control_checks": control.get("checks", {}),
        "evpn_vxlan_checks": evpn.get("checks", {}),
        "output": str(args.output),
    }, indent=2))
    if not report["success"]:
        raise SystemExit("FAIL: advanced protocol aggregate report is not successful")


if __name__ == "__main__":
    main()

