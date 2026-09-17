#!/usr/bin/env python3
"""Bounded ZeroMQ complex-sample NTN delay/Doppler channel emulator."""

from __future__ import annotations

import argparse
import heapq
import json
import math
import signal
import threading
import time
from dataclasses import dataclass

import numpy as np
import zmq


@dataclass(frozen=True)
class ChannelConfig:
    sample_rate_hz: float
    delay_us: float
    doppler_hz: float
    path_loss_db: float = 0.0
    delay_rate_us_per_s: float = 0.0
    doppler_rate_hz_per_s: float = 0.0

    def validate(self) -> None:
        if not math.isfinite(self.sample_rate_hz) or self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive and finite")
        if not math.isfinite(self.delay_us) or self.delay_us < 0:
            raise ValueError("delay_us must be non-negative and finite")
        if not math.isfinite(self.doppler_hz):
            raise ValueError("doppler_hz must be finite")
        if not math.isfinite(self.path_loss_db) or self.path_loss_db < 0:
            raise ValueError("path_loss_db must be non-negative and finite")


class SampleChannel:
    def __init__(self, config: ChannelConfig):
        config.validate()
        self.config = config
        self.sample_cursor = 0

    def transform(self, payload: bytes) -> bytes:
        if len(payload) == 0 or len(payload) % np.dtype(np.complex64).itemsize:
            raise ValueError("each channel message must contain complex64 IQ samples")
        samples = np.frombuffer(payload, dtype=np.complex64).copy()
        start_seconds = self.sample_cursor / self.config.sample_rate_hz
        indices = np.arange(samples.size, dtype=np.float64) + self.sample_cursor
        elapsed = indices / self.config.sample_rate_hz
        phase = 2 * np.pi * (
            self.config.doppler_hz * elapsed
            + 0.5 * self.config.doppler_rate_hz_per_s * elapsed * elapsed
        )
        attenuation = 10 ** (-self.config.path_loss_db / 20)
        samples *= (attenuation * np.exp(1j * phase)).astype(np.complex64)
        self.sample_cursor += samples.size
        return samples.tobytes()

    def delay_seconds(self) -> float:
        sample_time = self.sample_cursor / self.config.sample_rate_hz
        delay_us = self.config.delay_us + self.config.delay_rate_us_per_s * sample_time
        return max(delay_us, 0.0) / 1_000_000


def serve(
    config: ChannelConfig,
    input_endpoint: str,
    output_endpoint: str,
    *,
    context: zmq.Context | None = None,
    bind: bool = True,
    expected_blocks: int = 0,
    ready: threading.Event | None = None,
    stop: threading.Event | None = None,
) -> dict[str, int | float]:
    owned_context = context is None
    context = context or zmq.Context()
    pull = context.socket(zmq.PULL)
    push = context.socket(zmq.PUSH)
    pull.linger = push.linger = 0
    operation = "bind" if bind else "connect"
    getattr(pull, operation)(input_endpoint)
    getattr(push, operation)(output_endpoint)
    if ready is not None:
        ready.set()

    channel = SampleChannel(config)
    poller = zmq.Poller()
    poller.register(pull, zmq.POLLIN)
    queue: list[tuple[float, int, bytes]] = []
    received = forwarded = sample_count = 0
    sequence = 0
    started = time.monotonic()
    receive_done = False
    try:
        while not (receive_done and not queue):
            now = time.monotonic()
            timeout_ms = 10
            if queue:
                timeout_ms = max(0, min(timeout_ms, math.ceil((queue[0][0] - now) * 1000)))
            events = dict(poller.poll(timeout_ms))
            if pull in events and not receive_done:
                payload = pull.recv()
                transformed = channel.transform(payload)
                sample_count += len(payload) // np.dtype(np.complex64).itemsize
                received += 1
                sequence += 1
                heapq.heappush(
                    queue,
                    (time.monotonic() + channel.delay_seconds(), sequence, transformed),
                )
                if expected_blocks and received >= expected_blocks:
                    receive_done = True
                    poller.unregister(pull)
            now = time.monotonic()
            while queue and queue[0][0] <= now:
                _, _, payload = heapq.heappop(queue)
                push.send(payload)
                forwarded += 1
            if stop is not None and stop.is_set() and not queue:
                break
    finally:
        pull.close()
        push.close()
        if owned_context:
            context.term()
    return {
        "received_blocks": received,
        "forwarded_blocks": forwarded,
        "samples": sample_count,
        "elapsed_ms": (time.monotonic() - started) * 1000,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="ZeroMQ PULL endpoint")
    parser.add_argument("--output", required=True, help="ZeroMQ PUSH endpoint")
    parser.add_argument("--sample-rate-hz", required=True, type=float)
    parser.add_argument("--delay-us", required=True, type=float)
    parser.add_argument("--doppler-hz", required=True, type=float)
    parser.add_argument("--path-loss-db", type=float, default=0)
    parser.add_argument("--delay-rate-us-per-s", type=float, default=0)
    parser.add_argument("--doppler-rate-hz-per-s", type=float, default=0)
    parser.add_argument("--connect", action="store_true", help="connect sockets instead of binding them")
    args = parser.parse_args()
    stopping = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    stats = serve(
        ChannelConfig(
            sample_rate_hz=args.sample_rate_hz,
            delay_us=args.delay_us,
            doppler_hz=args.doppler_hz,
            path_loss_db=args.path_loss_db,
            delay_rate_us_per_s=args.delay_rate_us_per_s,
            doppler_rate_hz_per_s=args.doppler_rate_hz_per_s,
        ),
        args.input,
        args.output,
        bind=not args.connect,
        stop=stopping,
    )
    print(json.dumps(stats, sort_keys=True))


if __name__ == "__main__":
    main()
