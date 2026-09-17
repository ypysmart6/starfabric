#!/usr/bin/env python3
"""Propagate a TLE catalog with SGP4 and emit CCSDS OEM KVN for StarFabric.

The resulting OEM files feed ephemeris_contacts.py, keeping orbit propagation
separate from network/contact planning. Operational missions should replace TLE
and this engineering transform with their validated navigation ephemeris.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sgp4.api import Satrec, jday


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path, help="JSON array of id,line1,line2")
    parser.add_argument("--start", required=True)
    parser.add_argument("--duration-seconds", type=int, required=True)
    parser.add_argument("--step-seconds", type=int, default=10)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def propagate(satellite: Satrec, at: datetime) -> tuple[tuple[float, ...], tuple[float, ...]]:
    second = at.second + at.microsecond / 1_000_000
    jd, fraction = jday(at.year, at.month, at.day, at.hour, at.minute, second)
    error, position, velocity = satellite.sgp4(jd, fraction)
    if error:
        raise RuntimeError(f"SGP4 propagation error {error} at {at.isoformat()}")
    return position, velocity


def main() -> None:
    args = arguments()
    if args.duration_seconds <= 0 or args.step_seconds <= 0:
        raise ValueError("duration and step must be positive")
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(catalog, list) or not catalog:
        raise ValueError("catalog must be a non-empty JSON array")
    start = parse_time(args.start)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for item in catalog:
        identifier = item["id"]
        if not identifier.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"unsafe satellite id: {identifier!r}")
        satellite = Satrec.twoline2rv(item["line1"], item["line2"])
        output = [
            "CCSDS_OEM_VERS = 2.0",
            f"CREATION_DATE = {datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}",
            "ORIGINATOR = STARFABRIC-SGP4",
            "META_START",
            f"OBJECT_NAME = {identifier}",
            f"OBJECT_ID = {identifier}",
            "CENTER_NAME = EARTH",
            "REF_FRAME = TEME",
            "TIME_SYSTEM = UTC",
            "META_STOP",
        ]
        elapsed = 0
        while elapsed <= args.duration_seconds:
            at = start + timedelta(seconds=elapsed)
            position, velocity = propagate(satellite, at)
            output.append(f"{at.isoformat().replace('+00:00', 'Z')} " + " ".join(f"{value:.9f}" for value in (*position, *velocity)))
            elapsed += args.step_seconds
        (args.output_dir / f"{identifier}.oem").write_text("\n".join(output) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
