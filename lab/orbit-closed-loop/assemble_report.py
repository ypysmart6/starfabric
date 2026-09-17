#!/usr/bin/env python3
"""Join orbital provenance and live network evidence into one acceptance report."""

import argparse
import json
from pathlib import Path


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--selection", required=True, type=Path)
parser.add_argument("--network", required=True, type=Path)
parser.add_argument("--scenario", required=True, type=Path)
parser.add_argument("--output", required=True, type=Path)
args = parser.parse_args()

selection = json.loads(args.selection.read_text(encoding="utf-8"))
network = json.loads(args.network.read_text(encoding="utf-8"))
scenario = json.loads(args.scenario.read_text(encoding="utf-8"))
artifacts = selection.get("artifacts", {})
window_types = selection.get("contact_window_inventory", {}).get("by_link_type", {})
checks = {
    "tle_catalog_hashed": len(artifacts.get("tle_catalog", {}).get("sha256", "")) == 64,
    "two_ccsds_oem_files": len(artifacts.get("oem", [])) == 2 and all(item.get("ccsds_oem") for item in artifacts.get("oem", [])),
    "contact_windows_hashed": len(artifacts.get("contacts", {}).get("sha256", "")) == 64,
    "contact_scenario_compiled": len(artifacts.get("compiled_contact_scenario", {}).get("sha256", "")) == 64,
    "sat_ground_visibility_windows_compiled": window_types.get("sat-ground", 0) >= 2,
    "inter_satellite_visibility_windows_compiled": window_types.get("oisl", 0) >= 2,
    "doppler_carried_into_topology": all(link.get("doppler_hz", 0) > 0 for link in scenario["topology"]["links"]),
    "range_carried_into_topology": all(link.get("range_km", 0) > 0 for link in scenario["topology"]["links"]),
    "primary_contact_ends_with_backup_available": selection["handover_ms"] > selection["sample_ms"],
    "live_frr_otg_closed_loop": network.get("success") is True,
}
report = {
    "success": all(checks.values()),
    "evidence_level": "single-host orbital-input to real software FIB and packet data plane",
    "boundary": "TLE replay uses software routers and OTG; it is not mission navigation truth, RF, or flight hardware evidence",
    "checks": checks,
    "orbit": selection,
    "network": network,
}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, indent=2))
if not report["success"]:
    raise SystemExit("FAIL: orbital/network evidence did not form one closed loop")
