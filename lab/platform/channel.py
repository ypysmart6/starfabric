"""Shared packet-impairment contract for deployment, orbit replay and live tests."""
from __future__ import annotations
import math


def parameters(link, queue_limit=10000):
    values = {"delay_us": link["latency_us"], "rate_bps": link["capacity_bps"],
              "loss_ppm": link.get("loss_ppm", 0), "queue_limit_packets": queue_limit}
    for name, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value:
            raise ValueError(f"invalid integer channel parameter {name}")
    if not 0 <= values["delay_us"] <= 1000000000 or not 8 <= values["rate_bps"] <= 10**12:
        raise ValueError("delay or capacity outside packet-emulator bounds")
    if not 0 <= values["loss_ppm"] <= 1000000 or not 1 <= queue_limit <= 1000000:
        raise ValueError("loss or queue limit outside packet-emulator bounds")
    return values


def qdisc_command(interface, link, queue_limit=10000, operation="replace"):
    p = parameters(link, queue_limit)
    if operation not in ("replace", "change"):
        raise ValueError("invalid qdisc operation")
    return ["tc", "qdisc", operation, "dev", interface, "root", "handle", "10:", "netem",
            "limit", str(p["queue_limit_packets"]), "delay", str(p["delay_us"]) + "us",
            "rate", str(p["rate_bps"]) + "bit", "loss", f'{p["loss_ppm"] / 10000:.6f}%']


def frr_cost(link):
    return max(1, min(65535, math.ceil(link["latency_us"] / 1000)))


def frr_bandwidth(link):
    # FRR link-params max-bw is bytes/s; scenario capacity_bps is bits/s.
    return format(link.get("capacity_bps", 1000000000) / 8, ".12g")
