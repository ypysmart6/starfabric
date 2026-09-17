#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def route_map(payload: dict) -> dict[str, list[dict]]:
    return {device["device"]: device.get("routes") or [] for device in payload["devices"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--initial", required=True)
    parser.add_argument("--switched", required=True)
    parser.add_argument("--devices", required=True)
    parser.add_argument("--restarted-devices", required=True)
    parser.add_argument("--restarted-status", required=True)
    parser.add_argument("--management-probe", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    initial = load(args.initial)
    switched = load(args.switched)
    devices_payload = load(args.devices)
    restarted_devices_payload = load(args.restarted_devices)
    restarted_status = load(args.restarted_status)
    management = load(args.management_probe)
    devices = route_map(devices_payload)
    restarted_devices = route_map(restarted_devices_payload)

    checks = {
        "initial_committed": initial["status"]["phase"] == "committed",
        "initial_primary_path": initial["plan"]["paths"]["service"][0]["nodes"]
        == ["edge", "sat-primary", "gateway"],
        "switch_committed": switched["status"]["phase"] == "committed",
        "backup_path_selected": switched["plan"]["paths"]["service"][0]["nodes"]
        == ["edge", "sat-backup", "gateway"],
        "edge_fib_switched": len(devices["edge"]) == 1
        and devices["edge"][0]["next_hop"] == "10.255.0.3",
        "primary_fib_withdrawn": devices["sat-primary"] == [],
        "backup_fib_programmed": len(devices["sat-backup"]) == 1
        and devices["sat-backup"][0]["next_hop"] == "10.255.0.4",
        "all_targets_fib_verified": all(
            target.get("healthy") and target.get("message") == "gRIBI FIB state verified"
            for target in devices_payload["devices"]
        ),
        "fib_survives_controller_restart": restarted_devices == devices,
        "committed_plan_survives_restart": restarted_status["reconcile"]["phase"] == "committed"
        and restarted_status["committed_plan"]["id"] == switched["plan"]["id"],
        "gnmi_capabilities_get_set_subscribe": management["success"]
        and management["gnmi_capabilities"]
        and management["gnmi_set_json_ietf"]
        and management["gnmi_get_json_ietf"]
        and management["gnmi_subscribe_samples"] >= 2,
        "gnoi_ping_and_time": management["gnoi_ping_replies"] == 2
        and management["gnoi_time_nanoseconds"] > 0,
        "gnoi_scheduled_reboot_cancelled": management["gnoi_reboot_scheduled"]
        and management["gnoi_reboot_cancelled"],
        "gnoi_diagnostics_certificate_pcap_software": management[
            "gnoi_bert_zero_errors"
        ]
        and management["gnoi_certificate_count"] == 1
        and management["gnoi_captured_packets"] == 2
        and management["gnoi_os_version"] == "emulator-v2",
    }
    report = {
        "success": all(checks.values()),
        "evidence_level": "live local gNMI + gNOI System/Diag/Certificate/PacketCapture/OS + gRIBI target RPCs with FIB ACK",
        "scope": "software OpenConfig target; vendor ASIC and physical interfaces are excluded",
        "checks": checks,
        "initial_plan_id": initial["plan"]["id"],
        "switched_plan_id": switched["plan"]["id"],
        "final_routes": devices,
        "management_protocols": management,
    }
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["success"]:
        raise SystemExit("OpenConfig closed-loop assertions failed")


if __name__ == "__main__":
    main()
