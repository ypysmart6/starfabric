#!/usr/bin/env python3
"""Select a real contact handover and compile it into the live FRR topology.

The orbital time axis is preserved in the selection report.  Only replay time
is compressed: the selected contact-end event is injected after traffic has
started so a six-hour orbit propagation does not require a six-hour lab run.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


GROUND_A = "ground-a"
GROUND_B = "ground-b"
REPLAY_PRIMARY = "r2"
REPLAY_BACKUP = "r3"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contacts", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--contact-scenario", required=True, type=Path)
    parser.add_argument("--oem", action="append", required=True, type=Path)
    parser.add_argument("--epoch", required=True)
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--event-forward", required=True, type=Path)
    parser.add_argument("--event-reverse", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    return parser.parse_args()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_rows(path: Path) -> list[dict]:
    output = []
    with path.open(newline="", encoding="utf-8") as stream:
        for raw in csv.DictReader(stream):
            row = dict(raw)
            for key in ("start_ms", "end_ms", "latency_us", "capacity_bps"):
                row[key] = int(row[key])
            for key in ("range_km", "doppler_hz"):
                row[key] = float(row[key])
            if row["end_ms"] <= row["start_ms"]:
                raise ValueError(f"invalid contact interval: {row}")
            output.append(row)
    if not output:
        raise ValueError("contact CSV is empty")
    return output


def interval_intersections(rows: list[dict], satellite: str) -> list[tuple[int, int]]:
    left = [row for row in rows if row["source"] == satellite and row["target"] == GROUND_A]
    right = [row for row in rows if row["source"] == satellite and row["target"] == GROUND_B]
    output = []
    for a in left:
        for b in right:
            start, end = max(a["start_ms"], b["start_ms"]), min(a["end_ms"], b["end_ms"])
            if end > start:
                output.append((start, end))
    return sorted(set(output))


def active(rows: list[dict], source: str, target: str, at_ms: int) -> dict:
    candidates = [
        row for row in rows
        if row["source"] == source and row["target"] == target
        and row["start_ms"] <= at_ms < row["end_ms"]
    ]
    if len(candidates) != 1:
        raise ValueError(f"expected one {source}->{target} contact at {at_ms}, found {len(candidates)}")
    return candidates[0]


def select_handover(rows: list[dict], satellites: list[str]) -> dict:
    candidates = []
    for primary in satellites:
        for backup in satellites:
            if primary == backup:
                continue
            for start, end in interval_intersections(rows, primary):
                sample = end - 1000
                if sample < start:
                    continue
                try:
                    primary_links = [active(rows, GROUND_A, primary, sample), active(rows, primary, GROUND_B, sample)]
                    backup_ground = active(rows, backup, GROUND_B, sample)
                    optical = active(rows, primary, backup, sample)
                    # Select a true bent-pipe to OISL handover: ingress via the
                    # primary survives the egress contact end, while the backup
                    # egress and optical crosslink remain available.
                    if primary_links[0]["end_ms"] < end + 10_000:
                        continue
                    if primary_links[1]["end_ms"] != end:
                        continue
                    if backup_ground["end_ms"] < end + 10_000 or optical["end_ms"] < end + 10_000:
                        continue
                except ValueError:
                    continue
                primary_cost = sum(link["latency_us"] for link in primary_links)
                backup_cost = primary_links[0]["latency_us"] + optical["latency_us"] + backup_ground["latency_us"]
                candidates.append((end, primary, backup, sample, primary_cost, backup_cost))
    if not candidates:
        raise ValueError("no overlapping handover where the ending contact is the active latency path")
    end, primary, backup, sample, primary_cost, backup_cost = sorted(candidates)[0]
    return {
        "primary": primary,
        "backup": backup,
        "sample_ms": sample,
        "handover_ms": end,
        "primary_path_latency_us": primary_cost,
        "backup_path_latency_us": backup_cost,
    }


def mapped_link(row: dict, link_id: str, source: str, target: str) -> dict:
    groups = [value for value in row.get("risk_groups", "").split(";") if value]
    return {
        "id": link_id,
        "source": source,
        "target": target,
        "link_type": row["link_type"],
        "admin_up": True,
        "operational_up": True,
        "latency_us": row["latency_us"],
        "capacity_bps": row["capacity_bps"],
        "range_km": row["range_km"],
        "doppler_hz": row["doppler_hz"],
        "risk_groups": groups,
    }


def iso_at(epoch: datetime, milliseconds: int) -> str:
    return (epoch + timedelta(milliseconds=milliseconds)).isoformat().replace("+00:00", "Z")


def main():
    args = parse_args()
    epoch = datetime.fromisoformat(args.epoch.replace("Z", "+00:00"))
    if epoch.tzinfo is None:
        epoch = epoch.replace(tzinfo=timezone.utc)
    rows = load_rows(args.contacts)
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    satellites = sorted(item["id"] for item in catalog)
    selected = select_handover(rows, satellites)
    sample = selected["sample_ms"]
    mapping = {GROUND_A: "r1", selected["primary"]: REPLAY_PRIMARY, selected["backup"]: REPLAY_BACKUP, GROUND_B: "r4"}
    definitions = [
        (GROUND_A, selected["primary"], "r1-r2"),
        (selected["primary"], GROUND_A, "r2-r1"),
        (selected["primary"], GROUND_B, "r2-r4"),
        (GROUND_B, selected["primary"], "r4-r2"),
        (selected["primary"], selected["backup"], "r2-r3"),
        (selected["backup"], selected["primary"], "r3-r2"),
        (selected["backup"], GROUND_B, "r3-r4"),
        (GROUND_B, selected["backup"], "r4-r3"),
    ]
    links = []
    source_links = {}
    for source, target, link_id in definitions:
        row = active(rows, source, target, sample)
        links.append(mapped_link(row, link_id, mapping[source], mapping[target]))
        source_links[link_id] = {
            "contact_link_id": row["link_id"],
            "latency_us": row["latency_us"],
            "range_km": row["range_km"],
            "max_abs_doppler_hz": row["doppler_hz"],
            "contact_start_ms": row["start_ms"],
            "contact_end_ms": row["end_ms"],
        }

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    scenario = {
        "scenario_id": "tle-sgp4-orbit-frr-otg-live",
        "description": "Compressed replay of a TLE-derived contact handover over the real FRR/Ixia-c data plane",
        "random_seed": 20260904,
        "topology": {
            "version": 1,
            "generated_at": now,
            "valid_from": now,
            "nodes": [
                {"id": "r1", "kind": "gateway", "loopback": "10.255.0.1", "labels": {"frr_container": "clab-sf-phase1-r1", "probe_target": "10.255.0.1", "orbital_object": GROUND_A}, "enabled": True},
                {"id": "r2", "kind": "satellite", "loopback": "10.255.0.2", "labels": {"frr_container": "clab-sf-phase1-r2", "orbital_object": selected["primary"]}, "enabled": True},
                {"id": "r3", "kind": "satellite", "loopback": "10.255.0.3", "labels": {"frr_container": "clab-sf-phase1-r3", "orbital_object": selected["backup"]}, "enabled": True},
                {"id": "r4", "kind": "gateway", "loopback": "10.255.0.4", "labels": {"frr_container": "clab-sf-phase1-r4", "probe_target": "10.255.0.4", "orbital_object": GROUND_B}, "enabled": True},
            ],
            "links": links,
        },
        "intents": [
            {"id": "n3-forward", "source": "r1", "destination": "r4", "destination_prefix": "198.51.100.0/24", "policy": "hops", "demand_bps": 10_000_000, "class": "orbit-derived-transport", "priority": 100},
            {"id": "n3-reverse", "source": "r4", "destination": "r1", "destination_prefix": "192.0.2.0/24", "policy": "hops", "demand_bps": 10_000_000, "class": "orbit-derived-transport", "priority": 100},
        ],
        "timeline": [],
    }

    contact_end = iso_at(epoch, selected["handover_ms"])
    common_attributes = {
        "source": "TLE/SGP4/CCSDS-OEM/contact-window",
        "orbital_object": selected["primary"],
        "contact_end": contact_end,
        "replay": "compressed",
    }
    event_forward = {"event_id": "orbit-contact-end-forward-1", "subject": "r1-r2", "sequence": 1, "type": "link_down", "link": {"id": "r1-r2"}, "attributes": common_attributes}
    event_reverse = {"event_id": "orbit-contact-end-reverse-1", "subject": "r2-r1", "sequence": 1, "type": "link_down", "link": {"id": "r2-r1"}, "attributes": common_attributes}

    selection = {
        "schema": 1,
        "time_model": "orbital timestamps preserved; event replay compressed to the live traffic window",
        "epoch": epoch.isoformat().replace("+00:00", "Z"),
        "sample_at": iso_at(epoch, selected["sample_ms"]),
        "handover_at": contact_end,
        **selected,
        "mapping": mapping,
        "initial_network_path": ["r1", "r2", "r4"],
        "predicted_network_path": ["r1", "r2", "r3", "r4"],
        "network_links": source_links,
        "contact_window_inventory": {
            "total": len(rows),
            "by_link_type": dict(sorted(Counter(row["link_type"] for row in rows).items())),
        },
        "artifacts": {
            "tle_catalog": {"path": str(args.catalog), "sha256": sha256(args.catalog)},
            "oem": [{"path": str(path), "sha256": sha256(path), "ccsds_oem": "CCSDS_OEM_VERS = 2.0" in path.read_text(encoding="utf-8", errors="replace")[:256]} for path in args.oem],
            "contacts": {"path": str(args.contacts), "sha256": sha256(args.contacts)},
            "compiled_contact_scenario": {"path": str(args.contact_scenario), "sha256": sha256(args.contact_scenario)},
        },
    }
    for path, value in ((args.scenario, scenario), (args.event_forward, event_forward), (args.event_reverse, event_reverse), (args.selection, selection)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(selection, indent=2))


if __name__ == "__main__":
    main()
