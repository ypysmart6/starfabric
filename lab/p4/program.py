#!/usr/bin/env python3
"""Install and verify the StarFabric BMv2 pipeline through P4Runtime 1.4.1."""
import argparse
import json
import time
from pathlib import Path

import p4runtime_sh.shell as sh


def table_count(name: str) -> int:
    return sum(1 for _ in sh.TableEntry(name).read())


def counter_packets(name: str) -> int:
    return sum(entry.packet_count for entry in sh.DirectCounterEntry(name).read())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", default="127.0.0.1:19559")
    parser.add_argument("--p4info", default="build/starfabric.p4info.txtpb")
    parser.add_argument("--pipeline", default="build/starfabric.json")
    parser.add_argument("--wait-seconds", type=float, default=10.0)
    parser.add_argument("--report", default="artifacts/p4runtime.json")
    args = parser.parse_args()

    sh.setup(
        device_id=1,
        grpc_addr=args.address,
        election_id=(0, 1),
        config=sh.FwdPipeConfig(args.p4info, args.pipeline),
    )
    try:
        meter = sh.MeterEntry("IngressImpl.service_meter")
        meter.index = 1
        meter.cir = 1_000_000
        meter.cburst = 10_000
        meter.pir = 2_000_000
        meter.pburst = 20_000
        meter.modify()

        for table_name, field in (
            ("IngressImpl.qos_classification", "hdr.ipv4.diffserv"),
            ("IngressImpl.ipv6_qos_classification", "hdr.ipv6.traffic_class"),
        ):
            qos = sh.TableEntry(table_name)(action="IngressImpl.classify")
            qos.match[field] = "184"  # EF DSCP 46 including ECN bits.
            qos.action["traffic_class"] = "7"
            qos.insert()

        ipv4 = sh.TableEntry("IngressImpl.ipv4_lpm")(action="IngressImpl.set_nexthop")
        ipv4.priority = 10
        ipv4.match["hdr.ipv4.dst_addr"] = "198.51.100.0/24"
        ipv4.action["dst_mac"] = "02:00:00:00:02:01"
        ipv4.action["port"] = "2"
        ipv4.action["meter_index"] = "1"
        ipv4.insert()

        ipv6 = sh.TableEntry("IngressImpl.ipv6_lpm")(action="IngressImpl.set_nexthop")
        ipv6.priority = 10
        ipv6.match["hdr.ipv6.dst_addr"] = "2001:db8:200::/64"
        ipv6.action["dst_mac"] = "02:00:00:00:02:01"
        ipv6.action["port"] = "2"
        ipv6.action["meter_index"] = "1"
        ipv6.insert()

        counts = {
            "ipv4_lpm": table_count("IngressImpl.ipv4_lpm"),
            "ipv6_lpm": table_count("IngressImpl.ipv6_lpm"),
            "ipv4_qos": table_count("IngressImpl.qos_classification"),
            "ipv6_qos": table_count("IngressImpl.ipv6_qos_classification"),
        }
        expected = {"ipv4_lpm": 1, "ipv6_lpm": 1, "ipv4_qos": 1, "ipv6_qos": 1}
        if counts != expected:
            raise RuntimeError(f"P4Runtime table readback mismatch: {counts}")

        time.sleep(args.wait_seconds)
        counters = {
            "ipv4_packets": counter_packets("IngressImpl.ipv4_route_counter"),
            "ipv6_packets": counter_packets("IngressImpl.ipv6_route_counter"),
        }
        report = {
            "pipeline_installed": True,
            "p4runtime_version": "1.4.1",
            "meter_configured": True,
            "table_entries": counts,
            "direct_counters": counters,
        }
        Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        sh.teardown()


if __name__ == "__main__":
    main()
