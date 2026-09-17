#!/usr/bin/env python3
"""Compile a deterministic contact-window CSV into a StarFabric scenario.

Required CSV columns: link_id,source,target,start_ms,end_ms,latency_us,capacity_bps.
Optional risk_groups is a semicolon-separated shared-risk list.
The base JSON provides nodes, intents, and any permanent links.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


REQUIRED = {
    "link_id",
    "source",
    "target",
    "start_ms",
    "end_ms",
    "latency_us",
    "capacity_bps",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--contacts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def positive(row: dict[str, str], key: str, *, allow_zero: bool = False) -> int:
    value = int(row[key])
    if value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{key} must be {'non-negative' if allow_zero else 'positive'}: {row}")
    return value


def compile_plan(base: dict, rows: list[dict[str, str]]) -> dict:
    nodes = {node["id"] for node in base["topology"]["nodes"]}
    links = {link["id"]: link for link in base["topology"].setdefault("links", [])}
    actions: dict[tuple[int, str], list[str]] = defaultdict(list)
    windows: dict[str, list[tuple[int, int]]] = defaultdict(list)

    for row in rows:
        missing = REQUIRED.difference(row)
        if missing:
            raise ValueError(f"missing columns: {sorted(missing)}")
        if row["source"] not in nodes or row["target"] not in nodes:
            raise ValueError(f"contact references unknown node: {row}")
        start = positive(row, "start_ms", allow_zero=True)
        end = positive(row, "end_ms")
        if end <= start:
            raise ValueError(f"end_ms must be after start_ms: {row}")
        link_id = row["link_id"]
        for previous_start, previous_end in windows[link_id]:
            if start < previous_end and previous_start < end:
                raise ValueError(f"overlapping windows for {link_id}")
        windows[link_id].append((start, end))
        candidate = {
            "id": link_id,
            "source": row["source"],
            "target": row["target"],
            "admin_up": True,
            "operational_up": False,
            "latency_us": positive(row, "latency_us", allow_zero=True),
            "capacity_bps": positive(row, "capacity_bps"),
            "link_type": row.get("link_type", ""),
            "range_km": float(row.get("range_km") or 0),
            "doppler_hz": float(row.get("doppler_hz") or 0),
            "risk_groups": sorted({value.strip() for value in (row.get("risk_groups") or "").split(";") if value.strip()}),
        }
        existing = links.get(link_id)
        if existing and (existing["source"], existing["target"]) != (row["source"], row["target"]):
            raise ValueError(f"link id reused with different endpoints: {link_id}")
        if not existing:
            links[link_id] = candidate
            base["topology"]["links"].append(candidate)
        actions[(start, "link_up")].append(link_id)
        actions[(end, "link_down")].append(link_id)

    generated = [
        {"at_ms": at_ms, "type": event_type, "link_ids": sorted(ids), "reconcile": True}
        for (at_ms, event_type), ids in sorted(actions.items(), key=lambda item: (item[0][0], item[0][1]))
    ]
    base["timeline"] = sorted(base.get("timeline", []) + generated, key=lambda action: action["at_ms"])
    return base


def main() -> None:
    args = parse_args()
    with args.base.open(encoding="utf-8") as stream:
        base = json.load(stream)
    with args.contacts.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    result = compile_plan(base, rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
