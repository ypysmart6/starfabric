#!/usr/bin/env python3
"""Run and grade StarFabric 5G NTN transport experiments.

The runner does not emulate a UE. It observes a running UE namespace, mutates
only the explicitly configured N3 interface and StarFabric topology, captures
GTP-U, and emits a deterministic JSON result suitable for CI/HIL pipelines.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=Path("reports/ntn.json"))
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args()


def validate(manifest):
    required = {"id", "duration_s", "sample_interval_ms", "flows", "assertions"}
    missing = required.difference(manifest)
    if missing:
        raise ValueError(f"manifest missing {sorted(missing)}")
    if manifest["duration_s"] <= 0 or manifest["sample_interval_ms"] <= 0:
        raise ValueError("duration and sample interval must be positive")
    if not manifest["flows"]:
        raise ValueError("at least one UE flow is required")
    if manifest.get("ue_namespace") and manifest.get("ue_container"):
        raise ValueError("choose either ue_namespace or ue_container")
    if manifest.get("transport_namespace") and manifest.get("transport_container"):
        raise ValueError("choose either transport_namespace or transport_container")
    names = set()
    for flow in manifest["flows"]:
        if not flow.get("name") or flow["name"] in names:
            raise ValueError("flow names must be non-empty and unique")
        names.add(flow["name"])
        ipaddress.ip_address(flow.get("destination", ""))
        protocol = flow.get("protocol", "icmp")
        if protocol not in {"icmp", "tcp", "udp"}:
            raise ValueError(f"unsupported flow protocol: {protocol}")
        if protocol != "icmp" and not 1 <= int(flow.get("port", 0)) <= 65535:
            raise ValueError(f"{protocol} flow requires a valid port")
        if flow.get("source_interface") and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", flow["source_interface"]):
            raise ValueError("source_interface is not a valid Linux interface name")
        if not 0 <= int(flow.get("dscp", 0)) <= 63:
            raise ValueError("dscp must be between 0 and 63")
    for action in manifest.get("actions", []):
        if action.get("type") not in {"controller_event", "controller_netem", "netem", "clear_netem", "gateway_route"}:
            raise ValueError(f"unsupported action type: {action.get('type')}")
        if action.get("at_s", -1) < 0 or action["at_s"] > manifest["duration_s"]:
            raise ValueError(f"action outside experiment duration: {action}")
        if action.get("type") == "controller_netem" and not action.get("intent_id"):
            raise ValueError("controller_netem requires intent_id")


def scoped_command(namespace, container, command):
    if namespace and container:
        raise ValueError("namespace and container execution scopes are mutually exclusive")
    if namespace:
        return ["ip", "netns", "exec", namespace, *command]
    if container:
        return ["docker", "exec", container, *command]
    return command


def dscp_traffic_class(dscp):
    """Encode a six-bit DSCP as the eight-bit IP traffic-class/TOS value."""
    value = int(dscp)
    if not 0 <= value <= 63:
        raise ValueError("dscp must be between 0 and 63")
    return value << 2


def preflight(manifest):
    namespace = manifest.get("transport_namespace", "")
    container = manifest.get("transport_container", "")
    interface = manifest.get("n3_interface", "")
    if interface:
        result = subprocess.run(
            scoped_command(namespace, container, ["ip", "link", "show", "dev", interface]),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode:
            scope = f"namespace {namespace}" if namespace else (f"container {container}" if container else "the host namespace")
            raise RuntimeError(f"N3 interface {interface!r} does not exist in {scope}")
    if manifest["assertions"].get("require_gtpu_capture", False):
        if not shutil.which("tcpdump"):
            raise RuntimeError("require_gtpu_capture needs host tcpdump to verify the saved PCAP")
        if namespace or container:
            available = subprocess.run(
                scoped_command(namespace, container, ["sh", "-c", "command -v tcpdump"]),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode == 0
        else:
            available = True
        if not available:
            raise RuntimeError("require_gtpu_capture needs tcpdump in the transport execution scope")
    if manifest["assertions"].get("require_controller_closed_loop", False):
        request_controller(manifest, "/readyz")


def request_controller(manifest, path, data=None):
    endpoint = manifest.get("controller", "http://127.0.0.1:8080").rstrip("/")
    body = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(endpoint + path, data=body, method="POST" if body is not None else "GET", headers={"Content-Type": "application/json"})
    token_env = manifest.get("controller_token_env", "SF_TOKEN")
    if os.environ.get(token_env):
        request.add_header("Authorization", "Bearer " + os.environ[token_env])
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status >= 300:
                raise RuntimeError(f"controller returned {response.status}")
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"controller event failed: {exc.code} {exc.read().decode(errors='replace')}") from exc


def request_event(manifest, event):
    return request_controller(manifest, "/api/v1/topology/events?reconcile=true", event)


def replace_netem(manifest, delay_ms, loss_percent=0):
    interface = manifest.get("n3_interface", "")
    if not interface:
        raise ValueError("netem action requires n3_interface")
    command = ["tc", "qdisc", "replace", "dev", interface, "root", "netem", "delay", f"{delay_ms}ms", "loss", f"{loss_percent}%"]
    subprocess.run(scoped_command(manifest.get("transport_namespace", ""), manifest.get("transport_container", ""), command), check=True)


def apply_action(manifest, action):
    namespace = manifest.get("transport_namespace", "")
    container = manifest.get("transport_container", "")
    interface = manifest.get("n3_interface", "")
    if action["type"] == "controller_event":
        response = request_event(manifest, action["event"])
        return {"type": "controller_event", "event_id": action["event"].get("event_id"), "plan_id": response["plan"]["id"], "phase": response["status"]["phase"]}
    elif action["type"] == "controller_netem":
        response = request_controller(manifest, "/api/v1/status")
        plan = response.get("committed_plan") or {}
        paths = plan.get("paths", {}).get(action["intent_id"], [])
        if response.get("reconcile", {}).get("phase") != "committed" or not paths:
            raise RuntimeError(f"controller has no committed path for {action['intent_id']}")
        delay_ms = paths[0]["latency_us"] / 1000
        replace_netem(manifest, delay_ms, action.get("loss_percent", 0))
        return {"type": "controller_netem", "intent_id": action["intent_id"], "plan_id": plan["id"], "path": paths[0]["nodes"], "applied_delay_ms": delay_ms}
    elif action["type"] == "netem":
        replace_netem(manifest, action.get("delay_ms", 0), action.get("loss_percent", 0))
        return {"type": "netem", "applied_delay_ms": action.get("delay_ms", 0)}
    elif action["type"] == "clear_netem":
        command = ["tc", "qdisc", "del", "dev", interface, "root"]
        subprocess.run(
            scoped_command(namespace, container, command),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"type": "clear_netem"}
    elif action["type"] == "gateway_route":
        command = ["ip", "route", "replace", action["prefix"], "via", action["via"], "dev", interface]
        subprocess.run(scoped_command(namespace, container, command), check=True)
        return {"type": "gateway_route", "prefix": action["prefix"]}


def probe(namespace, container, flow, timeout_s):
    protocol = flow.get("protocol", "icmp")
    traffic_class = dscp_traffic_class(flow.get("dscp", 0))
    source_interface = flow.get("source_interface", "")
    if protocol == "icmp":
        command = ["ping", "-n", "-c", "1", "-W", str(max(1, int(timeout_s))), "-Q", str(traffic_class), flow["destination"]]
        if source_interface:
            command[1:1] = ["-I", source_interface]
    else:
        socket_type = "socket.SOCK_STREAM" if protocol == "tcp" else "socket.SOCK_DGRAM"
        operation = "s.connect(target)" if protocol == "tcp" else "s.sendto(b'starfabric-ntn', target); s.recvfrom(64)"
        bind_program = (
            f"s.setsockopt(socket.SOL_SOCKET,socket.SO_BINDTODEVICE,{(source_interface.encode() + bytes([0]))!r}); "
            if source_interface
            else ""
        )
        program = (
            "import socket; "
            f"family=socket.AF_INET6 if ':' in {flow['destination']!r} else socket.AF_INET; "
            f"s=socket.socket(family,{socket_type}); "
            + bind_program
            +
            f"s.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_TCLASS,{traffic_class}) if family == socket.AF_INET6 "
            f"else s.setsockopt(socket.IPPROTO_IP,socket.IP_TOS,{traffic_class}); "
            f"s.settimeout({float(timeout_s)!r}); target=({flow['destination']!r},{int(flow['port'])}); {operation}"
        )
        command = ["python3", "-c", program]
    started = time.monotonic_ns()
    result = subprocess.run(scoped_command(namespace, container, command), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0, (time.monotonic_ns() - started) / 1_000_000


def percentile(values, percentile_value):
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile_value / 100
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def verify_gtpu_capture(path):
    """Require tcpdump to decode at least one UDP/2152 packet from the PCAP."""
    if not path.exists() or path.stat().st_size <= 24 or not shutil.which("tcpdump"):
        return False
    result = subprocess.run(
        ["tcpdump", "-nn", "-r", str(path), "-c", "1", "udp", "port", "2152"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def main():
    args = parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    validate(manifest)
    if args.validate_only:
        print(f'PASS: {manifest["id"]}')
        return
    preflight(manifest)
    namespace = manifest.get("ue_namespace", "")
    ue_container = manifest.get("ue_container", "")
    interval = manifest["sample_interval_ms"] / 1000
    deadline = time.monotonic() + manifest["duration_s"]
    actions = sorted(manifest.get("actions", []), key=lambda item: item["at_s"])
    action_index = 0
    action_evidence = []
    results = {flow["name"]: {"sent": 0, "received": 0, "latencies_ms": [], "longest_outage_ms": 0.0} for flow in manifest["flows"]}
    outage_start = {flow["name"]: None for flow in manifest["flows"]}
    started = time.monotonic()
    capture = None
    capture_file = None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    capture_path = args.output.with_suffix(".gtpu.pcap")
    transport_container = manifest.get("transport_container", "")
    capture_available = shutil.which("tcpdump") or transport_container or manifest.get("transport_namespace")
    if manifest.get("n3_interface") and capture_available:
        capture_target = "-" if transport_container else str(capture_path)
        command = ["tcpdump", "-U", "-i", manifest["n3_interface"], "-w", capture_target, "udp", "port", "2152"]
        if transport_container:
            capture_file = capture_path.open("wb")
        capture = subprocess.Popen(
            scoped_command(manifest.get("transport_namespace", ""), transport_container, command),
            stdout=capture_file or subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    try:
        while time.monotonic() < deadline:
            elapsed = time.monotonic() - started
            while action_index < len(actions) and actions[action_index]["at_s"] <= elapsed:
                evidence = apply_action(manifest, actions[action_index])
                if evidence:
                    evidence["at_s"] = actions[action_index]["at_s"]
                    action_evidence.append(evidence)
                action_index += 1
            for flow in manifest["flows"]:
                state = results[flow["name"]]
                ok, latency = probe(namespace, ue_container, flow, interval)
                state["sent"] += 1
                if ok:
                    state["received"] += 1
                    state["latencies_ms"].append(latency)
                    if outage_start[flow["name"]] is not None:
                        state["longest_outage_ms"] = max(state["longest_outage_ms"], (time.monotonic() - outage_start[flow["name"]]) * 1000)
                        outage_start[flow["name"]] = None
                elif outage_start[flow["name"]] is None:
                    outage_start[flow["name"]] = time.monotonic()
            time.sleep(interval)
    finally:
        if capture:
            capture.terminate()
            try:
                capture.wait(timeout=5)
            except subprocess.TimeoutExpired:
                capture.kill()
                capture.wait(timeout=5)
        if capture_file:
            capture_file.close()
        if manifest.get("n3_interface"):
            apply_action(manifest, {"type": "clear_netem"})
    failed = False
    for name, state in results.items():
        if outage_start[name] is not None:
            state["longest_outage_ms"] = max(state["longest_outage_ms"], (time.monotonic() - outage_start[name]) * 1000)
        state["loss_percent"] = 100.0 if not state["sent"] else (state["sent"] - state["received"]) * 100 / state["sent"]
        latencies = state.pop("latencies_ms")
        state["latency_ms"] = {
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "p99": percentile(latencies, 99),
            "max": max(latencies, default=0.0),
        }
        if state["loss_percent"] > manifest["assertions"]["max_loss_percent"] or state["longest_outage_ms"] > manifest["assertions"]["max_outage_ms"]:
            failed = True
        if state["latency_ms"]["p99"] > manifest["assertions"].get("max_p99_latency_ms", float("inf")):
            failed = True
    capture_valid = verify_gtpu_capture(capture_path)
    if manifest["assertions"].get("require_gtpu_capture", False) and not capture_valid:
        failed = True
    controller = None
    if manifest["assertions"].get("require_controller_closed_loop", False):
        try:
            controller = request_controller(manifest, "/api/v1/status")
            controller_actions = [item for item in action_evidence if item["type"] == "controller_event"]
            channel_actions = [item for item in action_evidence if item["type"] == "controller_netem"]
            controller_ok = (
                controller.get("reconcile", {}).get("phase") == "committed"
                and controller.get("committed_plan")
                and controller_actions
                and channel_actions
                and all(item.get("phase") == "committed" for item in controller_actions)
                and all(item.get("applied_delay_ms", 0) > 0 for item in channel_actions)
            )
        except Exception as exc:
            controller = {"error": str(exc)}
            controller_ok = False
        if not controller_ok:
            failed = True
    report = {
        "experiment_id": manifest["id"],
        "success": not failed,
        "flows": results,
        "capture": str(capture_path) if capture_valid else None,
        "gtpu_capture_verified": capture_valid,
        "actions": action_evidence,
        "controller": controller,
        "controller_closed_loop": controller_ok if manifest["assertions"].get("require_controller_closed_loop", False) else None,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if failed:
        raise SystemExit("NTN experiment SLO failed")


if __name__ == "__main__":
    main()
