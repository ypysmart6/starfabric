#!/usr/bin/env python3
"""Compile CCSDS OEM satellite ephemerides into StarFabric contact windows.

The tool consumes one OEM KVN file per satellite plus a JSON ground-station
catalog. It computes sampled ground visibility, inter-satellite line of sight,
range, propagation latency and first-order carrier Doppler. It is intentionally
an ephemeris consumer; orbit propagation remains the responsibility of a
validated flight-dynamics tool such as Orekit/GMAT/STK.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

EARTH_RADIUS_KM = 6378.137
WGS84_FLATTENING = 1 / 298.257223563
WGS84_ECCENTRICITY_SQUARED = WGS84_FLATTENING * (2 - WGS84_FLATTENING)
EARTH_ROTATION_RAD_S = 7.2921150e-5
LIGHT_KM_S = 299792.458


@dataclass(frozen=True)
class Sample:
    at: datetime
    position: tuple[float, float, float]
    velocity: tuple[float, float, float]


def parse_time(value: str) -> datetime:
    value = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_oem(path: Path) -> list[Sample]:
    output: list[Sample] = []
    in_metadata = False
    metadata: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("COMMENT") or line.startswith("CCSDS_"):
            continue
        if line == "META_START":
            in_metadata = True
            continue
        if line == "META_STOP":
            in_metadata = False
            continue
        if in_metadata:
            if "=" in line:
                key, value = line.split("=", 1)
                metadata[key.strip()] = value.strip()
            continue
        if "=" in line:
            continue
        fields = line.split()
        if len(fields) < 7:
            continue
        try:
            output.append(Sample(parse_time(fields[0]), tuple(map(float, fields[1:4])), tuple(map(float, fields[4:7]))))
        except ValueError as exc:
            raise ValueError(f"invalid OEM state vector in {path}: {line}") from exc
    if not output:
        raise ValueError(f"no OEM state vectors found in {path}")
    if metadata.get("CENTER_NAME", "").upper() != "EARTH":
        raise ValueError(f"{path} must use CENTER_NAME = EARTH")
    if metadata.get("REF_FRAME", "").upper() != "TEME":
        raise ValueError(f"{path} must use REF_FRAME = TEME for SGP4 state vectors")
    if metadata.get("TIME_SYSTEM", "").upper() != "UTC":
        raise ValueError(f"{path} must use TIME_SYSTEM = UTC")
    output.sort(key=lambda sample: sample.at)
    return output


def dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def subtract(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(x - y for x, y in zip(a, b))


def norm(value: tuple[float, float, float]) -> float:
    return math.sqrt(dot(value, value))


def julian_date(at: datetime) -> float:
    return at.timestamp() / 86400.0 + 2440587.5


def greenwich_mean_sidereal_time(at: datetime) -> float:
    """IAU-82 GMST angle used by the SGP4 TEME-to-PEF transform."""
    jd = julian_date(at.astimezone(timezone.utc))
    centuries = (jd - 2451545.0) / 36525.0
    degrees = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
        + 0.000387933 * centuries**2
        - centuries**3 / 38710000.0
    )
    return math.radians(degrees % 360.0)


def rotate_ecef_to_teme(value: tuple[float, float, float], angle: float) -> tuple[float, float, float]:
    cosine, sine = math.cos(angle), math.sin(angle)
    return (cosine * value[0] - sine * value[1], sine * value[0] + cosine * value[1], value[2])


def ground_teme(station: dict, at: datetime) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    lat = math.radians(float(station["latitude_deg"]))
    lon = math.radians(float(station["longitude_deg"]))
    altitude_km = float(station.get("altitude_m", 0)) / 1000
    prime_vertical = EARTH_RADIUS_KM / math.sqrt(1 - WGS84_ECCENTRICITY_SQUARED * math.sin(lat) ** 2)
    ecef = (
        (prime_vertical + altitude_km) * math.cos(lat) * math.cos(lon),
        (prime_vertical + altitude_km) * math.cos(lat) * math.sin(lon),
        (prime_vertical * (1 - WGS84_ECCENTRICITY_SQUARED) + altitude_km) * math.sin(lat),
    )
    up_ecef = (math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat))
    angle = greenwich_mean_sidereal_time(at)
    position = rotate_ecef_to_teme(ecef, angle)
    up = rotate_ecef_to_teme(up_ecef, angle)
    velocity = (-EARTH_ROTATION_RAD_S * position[1], EARTH_ROTATION_RAD_S * position[0], 0.0)
    return position, velocity, up


def ground_eci(station: dict, at: datetime) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Compatibility wrapper; output is specifically in the SGP4 TEME frame."""
    position, velocity, _ = ground_teme(station, at)
    return position, velocity


def elevation_deg(satellite: tuple[float, float, float], ground: tuple[float, float, float], up: tuple[float, float, float] | None = None) -> float:
    line = subtract(satellite, ground)
    zenith = ground if up is None else up
    return math.degrees(math.asin(dot(line, zenith) / (norm(line) * norm(zenith))))


def intersat_visible(a: tuple[float, float, float], b: tuple[float, float, float], clearance_km: float) -> bool:
    direction = subtract(b, a)
    denominator = dot(direction, direction)
    if denominator == 0:
        return False
    parameter = max(0.0, min(1.0, -dot(a, direction) / denominator))
    closest = tuple(a[i] + parameter * direction[i] for i in range(3))
    return norm(closest) > EARTH_RADIUS_KM + clearance_km


def measurement(a: Sample, b_position: tuple[float, float, float], b_velocity: tuple[float, float, float], carrier_hz: float) -> tuple[float, int, float]:
    delta = subtract(a.position, b_position)
    relative_velocity = subtract(a.velocity, b_velocity)
    distance = norm(delta)
    range_rate = dot(delta, relative_velocity) / distance
    latency_us = math.ceil(distance / LIGHT_KM_S * 1_000_000)
    doppler_hz = -range_rate / LIGHT_KM_S * carrier_hz
    return distance, latency_us, doppler_hz


def contiguous(records: Iterable[dict], step_seconds: float) -> list[dict]:
    windows: list[dict] = []
    active: dict | None = None
    for record in records:
        if record["visible"]:
            if active is None:
                active = dict(record)
                active["end"] = record["at"]
            elif (record["at"] - active["end"]).total_seconds() > step_seconds * 1.5:
                windows.append(active)
                active = dict(record)
            active["end"] = record["at"]
            active["max_range_km"] = max(active.get("max_range_km", 0), record["range_km"])
            active["max_abs_doppler_hz"] = max(active.get("max_abs_doppler_hz", 0), abs(record["doppler_hz"]))
            active["max_latency_us"] = max(active.get("max_latency_us", 0), record["latency_us"])
        elif active is not None:
            windows.append(active)
            active = None
    if active is not None:
        windows.append(active)
    return windows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oem", action="append", required=True, metavar="SAT=FILE")
    parser.add_argument("--ground-stations", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--minimum-elevation-deg", type=float, default=10.0)
    parser.add_argument("--intersat-clearance-km", type=float, default=80.0)
    parser.add_argument("--carrier-hz", type=float, default=20e9)
    parser.add_argument("--capacity-bps", type=int, default=1_000_000_000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ephemerides: dict[str, list[Sample]] = {}
    for specification in args.oem:
        satellite, separator, filename = specification.partition("=")
        if not separator or not satellite:
            raise ValueError("--oem must use SAT=FILE")
        ephemerides[satellite] = read_oem(Path(filename))
    stations = json.loads(args.ground_stations.read_text(encoding="utf-8"))
    if not isinstance(stations, list):
        raise ValueError("ground-stations JSON must be an array")
    start = min(sample.at for values in ephemerides.values() for sample in values)
    rows: list[dict] = []
    for satellite, samples in sorted(ephemerides.items()):
        step = min(((b.at - a.at).total_seconds() for a, b in zip(samples, samples[1:])), default=1.0)
        for station in stations:
            station_id = station["id"]
            records = []
            for sample in samples:
                position, velocity, up = ground_teme(station, sample.at)
                distance, latency, doppler = measurement(sample, position, velocity, args.carrier_hz)
                records.append({"at": sample.at, "visible": elevation_deg(sample.position, position, up) >= args.minimum_elevation_deg, "range_km": distance, "latency_us": latency, "doppler_hz": doppler})
            for window in contiguous(records, step):
                common = {
                    "start_ms": int((window["at"] - start).total_seconds() * 1000),
                    "end_ms": int((window["end"] - start).total_seconds() * 1000 + step * 1000),
                    "latency_us": window["max_latency_us"],
                    "capacity_bps": args.capacity_bps,
                    "range_km": f'{window["max_range_km"]:.3f}',
                    "doppler_hz": f'{window["max_abs_doppler_hz"]:.3f}',
                    "link_type": "sat-ground",
                    "risk_groups": f"ground-site:{station_id}",
                }
                # StarFabric models a physical duplex contact as two directed
                # links so a later telemetry source can report asymmetric
                # capacity, loss, or availability without changing the data
                # model. Geometry is shared by both directions here.
                for source, target in ((satellite, station_id), (station_id, satellite)):
                    rows.append({"link_id": f"{source}-{target}", "source": source, "target": target, **common})
    names = sorted(ephemerides)
    for index, left in enumerate(names):
        by_time = {sample.at: sample for sample in ephemerides[left]}
        for right in names[index + 1:]:
            records = []
            right_by_time = {sample.at: sample for sample in ephemerides[right]}
            common = sorted(set(by_time).intersection(right_by_time))
            step = min(((b - a).total_seconds() for a, b in zip(common, common[1:])), default=1.0)
            for at in common:
                a, b = by_time[at], right_by_time[at]
                distance, latency, doppler = measurement(a, b.position, b.velocity, args.carrier_hz)
                records.append({"at": at, "visible": intersat_visible(a.position, b.position, args.intersat_clearance_km), "range_km": distance, "latency_us": latency, "doppler_hz": doppler})
            for window in contiguous(records, step):
                for source, target in ((left, right), (right, left)):
                    rows.append({"link_id": f"{source}-{target}", "source": source, "target": target, "start_ms": int((window["at"] - start).total_seconds() * 1000), "end_ms": int((window["end"] - start).total_seconds() * 1000 + step * 1000), "latency_us": window["max_latency_us"], "capacity_bps": args.capacity_bps, "range_km": f'{window["max_range_km"]:.3f}', "doppler_hz": f'{window["max_abs_doppler_hz"]:.3f}', "link_type": "oisl", "risk_groups": ""})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["link_id", "source", "target", "start_ms", "end_ms", "latency_us", "capacity_bps", "range_km", "doppler_hz", "link_type", "risk_groups"])
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: (row["start_ms"], row["link_id"])))


if __name__ == "__main__":
    main()
