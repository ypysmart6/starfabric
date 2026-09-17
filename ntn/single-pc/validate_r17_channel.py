#!/usr/bin/env python3
"""Validate the Rel-17 NTN profile and live complex-sample ZMQ channel."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml
import zmq

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ntn.channel_emulator import ChannelConfig, serve


PROFILE = Path(__file__).with_name("geo-ntn-r17.yaml")
REPORT = ROOT / "reports/ntn-r17-channel.json"
C = 299_792_458.0


def geodetic_ecef(latitude: float, longitude: float, altitude: float) -> tuple[float, float, float]:
    semi_major = 6_378_137.0
    eccentricity_squared = 6.69437999014e-3
    lat, lon = math.radians(latitude), math.radians(longitude)
    prime_vertical = semi_major / math.sqrt(1 - eccentricity_squared * math.sin(lat) ** 2)
    return (
        (prime_vertical + altitude) * math.cos(lat) * math.cos(lon),
        (prime_vertical + altitude) * math.cos(lat) * math.sin(lon),
        (prime_vertical * (1 - eccentricity_squared) + altitude) * math.sin(lat),
    )


def run_channel(config: ChannelConfig, input_frequency_hz: float, blocks: int = 8) -> dict[str, float | int]:
    context = zmq.Context()
    identifier = uuid.uuid4().hex
    input_endpoint = f"inproc://ntn-input-{identifier}"
    output_endpoint = f"inproc://ntn-output-{identifier}"
    ready = threading.Event()
    holder: dict[str, object] = {}

    def channel() -> None:
        try:
            holder["stats"] = serve(
                config,
                input_endpoint,
                output_endpoint,
                context=context,
                expected_blocks=blocks,
                ready=ready,
            )
        except BaseException as error:  # propagate thread failures to the gate
            holder["error"] = error

    worker = threading.Thread(target=channel, name="ntn-zmq-channel")
    worker.start()
    if not ready.wait(timeout=2):
        raise AssertionError("channel sockets did not become ready")
    source, sink = context.socket(zmq.PUSH), context.socket(zmq.PULL)
    source.linger = sink.linger = 0
    source.connect(input_endpoint)
    sink.connect(output_endpoint)
    samples_per_block = 4096
    total = samples_per_block * blocks
    indices = np.arange(total, dtype=np.float64)
    transmitted = np.exp(2j * np.pi * input_frequency_hz * indices / config.sample_rate_hz).astype(np.complex64)
    started = time.monotonic()
    for block in np.array_split(transmitted, blocks):
        source.send(block.tobytes())
    received: list[np.ndarray] = []
    first_received = 0.0
    for _ in range(blocks):
        payload = sink.recv()
        if not received:
            first_received = time.monotonic()
        received.append(np.frombuffer(payload, dtype=np.complex64).copy())
    worker.join(timeout=2)
    source.close()
    sink.close()
    context.term()
    if worker.is_alive():
        raise AssertionError("channel worker did not terminate")
    if "error" in holder:
        raise holder["error"]  # type: ignore[misc]
    output = np.concatenate(received)
    phase_step = np.angle(output[1:] * np.conj(output[:-1]))
    measured_frequency = float(np.median(phase_step) * config.sample_rate_hz / (2 * np.pi))
    return {
        "blocks": blocks,
        "samples": int(output.size),
        "first_sample_delay_ms": (first_received - started) * 1000,
        "measured_frequency_hz": measured_frequency,
        "mean_amplitude": float(np.mean(np.abs(output))),
        **holder["stats"],  # type: ignore[arg-type]
    }


def main() -> None:
    profile = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
    cell = profile["cell_cfg"]
    ntn = profile["ntn"]
    channel = profile["channel"]
    ground = profile["ue_ground_position"]
    satellite = ntn["ephemeris_info_ecef"]
    ground_ecef = geodetic_ecef(ground["latitude"], ground["longitude"], ground["altitude_m"])
    range_m = math.sqrt(sum(
        (satellite[axis] - ground_ecef[index]) ** 2
        for index, axis in enumerate(("pos_x", "pos_y", "pos_z"))
    ))
    physical_delay_us = range_m / C * 1_000_000
    physical_rtt_ms = 2 * range_m / C * 1000

    config = ChannelConfig(
        sample_rate_hz=float(channel["sample_rate_hz"]),
        delay_us=float(channel["one_way_delay_us"]),
        doppler_hz=float(channel["validation_doppler_hz"]),
        path_loss_db=float(channel["path_loss_db"]),
    )
    nominal_frequency = 20_000.0
    uncompensated = run_channel(config, nominal_frequency)
    compensated = run_channel(config, nominal_frequency - config.doppler_hz)
    expected_amplitude = 10 ** (-config.path_loss_db / 20)
    sib_mappings = {entry["sib_mapping"] for entry in cell["sib"]["si_sched_info"]}
    checks = {
        "release_17_ntn_profile": profile["release"] == 17,
        "band_256_arfcn": cell["band"] == 256 and cell["dl_arfcn"] == 437000,
        "sib19_scheduled": 19 in sib_mappings,
        "ephemeris_broadcast_fields": set(satellite) == {"pos_x", "pos_y", "pos_z", "vel_x", "vel_y", "vel_z"},
        "common_timing_advance_matches_rtt": abs(ntn["cell_specific_koffset"] - round(physical_rtt_ms)) <= 1,
        "extended_ntn_timers": cell["sib"]["t300"] == 2000 and cell["sib"]["t301"] == 2000
        and cell["sib"]["t311"] == 3000 and cell["sib"]["t319"] == 2000,
        "harq_retransmissions_disabled": cell["pdsch"]["max_nof_harq_retxs"] == 0
        and cell["prach"]["max_msg3_harq_retx"] == 0,
        "preamble_format_1_configuration": cell["prach"]["prach_config_index"] == 31,
        "profile_delay_matches_ephemeris": abs(channel["one_way_delay_us"] - physical_delay_us) < 100,
        "live_zmq_complex_sample_channel": uncompensated["forwarded_blocks"] == uncompensated["received_blocks"] == 8,
        "channel_delay_applied": uncompensated["first_sample_delay_ms"] >= channel["one_way_delay_us"] / 1000 * 0.95,
        "doppler_frequency_injected": abs(uncompensated["measured_frequency_hz"] - (nominal_frequency + config.doppler_hz)) < 5,
        "doppler_precompensation": abs(compensated["measured_frequency_hz"] - nominal_frequency) < 5,
        "path_loss_applied": abs(uncompensated["mean_amplitude"] - expected_amplitude) < 0.01,
    }
    report = {
        "schema_version": 1,
        "success": all(checks.values()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence_level": "Rel-17 NTN gNB profile semantics + live ZeroMQ complex-sample delay/Doppler/path-loss channel",
        "checks": checks,
        "physics": {
            "slant_range_m": range_m,
            "one_way_delay_us": physical_delay_us,
            "round_trip_ms": physical_rtt_ms,
        },
        "uncompensated": uncompensated,
        "compensated": compensated,
        "artifacts": {"profile_sha256": hashlib.sha256(PROFILE.read_bytes()).hexdigest()},
        "boundary": "The channel is software IQ/ZMQ; commercial NTN UE, RF front end and over-the-air behavior are not claimed.",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, REPORT)
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["success"]:
        raise SystemExit("Rel-17 NTN channel checks failed")


if __name__ == "__main__":
    main()
