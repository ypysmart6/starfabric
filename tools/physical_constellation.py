#!/usr/bin/env python3
"""Compile per-satellite TEME states into a resource-limited physical network.

SGP4 supplies propagation; WGS84 ground coordinates and Earth rotation come from
ephemeris_contacts. A sampled, deterministic terminal scheduler chooses only
geometrically eligible links. Radio/optical budgets are explicit reference-SNR
engineering models, not calibrated mission link budgets.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.ephemeris_contacts import Sample, elevation_deg, ground_teme, intersat_visible, measurement, read_oem


def utc(value):
    at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if at.tzinfo is None:
        raise ValueError("physical epoch must specify a UTC offset")
    return at.astimezone(timezone.utc)


def finite(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be finite and within [{minimum}, {maximum}]")
    return value


def load_config(path, satellite_ids, gateway_ids):
    config = json.loads(Path(path).read_text())
    if config.get("schema_version") != 1:
        raise ValueError("physical config schema_version must be 1")
    utc(config["epoch"])
    finite(config["duration_seconds"], "duration_seconds", 1, 86400)
    finite(config["step_seconds"], "step_seconds", 0.1, 300)
    if config["duration_seconds"] < config["step_seconds"]:
        raise ValueError("duration must include at least one complete simulation step")
    frames=math.ceil(config['duration_seconds']/config['step_seconds'])+2
    if frames*len(satellite_ids)>2000000 or frames*len(satellite_ids)**2>500000000:
        raise ValueError('physical compilation budget exceeded; shorten the horizon or increase the sampling step')
    stations = config["ground_stations"]
    if len({s["id"] for s in stations}) != len(stations):
        raise ValueError("duplicate ground station id")
    by_id = {s["id"]: s for s in stations}
    missing = set(gateway_ids) - set(by_id)
    if missing:
        raise ValueError(f"missing geographic coordinates for gateways: {sorted(missing)}")
    config["ground_stations"] = [by_id[n] for n in gateway_ids]
    for s in config["ground_stations"]:
        for key, lo, hi in [("latitude_deg", -90, 90), ("longitude_deg", -180, 180), ("altitude_m", -500, 10000)]:
            finite(s[key], key, lo, hi)
    links = config["links"]
    for key, lo, hi in [("minimum_elevation_deg", 0, 90), ("earth_clearance_km", 0, 1000),
                        ("max_isl_range_km", 1, 100000), ("max_feeder_range_km", 1, 100000),
                        ("acquisition_seconds", 0, 600), ("processing_delay_us", 0, 1000000),
                        ("packet_loss_ppm", 0, 1000000), ("queue_limit_packets", 1, 1000000)]:
        finite(links[key], key, lo, hi)
    for key in ("isl_terminals_per_satellite", "feeder_terminals_per_satellite", "gateway_terminals", "queue_limit_packets"):
        finite(links[key], key, 1, 1000000)
        if int(links[key]) != links[key]:
            raise ValueError(f"{key} must be an integer")
    finite(links["same_plane_isl_terminals"], "same_plane_isl_terminals", 0, links["isl_terminals_per_satellite"])
    if int(links["same_plane_isl_terminals"]) != links["same_plane_isl_terminals"]:
        raise ValueError("same_plane_isl_terminals must be an integer")
    for kind in ("isl", "feeder"):
        budget = links[kind]
        for key, lo, hi in [("carrier_hz", 1, 1e16), ("reference_range_km", 1, 100000),
                            ("snr_at_reference_db", -100, 100), ("bandwidth_hz", 1, 1e12),
                            ("efficiency", 0.001, 1), ("max_capacity_bps", 1, 1e12)]:
            finite(budget[key], kind + "." + key, lo, hi)
    source = config["orbit"]["source"]
    if source not in ("walker_sgp4", "tle", "oem"):
        raise ValueError("orbit.source must be walker_sgp4, tle, or oem")
    if source == "walker_sgp4":
        for key, lo, hi in [("altitude_km", 200, 20000), ("inclination_deg", 0, 180),
                            ("phasing", 0, len(satellite_ids)), ("raan_offset_deg", 0, 360),
                            ("mean_anomaly_offset_deg", 0, 360)]:
            finite(config["orbit"][key], key, lo, hi)
        if int(config['orbit']['phasing'])!=config['orbit']['phasing']:
            raise ValueError('Walker phasing must be an integer')
    else:
        catalog_path = (Path(path).resolve().parent / config["orbit"]["catalog"]).resolve()
        catalog = json.loads(catalog_path.read_text())
        if not isinstance(catalog, list) or len({v["id"] for v in catalog}) != len(catalog):
            raise ValueError("orbit catalog must have unique satellite ids")
        if {v["id"] for v in catalog} != set(satellite_ids):
            raise ValueError("orbit catalog must match every configured satellite exactly")
        config["orbit"]["catalog_sha256"] = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
        if source == "oem":
            for item in catalog:
                item["path"] = str((catalog_path.parent / item["path"]).resolve())
        config["orbit"]["records"] = catalog
    return config


class Orbits:
    def __init__(self, nodes, config):
        self.epoch = utc(config["epoch"])
        self.source = config["orbit"]["source"]
        self.records, self.elements = {}, {}
        orbit = config["orbit"]
        if self.source != "oem":
            from sgp4.api import Satrec, WGS72, jday
            jd, fraction = jday(self.epoch.year, self.epoch.month, self.epoch.day, self.epoch.hour,
                                self.epoch.minute, self.epoch.second + self.epoch.microsecond / 1e6)
        if self.source == "walker_sgp4":
            planes = max(int(n["labels"]["logical_plane"]) for n in nodes)
            slots = len(nodes) // planes
            mean_motion = math.sqrt(398600.8 / (6378.135 + orbit["altitude_km"]) ** 3) * 60
            for ordinal, node in enumerate(nodes, 1):
                plane, slot = int(node["labels"]["logical_plane"]) - 1, int(node["labels"]["logical_slot"]) - 1
                raan = (orbit["raan_offset_deg"] + 360 * plane / planes) % 360
                anomaly = (orbit["mean_anomaly_offset_deg"] + 360 * slot / slots + 360 * orbit["phasing"] * plane / len(nodes)) % 360
                sat = Satrec()
                sat.sgp4init(WGS72, "i", ordinal, jd + fraction - 2433281.5, 0, 0, 0, 0, 0,
                            math.radians(orbit["inclination_deg"]), math.radians(anomaly), mean_motion, math.radians(raan))
                self.records[node["id"]] = sat
                self.elements[node["id"]] = {"source": "designed_sgp4_mean_elements", "epoch": config["epoch"],
                    "altitude_parameter_km": orbit["altitude_km"], "inclination_deg": orbit["inclination_deg"],
                    "raan_deg": raan, "mean_anomaly_deg": anomaly, "mean_motion_rad_min": mean_motion,
                    "eccentricity": 0, "argument_of_perigee_deg": 0, "bstar": 0}
        elif self.source == "tle":
            for item in orbit["records"]:
                self.records[item["id"]] = Satrec.twoline2rv(item["line1"], item["line2"])
                self.elements[item["id"]] = dict(item, source="tle")
        else:
            for item in orbit["records"]:
                values = read_oem(Path(item["path"]))
                if any(b.at <= a.at for a, b in zip(values, values[1:])):
                    raise ValueError("OEM sample times must be unique and increasing")
                self.records[item["id"]] = values
                self.elements[item["id"]] = {"source": "oem", "path": item["path"],
                    "sha256": hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest()}

    def states(self, offset):
        at = self.epoch + timedelta(seconds=offset)
        values = {}
        for name, record in self.records.items():
            if self.source != "oem":
                from tools.tle_to_oem import propagate
                position, velocity = propagate(record, at)
            else:
                # Cubic Hermite interpolation uses both OEM positions and velocities.
                import bisect
                index = bisect.bisect_left([v.at for v in record], at)
                if index < len(record) and record[index].at == at:
                    position, velocity = record[index].position, record[index].velocity
                else:
                    if not 0 < index < len(record):
                        raise ValueError(f"OEM for {name} does not cover {at.isoformat()} (including acquisition warmup)")
                    a, b = record[index - 1], record[index]
                    dt = (b.at - a.at).total_seconds(); u = (at - a.at).total_seconds() / dt
                    position = tuple((2*u**3-3*u**2+1)*a.position[i] + (u**3-2*u**2+u)*dt*a.velocity[i]
                                     + (-2*u**3+3*u**2)*b.position[i] + (u**3-u**2)*dt*b.velocity[i] for i in range(3))
                    velocity = tuple((6*u*u-6*u)/dt*a.position[i] + (3*u*u-4*u+1)*a.velocity[i]
                                     + (-6*u*u+6*u)/dt*b.position[i] + (3*u*u-2*u)*b.velocity[i] for i in range(3))
            values[name] = Sample(at, position, velocity)
        return values


def capacity(distance, budget):
    snr = 10 ** (budget["snr_at_reference_db"] / 10) * (budget["reference_range_km"] / max(distance, 0.001)) ** 2
    return max(1, int(min(budget["max_capacity_bps"], budget["efficiency"] * budget["bandwidth_hz"] * math.log2(1 + snr))))


class Contacts:
    def __init__(self, nodes, config):
        self.config = config
        self.planes = {n["id"]: n.get("labels", {}).get("logical_plane") for n in nodes}
        self.selected = {}

    def frame(self, states, offset):
        policy = self.config["links"]
        candidates = []
        ids = sorted(states)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                left, right = states[a], states[b]
                if sum((x-y)**2 for x,y in zip(left.position,right.position))<1e-12:
                    raise ValueError(f'coincident satellite states: {a}, {b}; check orbit identity and phasing')
                distance, delay, doppler = measurement(left, right.position, right.velocity, policy["isl"]["carrier_hz"])
                if distance > policy["max_isl_range_km"] or not intersat_visible(left.position, right.position, policy["earth_clearance_km"]):
                    continue
                # Same-plane neighbors receive scheduling preference, but geometry
                # and each terminal budget still apply to every selected pair.
                preference = 0 if self.planes[a] is not None and self.planes[a] == self.planes[b] else 1
                candidates.append(("isl", a, b, distance, delay, doppler, preference))
        for station in self.config["ground_stations"]:
            for name, state in states.items():
                pos, vel, up = ground_teme(station, state.at)
                distance, delay, doppler = measurement(state, pos, vel, policy["feeder"]["carrier_hz"])
                if distance <= policy["max_feeder_range_km"] and elevation_deg(state.position, pos, up) >= policy["minimum_elevation_deg"]:
                    candidates.append(("feeder", name, station["id"], distance, delay, doppler, 0))
        candidates.sort(key=lambda v: (v[0], v[6], (v[1], v[2]) not in self.selected, v[3], v[1], v[2]))
        counts, chosen, links = Counter(), {}, []
        for kind, a, b, distance, delay, doppler, preference in candidates:
            keys = ((kind, a), (kind, b))
            limits = (policy["isl_terminals_per_satellite"],) * 2 if kind == "isl" else (policy["feeder_terminals_per_satellite"], policy["gateway_terminals"])
            if any(counts[key] >= limit for key, limit in zip(keys, limits)):
                continue
            if kind == "isl" and preference == 0:
                if any(counts["same_plane", node] >= policy["same_plane_isl_terminals"] for node in (a, b)):
                    continue
                counts.update([("same_plane", a), ("same_plane", b)])
            counts.update(keys)
            since = self.selected.get((a, b), offset)
            chosen[a, b] = since
            acquired = offset - since >= policy["acquisition_seconds"]
            for source, target in ((a, b), (b, a)):
                links.append({"id": source + "--" + target, "source": source, "target": target,
                    "link_type": "oisl" if kind == "isl" else "feeder", "admin_up": True, "operational_up": acquired,
                    "acquisition_state": "locked" if acquired else "acquiring",
                    "latency_us": delay + int(policy["processing_delay_us"]),
                    "capacity_bps": capacity(distance, policy[kind]), "loss_ppm": int(policy["packet_loss_ppm"]),
                    "reliability_ppm": 1000000 - int(policy["packet_loss_ppm"]),
                    "range_km": round(distance, 6), "doppler_hz": round(doppler, 3),
                    "risk_groups": []})
        self.selected = chosen
        return {"offset_seconds": offset, "at": next(iter(states.values())).at.isoformat(),
            "states": {n: {"position_km": list(v.position), "velocity_km_s": list(v.velocity)} for n, v in states.items()},
            "links": sorted(links, key=lambda v: v["id"]), "eligible_pairs": len(candidates),
            "selected_pairs": len(chosen), "active_pairs": sum(v["operational_up"] for v in links) // 2}


def components(nodes, links):
    graph = {n: set() for n in nodes}
    for link in links:
        if link["operational_up"]:
            graph[link["source"]].add(link["target"])
    remaining, groups = set(graph), []
    while remaining:
        seen, todo = set(), [min(remaining)]
        while todo:
            node = todo.pop()
            if node in seen:
                continue
            seen.add(node); todo.extend(graph[node] - seen)
        remaining -= seen; groups.append(sorted(seen))
    return groups


def compile_scenario(seed, config):
    scenario = copy.deepcopy(seed)
    satellites = sorted((n for n in scenario["topology"]["nodes"] if n["kind"] == "satellite"), key=lambda n: n["id"])
    orbits = Orbits(satellites, config)
    contacts = Contacts(satellites, config)
    step, duration = config["step_seconds"], config["duration_seconds"]
    # Run acquisition before the observation epoch. All emitted t=0 links must
    # have satisfied the same geometry and terminal scheduler during warmup.
    warmup = max(step, math.ceil(config["links"]["acquisition_seconds"] / step) * step)
    offset = -warmup
    frames = []
    while offset <= duration + 1e-9:
        frame = contacts.frame(orbits.states(offset), offset)
        if offset >= 0:
            frames.append(frame)
        offset = round(offset + step, 9)
    if frames[-1]["offset_seconds"] < duration:
        frames.append(contacts.frame(orbits.states(duration), duration))
    universe = {l["id"]: l for f in frames for l in f["links"]}
    initial = {l["id"]: l for l in frames[0]["links"]}
    scenario["topology"]["links"] = [copy.deepcopy(initial.get(name, dict(link, operational_up=False, acquisition_state="unavailable"))) for name, link in sorted(universe.items())]
    scenario["topology"].update(version=1, generated_at=config["epoch"], valid_from=config["epoch"])
    scenario["topology"].pop("valid_until", None)
    stations = {s["id"]: s for s in config["ground_stations"]}
    for node in scenario["topology"]["nodes"]:
        labels = node.setdefault("labels", {})
        labels["model"] = "physical-sampled"
        if node["kind"] == "satellite":
            labels["orbit_source"] = config["orbit"]["source"]
        else:
            labels.update({k: str(stations[node["id"]][k]) for k in ("latitude_deg", "longitude_deg", "altitude_m")})
            # A ground site is an endpoint failure domain. Declaring it on every
            # feeder as a link SRLG would make all same-endpoint backup paths
            # impossible. Site failure is represented by disabling this node.
            labels['ground_site']=node['id']
    scenario.update(scenario_id=seed["scenario_id"].replace("synthetic-", "physical-"),
                    description="Per-satellite TEME propagation and geographic gateways drive sampled, terminal-limited physical contacts", timeline=[])
    connectivity = [{"offset_seconds": f["offset_seconds"], "components": components([n["id"] for n in scenario["topology"]["nodes"]], f["links"])} for f in frames]
    scenario["physical_model"] = {"schema_version": 1, "reference_frame": "TEME", "config": config,
        "satellite_orbits": orbits.elements, "frames": frames, "connectivity": connectivity,
        "warmup_seconds": warmup, "time_scale": 1,
        "boundary": "Sampled geometry; SGP4 design/TLE or TEME OEM; reference-SNR capacity model; no attitude, weather, interference or RF waveform simulation. Initial protocol checks hold the epoch snapshot; physical replay uses an explicit 1:1 clock."}
    return scenario


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, required=True, help="Generated seed identities and business intents; its links are replaced")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    if len({p.resolve() for p in (args.scenario, args.config, args.output)}) != 3:
        parser.error("input and output files must differ")
    if args.summary and args.summary.resolve() in {p.resolve() for p in (args.scenario,args.config,args.output)}:
        parser.error("summary must not overwrite an input or scenario")
    seed = json.loads(args.scenario.read_text())
    nodes = seed["topology"]["nodes"]
    config = load_config(args.config, [n["id"] for n in nodes if n["kind"] == "satellite"], [n["id"] for n in nodes if n["kind"] == "gateway"])
    result = compile_scenario(seed, config)
    result["physical_model"]["input_sha256"] = {"seed": hashlib.sha256(args.scenario.read_bytes()).hexdigest(), "config": hashlib.sha256(args.config.read_bytes()).hexdigest()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, separators=(",", ":"), allow_nan=False) + "\n")
    model = result["physical_model"]
    summary={"physical_scenario": str(args.output), "satellites": len(model["satellite_orbits"]),
        "gateways": len(config["ground_stations"]), "frames": len(model["frames"]),
        "potential_pairs": len(result["topology"]["links"]) // 2,
        "active_pairs": [f["active_pairs"] for f in model["frames"]],
        "components": [len(f["components"]) for f in model["connectivity"]],
        "epoch":config["epoch"],"orbit_source":config["orbit"]["source"],"real_packet_data_plane":False,
        "boundary":"Generated physical model and deployment input; runtime and packet measurements require separate execution."}
    if args.summary:
        args.summary.parent.mkdir(parents=True,exist_ok=True)
        args.summary.write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
