#!/usr/bin/env python3
"""Connect orbital prediction, FRR, live 5G GTP-U and real onboard autonomy.

Invoked by ntn/single-pc/run.sh --unified after the owned 5G stack is ready.
Only resources carrying this run's identity are created and removed here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from noc import NOC

ROOT = Path(__file__).resolve().parents[2]
FRR = "quay.io/frrouting/frr@sha256:65e5967b922572c0565d968388fb06af69d7e9b3b3eea40ad7e3810687667f68"
TOOLS = "ghcr.io/herlesupreeth/docker_open5gs@sha256:985d89d67ad9c3e25ea8a98b0897c14a2420dc15909c69f3e0794a043327407c"
REPORT = ROOT / "reports/unified-runtime.json"
GNB, UPF = "172.22.0.23", "172.22.0.8"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def command(*args: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"{args[:6]} exited {result.returncode}: {result.stdout[-4000:]} {result.stderr[-4000:]}")
    return result


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temp.replace(path)


def wait_for(label: str, predicate: Callable[[], Any], timeout: int = 30) -> Any:
    deadline, last = time.monotonic() + timeout, None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (RuntimeError, urllib.error.URLError, TimeoutError, KeyError, ValueError) as error:
            last = str(error)
        time.sleep(0.2)
    raise AssertionError(f"{label} timed out: {last}")


class Runtime:
    def __init__(self) -> None:
        self.identifier = "sf-unified-" + uuid.uuid4().hex[:10]
        self.art = ROOT / "lab/unified/artifacts" / self.identifier
        self.art.mkdir(parents=True)
        self.containers: list[str] = []
        self.networks: list[str] = []
        self.external_links: list[tuple[str, str]] = []
        self.interfaces: dict[tuple[str, str], str] = {}
        self.controller: subprocess.Popen[Any] | None = None
        self.controller_log: Any = None
        self.stop_heartbeat = threading.Event()
        self.heartbeat_enabled = threading.Event()
        self.heartbeat_thread: threading.Thread | None = None
        self.generation = 1
        self.timeline: list[Any] = []
        self.checks: dict[str, Any] = {}
        self.traffic: list[Any] = []
        self.captures: dict[str, str] = {}
        self.routes_added: list[tuple[str, str, str]] = []
        self.continuous: subprocess.Popen[Any] | None = None
        self.continuous_log: Any = None
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.endpoint = f"http://127.0.0.1:{self.port}"
        self.noc = NOC(self)

    def event(self, name: str, **data: Any) -> None:
        value = {"run_id": self.identifier, "at": now(), "event": name, **data}
        self.timeline.append(value)
        save(self.art / "timeline.json", self.timeline)
        with (self.art / "timeline.jsonl").open("a") as stream:
            stream.write(json.dumps(value) + "\n")
        print(json.dumps(value, ensure_ascii=False), flush=True)

    def router(self, node: str) -> str:
        return self.identifier + "-" + node

    def exec(self, name: str, *args: str, check: bool = True) -> str:
        return command("docker", "exec", name, *args, check=check).stdout

    def vty(self, node: str, *commands: str) -> str:
        args = [part for item in commands for part in ("-c", item)]
        return self.exec(self.router(node), "vtysh", *args)

    def configure(self, node: str, *commands: str) -> None:
        self.vty(node, "configure terminal", *commands, "end")

    def request(self, path: str, body: Any = None) -> Any:
        trace_id = uuid.uuid4().hex if getattr(self, 'live_mode', False) else self.noc.trace_id
        req = urllib.request.Request(self.endpoint + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-Request-ID": self.identifier,
                                              "traceparent": f"00-{trace_id}-0123456789abcdef-01"})
        try:
            with urllib.request.urlopen(req, timeout=getattr(self, "request_timeout", 15)) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            body=error.read().decode(errors='replace')
            save(self.art/'last-http-error.json',{'path':path,'status':error.code,'body':body,'at':now()})
            raise RuntimeError(f'controller {path}: HTTP {error.code}: {body}') from error

    def onboard_request(self, node: str, path: str, body: Any = None) -> Any:
        args = ["curl", "--silent", "--show-error", "--fail", "--max-time", "2",
                "-H", "Content-Type: application/json"]
        if body is not None:
            args += ["--data-binary", json.dumps(body)]
        return json.loads(self.exec(self.router(node) + "-onboard", *args, "http://127.0.0.1:19080" + path))

    def compile_orbit(self) -> None:
        self.event("compile_orbital_scenario")
        orbit = ROOT / "lab/orbit-closed-loop"
        inputs = orbit / "inputs"
        oem = self.art / "oem"
        oem.mkdir()
        epoch = "2026-09-04T00:00:00Z"
        def run(path: Path, *args: str) -> None:
            result = command("python3", str(path), *args, timeout=180)
            (self.art / (path.stem + ".log")).write_text(result.stdout + result.stderr)
        run(ROOT / "tools/tle_to_oem.py", "--catalog", str(inputs / "tle-catalog.json"), "--start", epoch,
            "--duration-seconds", "86400", "--step-seconds", "10", "--output-dir", str(oem))
        run(ROOT / "tools/ephemeris_contacts.py", "--oem", f"orbit-a={oem}/orbit-a.oem", "--oem", f"orbit-b={oem}/orbit-b.oem",
            "--ground-stations", str(inputs / "ground-stations.json"), "--minimum-elevation-deg", "10", "--carrier-hz", "20000000000",
            "--output", str(self.art / "contacts.csv"))
        run(ROOT / "tools/contactplan.py", "--base", str(inputs / "contact-plan-base.json"), "--contacts", str(self.art / "contacts.csv"),
            "--output", str(self.art / "contact-scenario.json"))
        run(orbit / "compile_replay.py", "--contacts", str(self.art / "contacts.csv"), "--catalog", str(inputs / "tle-catalog.json"),
            "--contact-scenario", str(self.art / "contact-scenario.json"), "--oem", str(oem / "orbit-a.oem"), "--oem", str(oem / "orbit-b.oem"),
            "--epoch", epoch, "--scenario", str(self.art / "scenario.json"), "--event-forward", str(self.art / "event-forward.json"),
            "--event-reverse", str(self.art / "event-reverse.json"), "--selection", str(self.art / "orbit-selection.json"))
        self.scenario = json.loads((self.art / "scenario.json").read_text())
        self.scenario["scenario_id"] = self.identifier
        self.scenario["description"] = "TLE-derived contact handover carrying a real Open5GS/UERANSIM PDU session"
        for node in self.scenario["topology"]["nodes"]:
            node["labels"]["frr_container"] = self.router(node["id"])
        self.scenario["intents"][0]["destination_prefix"] = UPF + "/32"
        self.scenario["intents"][1]["destination_prefix"] = GNB + "/32"
        save(self.art / "scenario.json", self.scenario)

    def network(self) -> None:
        self.event("create_frr_forwarding_network")
        for name in ("nr_gnb", "upf", "nr_ue"):
            info = json.loads(command("docker", "inspect", name).stdout)[0]
            assert info["Config"]["Labels"].get("com.docker.compose.project") == "starfabric-5g"
            assert info["State"]["Running"]
        config = getattr(self, "frr_config_dir", ROOT / "lab/containerlab/phase1-frr-otg/configs")
        for n in range(1, 5):
            node = f"r{n}"
            conf = self.art / f"{node}.conf"
            conf.write_text(f"frr defaults traditional\nhostname {node}\nservice integrated-vtysh-config\nlog stdout informational\n")
            name = self.router(node)
            command("docker", "run", "-d", "--name", name, "--label", f"starfabric.run={self.identifier}", "--network=none",
                    "--cap-add=NET_ADMIN", "--cap-add=NET_RAW", "--cap-add=SYS_ADMIN", "--sysctl", "net.ipv4.ip_forward=1",
                    "--sysctl", "net.ipv4.conf.all.rp_filter=0", *getattr(self, "frr_extra_args", []), "-v", f"{config}/daemons:/etc/frr/daemons:ro",
                    "-v", f"{config}/vtysh.conf:/etc/frr/vtysh.conf:ro", "-v", f"{conf}:/etc/frr/frr.conf:ro", FRR)
            self.containers.append(name)
        edges = [("r1", "nr_gnb", 1), ("r1", "r2", 12), ("r1", "r3", 13),
                 ("r2", "r4", 24), ("r3", "r4", 34), ("r2", "r3", 23), ("r4", "upf", 6)]
        for left, right, subnet in edges:
            names = [self.router(n) if n.startswith("r") and n[1:].isdigit() else n for n in (left, right)]
            pids = [json.loads(command("docker", "inspect", name).stdout)[0]["State"]["Pid"] for name in names]
            interface, peer = f"sf{subnet}", f"peer{subnet}"
            # Create the veth inside the owned router namespace and move only
            # its peer to the other lab namespace. The host has no link, route,
            # NAT rule or Docker bridge in the business forwarding path.
            helper = ["docker", "run", "--rm", "--privileged", "--pid=host", "--network=none", "--entrypoint", "nsenter",
                      TOOLS, "-t", str(pids[0]), "-n", "--", "ip", "link"]
            command(*helper, "add", interface, "type", "veth", "peer", "name", peer)
            command(*helper, "set", peer, "netns", str(pids[1]))
            self.exec(names[1], "ip", "link", "set", peer, "name", interface)
            for node, name, address in ((left, names[0], f"10.231.{subnet}.2/29"), (right, names[1], f"10.231.{subnet}.3/29")):
                self.exec(name, "ip", "address", "add", address, "dev", interface)
                self.exec(name, "ip", "link", "set", interface, "up")
                self.interfaces[node, right if node == left else left] = interface
        for n in range(1, 5):
            node = f"r{n}"
            wait_for(node + " daemons", lambda: all(d in self.vty(node, "show daemons") for d in ("mgmtd", "zebra", "isisd", "staticd")))
            self.configure(node, "interface lo", f"ip address 10.255.0.{n}/32", "ip router isis SF", "isis passive", "exit",
                           "router isis SF", f"net 49.0001.0000.0000.000{n}.00", "is-type level-2-only", "metric-style wide",
                           "lsp-gen-interval 1", "spf-interval 1", "exit")
            for (source, target), interface in self.interfaces.items():
                if source == node and target.startswith("r") and target[1:].isdigit():
                    self.configure(node, "interface " + interface, "ip router isis SF", "isis network point-to-point",
                                   "isis hello-interval 1", "isis hello-multiplier 3", "isis metric level-2 10", "exit")
            # Prevent the host's Docker management routing from bypassing the
            # satellite graph if an intended /32 forwarding route disappears.
            self.exec(self.router(node), "ip", "route", "add", "blackhole", "172.22.0.0/16", "metric", "42799")
        self.configure("r1", f"ip route {GNB}/32 10.231.1.3")
        self.configure("r4", f"ip route {UPF}/32 10.231.6.3")
        wait_for("IS-IS loopback reachability", lambda: command("docker", "exec", self.router("r1"), "ping", "-I", "10.255.0.1", "-c", "1", "-W", "1", "10.255.0.4", check=False).returncode == 0, 60)
        self.checks["real_frr_isis_network"] = True

    def start_controller(self) -> None:
        self.controller_log = (self.art / "controller.log").open("a")
        self.controller = subprocess.Popen([str(ROOT / "bin/sf-controller"), "--scenario", str(self.art / "scenario.json"),
            "--adapter", "frr", "--state-dir", str(self.art / "controller-state"), "--listen", f"127.0.0.1:{self.port}",
            "--operation-timeout", f"{getattr(self, 'frr_operation_timeout', 2)}s",
            "--plan-ttl", f"{getattr(self, 'plan_ttl', 30)}s",
            "--http-write-timeout", f"{max(30, getattr(self, 'request_timeout', 15) + 5)}s",
            "--reconcile-interval", "0", "--otlp-endpoint", f"127.0.0.1:{self.noc.port('otlp')}",
            "--otlp-insecure"], cwd=ROOT, stdout=self.controller_log, stderr=subprocess.STDOUT)
        wait_for("controller readiness", lambda: self.request("/readyz"))

    def stop_controller(self) -> None:
        if self.controller and self.controller.poll() is None:
            self.controller.terminate()
            try:
                self.controller.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.controller.kill()
                self.controller.wait(timeout=5)
        if self.controller_log:
            self.controller_log.close()
            self.controller_log = None

    def reconcile(self, name: str) -> Any:
        response = self.request("/api/v1/reconcile", {})
        assert response["status"]["phase"] == "committed", response
        save(self.art / f"{name}.json", response)
        self.event(name, plan_id=response["plan"]["id"], topology_version=response["plan"]["topology_version"])
        return response

    def route_n3(self) -> None:
        self.exec("nr_gnb", "ip", "route", "add", UPF + "/32", "via", "10.231.1.2", "src", GNB)
        self.routes_added.append(("nr_gnb", UPF, "10.231.1.2"))
        self.exec("upf", "ip", "route", "add", GNB + "/32", "via", "10.231.6.2", "src", UPF)
        self.routes_added.append(("upf", GNB, "10.231.6.2"))
        save(self.art / "n3-routes.json", {"gnb": json.loads(self.exec("nr_gnb", "ip", "-j", "route", "get", UPF)),
                                           "upf": json.loads(self.exec("upf", "ip", "-j", "route", "get", GNB))})
        for node in ("r2", "r3"):
            name = self.router(node) + "-capture"
            command("docker", "run", "-d", "--name", name, "--label", f"starfabric.run={self.identifier}",
                    "--network", "container:" + self.router(node), "--cap-add=NET_RAW", "-v", f"{self.art}:/evidence",
                    "--entrypoint", "tcpdump", TOOLS, "-U", "-n", "-i", "any", "-w", f"/evidence/{node}-gtpu.pcap", "udp", "port", "2152")
            self.containers.append(name)
            self.captures[node] = name
        self.checks["gtpu_endpoints_routed_over_satellite_graph"] = True
        self.continuous_log = (self.art / "continuous-ping.log").open("w")
        self.continuous = subprocess.Popen(["docker", "exec", "nr_ue", "ping", "-n", "-D", "-I", "uesimtun0",
            "-i", "0.1", "-w", str(getattr(self, "business_duration", 60)), "-W", "1", "192.168.100.1"], stdout=self.continuous_log, stderr=subprocess.STDOUT)
        self.event("continuous_5g_business_started")

    def ping(self, phase: str, count: int = 10) -> Any:
        result = command("docker", "exec", "nr_ue", "ping", "-n", "-D", "-I", "uesimtun0", "-i", "0.1", "-c", str(count), "-W", "1", "192.168.100.1", check=False)
        (self.art / f"ping-{phase}.log").write_text(result.stdout + result.stderr)
        match = re.search(r"(\d+) packets transmitted, (\d+) received", result.stdout)
        assert match and result.returncode == 0, result.stdout + result.stderr
        tx, rx = map(int, match.groups())
        assert rx == tx == count, result.stdout
        record = {"phase": phase, "transmitted": tx, "received": rx, "at": now()}
        self.traffic.append(record)
        self.event("business_packets_verified", **record)
        return record

    def start_onboard(self) -> None:
        self.event("start_real_onboard_runtimes")
        for node, next_hop, peer in (("r2", "10.231.23.3", "r3"), ("r3", "10.231.34.3", "r4")):
            directory = self.art / (node + "-onboard")
            directory.mkdir()
            config = json.loads((ROOT / "onboard/config.example.json").read_text())
            config["node_id"] = self.identifier + "/" + node
            config["fallback_routes"] = [{"prefix": UPF + "/32", "via": next_hop,
                                          "interface": self.interfaces[node, peer], "metric": 42760}]
            save(directory / "config.json", config)
            name = self.router(node) + "-onboard"
            command("docker", "run", "-d", "--name", name, "--label", f"starfabric.run={self.identifier}",
                    "--network", "container:" + self.router(node), "--cap-add=NET_ADMIN", "-v", f"{directory}:/data",
                    "-v", f"{ROOT}/onboard/target/release/satellite-node-runtime:/sf-onboard:ro", "--entrypoint", "/sf-onboard", TOOLS,
                    "--config", "/data/config.json", "--state-dir", "/data/state", "--listen", "127.0.0.1:19080",
                    "--disconnect-hold-seconds", "2", "--heartbeat-timeout-seconds", "2")
            self.containers.append(name)
            wait_for(node + " onboard", lambda: self.onboard_request(node, "/v1/status"))
        self.heartbeat_enabled.set()
        def heartbeats() -> None:
            while not self.stop_heartbeat.wait(0.4):
                if not self.heartbeat_enabled.is_set():
                    continue
                try:
                    self.request("/readyz")
                    self.generation += 1
                    for node in ("r2", "r3"):
                        self.onboard_request(node, "/v1/connectivity", {"connected": True, "generation": self.generation})
                except (RuntimeError, OSError, urllib.error.URLError, ValueError):
                    # No synthetic disconnect event: the onboard hold timer
                    # must discover missing ground heartbeats on its own.
                    continue
        self.heartbeat_thread = threading.Thread(target=heartbeats, daemon=True)
        self.heartbeat_thread.start()
        wait_for("onboard connected", lambda: all(self.onboard_request(n, "/v1/status")["connected"] for n in ("r2", "r3")))

    def orbit_handover(self) -> None:
        self.event("schedule_orbital_handover")
        command("python3", str(ROOT / "lab/orbit-closed-loop/render_prediction.py"), "--scenario", str(self.art / "scenario.json"),
                "--output", str(self.art / "prediction.json"), "--timing", str(self.art / "timing.json"),
                "--handover-delay-seconds", "12", "--lead-seconds", "6")
        created = self.request("/api/v1/predictive/schedules", json.loads((self.art / "prediction.json").read_text()))
        save(self.art / "predictive-created.json", created)
        completed = wait_for("predictive activation", lambda: (lambda s: s if s["state"] == "completed" else None)(
            self.request("/api/v1/predictive/schedules?id=" + created["id"])), 25)
        save(self.art / "predictive-completed.json", completed)
        status = self.request("/api/v1/status")
        save(self.art / "predicted-status.json", status)
        path = status["committed_plan"]["paths"]["n3-forward"][0]
        assert path["nodes"] == ["r1", "r2", "r3", "r4"], path
        end = datetime.fromisoformat(json.loads((self.art / "timing.json").read_text())["wall_clock_contact_end"].replace("Z", "+00:00"))
        while datetime.now(timezone.utc) < end:
            time.sleep(0.05)
        self.exec(self.router("r2"), "ip", "link", "set", self.interfaces["r2", "r4"], "down")
        self.exec(self.router("r4"), "ip", "link", "set", self.interfaces["r4", "r2"], "down")
        self.event("orbital_contact_physically_removed", plan_id=status["committed_plan"]["id"],
                   topology_version=status["committed_plan"]["topology_version"])
        save(self.art / "fib-after-contact.json", json.loads(self.vty("r2", f"show ip route {UPF}/32 json")))
        self.ping("orbital-handover")
        self.checks["orbit_prediction_switches_real_gtpu_path"] = True

    def autonomy(self) -> None:
        self.event("ground_controller_stopped")
        self.stop_controller()
        for node in ("r2", "r3"):
            state = wait_for(node + " heartbeat timeout", lambda: (lambda s: s if s["fallback_active"] and not s["connected"] else None)(self.onboard_request(node, "/v1/status")), 12)
            save(self.art / f"{node}-autonomous.json", state)
        self.event("onboard_heartbeat_timeout_installed_routes")
        static = self.vty("r2", "show running-config")
        routes = [line.strip() for line in static.splitlines() if line.strip().startswith(f"ip route {UPF}/32 ")]
        assert routes, static
        self.configure("r2", *("no " + route for route in routes))
        self.event("ground_owned_route_withdrawn")
        active = json.loads(self.exec(self.router("r2"), "ip", "-j", "route", "get", UPF))
        assert active[0].get("gateway") == "10.231.23.3", active
        kernel = json.loads(self.exec(self.router("r2"), "ip", "-j", "route", "show", UPF + "/32"))
        assert any(v.get("metric") == 42760 and v.get("gateway") == "10.231.23.3" for v in kernel), kernel
        save(self.art / "autonomous-kernel-fib.json", {"lookup": active, "routes": kernel})
        self.ping("onboard-autonomy", 20)
        self.heartbeat_enabled.clear()
        self.start_controller()
        self.reconcile("ground-restored-commit")
        self.heartbeat_enabled.set()
        wait_for("fallback withdrawn after reconnect", lambda: all(not self.onboard_request(n, "/v1/status")["fallback_active"] for n in ("r2", "r3")))
        kernel = json.loads(self.exec(self.router("r2"), "ip", "-j", "route", "show", UPF + "/32"))
        assert not any(v.get("metric") == 42760 for v in kernel), kernel
        save(self.art / "restored-kernel-fib.json", kernel)
        self.ping("ground-recovered", 20)
        self.checks.update({"missing_ground_heartbeat_triggers_autonomy": True,
                            "onboard_installs_real_kernel_routes": True, "gtpu_survives_ground_route_withdrawal": True,
                            "ground_reconnect_withdraws_fallback": True})

    def capture_evidence(self) -> None:
        assert self.continuous is not None
        self.continuous.wait(timeout=75)
        self.continuous_log.close()
        self.continuous_log = None
        raw_ping = (self.art / "continuous-ping.log").read_text()
        match = re.search(r"(\d+) packets transmitted, (\d+) received", raw_ping)
        assert match, raw_ping[-1500:]
        tx, rx = map(int, match.groups())
        loss = (tx - rx) * 100 / tx
        assert tx >= 300 and loss < 5, {"tx": tx, "rx": rx, "loss": loss}
        self.traffic.append({"phase": "continuous_across_all_faults", "transmitted": tx, "received": rx, "loss_percent": loss})
        self.checks["continuous_gtpu_business_across_faults"] = True
        for node, name in self.captures.items():
            command("docker", "kill", "--signal=INT", name)
            command("docker", "wait", name)
            raw = command("docker", "run", "--rm", "--network=none", "-v", f"{self.art}:/evidence:ro", "--entrypoint", "tcpdump", TOOLS,
                          "-n", "-r", f"/evidence/{node}-gtpu.pcap").stdout
            (self.art / f"{node}-gtpu.txt").write_text(raw)
            assert f"{GNB}.2152 > {UPF}.2152" in raw and f"{UPF}.2152 > {GNB}.2152" in raw, raw[-1000:]
            self.checks[node + "_bidirectional_gtpu_capture"] = True
        with urllib.request.urlopen(self.endpoint + "/metrics", timeout=5) as response:
            metrics = response.read().decode()
        assert "starfabric_" in metrics
        (self.art / "controller-metrics.prom").write_text(metrics)
        self.checks["shared_scenario_and_fault_timeline"] = True

    def cleanup(self) -> list[str]:
        errors = []
        self.stop_heartbeat.set()
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=20)
        self.stop_controller()
        for node in ("r1", "r2", "r3", "r4"):
            if self.router(node) in self.containers:
                for name, args in (("running-config", ["vtysh", "-c", "show running-config"]),
                                   ("isis", ["vtysh", "-c", "show isis neighbor"]),
                                   ("rib", ["vtysh", "-c", "show ip route"]),
                                   ("kernel", ["ip", "-j", "route", "show", "table", "all"]),
                                   ("addresses", ["ip", "-j", "address", "show"])):
                    result = command("docker", "exec", self.router(node), *args, check=False)
                    (self.art / f"{node}-{name}.txt").write_text(result.stdout + result.stderr)
        if self.continuous and self.continuous.poll() is None:
            self.continuous.terminate()
            self.continuous.wait(timeout=5)
        if self.continuous_log:
            self.continuous_log.close()
        for name, destination, gateway in self.routes_added:
            command("docker", "exec", name, "ip", "route", "del", destination + "/32", "via", gateway, check=False)
        for name in reversed(self.containers):
            log = command("docker", "logs", name, check=False)
            (self.art / (name + ".log")).write_text(log.stdout + log.stderr)
            result = command("docker", "rm", "-f", name, check=False)
            if result.returncode:
                errors.append(result.stderr)
        for network, name in reversed(self.external_links):
            result = command("docker", "network", "disconnect", network, name, check=False)
            if result.returncode:
                errors.append(result.stderr)
        for network in reversed(self.networks):
            result = command("docker", "network", "rm", network, check=False)
            if result.returncode:
                errors.append(result.stderr)
        return errors


def main() -> None:
    runtime = Runtime()
    report: dict[str, Any] = {"schema_version": 1, "success": False, "run_id": runtime.identifier,
        "suite_run_id": os.environ.get("SF_RUN_ID"), "scope": "connected TLE/SGP4/contact prediction + FRR + Open5GS/UERANSIM GTP-U + real Rust/kernel autonomy",
        "boundary": "Two software satellites and two gateways; orbital time is compressed and management uses a local heartbeat relay. This does not prove external Release 17 NTN UE, RIC or full-scale/24-hour budgets."}
    try:
        runtime.compile_orbit()
        runtime.network()
        runtime.event("start_connected_noc")
        runtime.noc.start()
        runtime.start_controller()
        runtime.reconcile("initial-commit")
        runtime.route_n3()
        runtime.ping("initial")
        runtime.start_onboard()
        runtime.orbit_handover()
        runtime.autonomy()
        runtime.capture_evidence()
        runtime.noc.verify()
        report["success"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        errors = []
        try:
            errors.extend(runtime.cleanup())
        except Exception as error:
            errors.append(str(error))
        try:
            errors.extend(runtime.noc.cleanup())
        except Exception as error:
            errors.append(str(error))
        runtime.checks["owned_resources_cleaned"] = not errors
        if errors:
            report["cleanup_errors"] = errors
            report["success"] = False
        report.update({"generated_at": now(), "checks": runtime.checks, "timeline": runtime.timeline,
                       "traffic": runtime.traffic, "artifacts": str(runtime.art.relative_to(ROOT)),
                       "scenario_sha256": hashlib.sha256((runtime.art / "scenario.json").read_bytes()).hexdigest()
                           if (runtime.art / "scenario.json").exists() else None})
        save(REPORT, report)
    print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)
    raise SystemExit(0 if report["success"] else 1)


if __name__ == "__main__":
    main()
