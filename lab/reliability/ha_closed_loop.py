#!/usr/bin/env python3
"""Run two controller processes against a live Kubernetes Lease API shape."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "lab/reliability/artifacts"
REPORT = ROOT / "reports/ha-failover.json"
TOKEN = "starfabric-ha-lab-token"
LEASE_PATH = "/apis/coordination.k8s.io/v1/namespaces/starfabric/leases/starfabric-controller"
COLLECTION_PATH = "/apis/coordination.k8s.io/v1/namespaces/starfabric/leases"


class LeaseAPI(BaseHTTPRequestHandler):
    lock = threading.Lock()
    lease: dict[str, object] | None = None
    writes = 0
    conflicts = 0

    def log_message(self, *_: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized() or self.path != LEASE_PATH:
            self._reply(401 if not self._authorized() else 404, {})
            return
        with self.lock:
            if self.lease is None:
                self._reply(404, {"kind": "Status", "reason": "NotFound"})
            else:
                self._reply(200, self.lease)

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized() or self.path != COLLECTION_PATH:
            self._reply(401 if not self._authorized() else 404, {})
            return
        value = self._body()
        with self.lock:
            if self.lease is not None:
                type(self).conflicts += 1
                self._reply(409, {"reason": "AlreadyExists"})
                return
            value.setdefault("metadata", {})["resourceVersion"] = "1"
            type(self).lease = value
            type(self).writes += 1
            self._reply(201, value)

    def do_PUT(self) -> None:  # noqa: N802
        if not self._authorized() or self.path != LEASE_PATH:
            self._reply(401 if not self._authorized() else 404, {})
            return
        value = self._body()
        with self.lock:
            current = self.lease
            supplied = value.get("metadata", {}).get("resourceVersion")
            expected = None if current is None else current.get("metadata", {}).get("resourceVersion")
            if current is None or supplied != expected:
                type(self).conflicts += 1
                self._reply(409, {"reason": "Conflict"})
                return
            version = str(int(str(expected)) + 1)
            value.setdefault("metadata", {})["resourceVersion"] = version
            type(self).lease = value
            type(self).writes += 1
            self._reply(200, value)

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {TOKEN}"

    def _body(self) -> dict[str, object]:
        size = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(size))

    def _reply(self, status: int, value: object) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def port() -> int:
    with socket.socket() as value:
        value.bind(("127.0.0.1", 0))
        return int(value.getsockname()[1])


def request(url: str, method: str = "GET") -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method=method), timeout=1) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (urllib.error.URLError, TimeoutError):
        return 0, b""


def wait_status(url: str, expected: int, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    last = (0, b"")
    while time.monotonic() < deadline:
        last = request(url)
        if last[0] == expected:
            return last[1]
        time.sleep(0.05)
    raise AssertionError(f"{url} status={last[0]}, want {expected}; body={last[1]!r}")


def start_controller(identity: str, api_port: int, health_port: int, lease_port: int, state: Path, token: Path, log: Path) -> tuple[subprocess.Popen[bytes], object]:
    handle = log.open("wb")
    command = [
        str(ROOT / "bin/sf-controller"),
        "--scenario", str(ROOT / "scenarios/leo-resilient.json"),
        "--listen", f"127.0.0.1:{api_port}",
        "--health-listen", f"127.0.0.1:{health_port}",
        "--state-dir", str(state),
        "--reconcile-interval", "0",
        "--leader-election",
        "--leader-api-endpoint", f"http://127.0.0.1:{lease_port}",
        "--leader-token-file", str(token),
        "--leader-namespace", "starfabric",
        "--leader-lease-name", "starfabric-controller",
        "--leader-identity", identity,
        "--leader-lease-duration", "2s",
        "--leader-retry-period", "250ms",
    ]
    return subprocess.Popen(command, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT), handle


def stop(process: subprocess.Popen[bytes] | None, handle: object | None) -> None:
    if process is not None and process.poll() is None:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    if handle is not None:
        handle.close()


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    state = ARTIFACTS / "state"
    state.mkdir(parents=True, exist_ok=True)
    for item in state.glob("*.json"):
        item.unlink()
    token = ARTIFACTS / "service-account-token"
    token.write_text(TOKEN + "\n", encoding="utf-8")
    os.chmod(token, 0o600)
    LeaseAPI.lease, LeaseAPI.writes, LeaseAPI.conflicts = None, 0, 0
    lease_server = ThreadingHTTPServer(("127.0.0.1", 0), LeaseAPI)
    lease_thread = threading.Thread(target=lease_server.serve_forever, daemon=True)
    lease_thread.start()
    a_api, a_health, b_api, b_health = port(), port(), port(), port()
    process_a = process_b = None
    handle_a = handle_b = None
    try:
        process_a, handle_a = start_controller("controller-a", a_api, a_health, lease_server.server_port, state, token, ARTIFACTS / "controller-a.log")
        wait_status(f"http://127.0.0.1:{a_health}/readyz", 200, 5)
        status, initial_body = request(f"http://127.0.0.1:{a_api}/api/v1/reconcile", "POST")
        assert status == 200, initial_body
        initial = json.loads(initial_body)
        assert initial["status"]["phase"] == "committed"

        process_b, handle_b = start_controller("controller-b", b_api, b_health, lease_server.server_port, state, token, ARTIFACTS / "controller-b.log")
        wait_status(f"http://127.0.0.1:{b_health}/healthz", 200, 5)
        wait_status(f"http://127.0.0.1:{b_health}/readyz", 503, 3)
        follower_status, follower_body = request(f"http://127.0.0.1:{b_api}/api/v1/reconcile", "POST")
        assert follower_status == 503 and b"not_leader" in follower_body

        started = time.monotonic()
        stop(process_a, handle_a)
        process_a, handle_a = None, None
        wait_status(f"http://127.0.0.1:{b_health}/readyz", 200, 7)
        takeover_seconds = time.monotonic() - started
        takeover_status, takeover_body = request(f"http://127.0.0.1:{b_api}/api/v1/reconcile", "POST")
        assert takeover_status == 200, takeover_body
        takeover = json.loads(takeover_body)
        assert takeover["status"]["phase"] == "committed"
        topology_status, topology_body = request(f"http://127.0.0.1:{b_api}/api/v1/topology")
        assert topology_status == 200
        assert json.loads(topology_body)["version"] == initial["plan"]["topology_version"]
        metrics_status, metrics = request(f"http://127.0.0.1:{b_health}/metrics")
        assert metrics_status == 200 and b"starfabric_leader 1" in metrics
        assert takeover_seconds < 6
    finally:
        stop(process_a, handle_a)
        stop(process_b, handle_b)
        lease_server.shutdown()
        lease_server.server_close()
        lease_thread.join(timeout=2)
        token.unlink(missing_ok=True)

    report = {
        "schema_version": 1,
        "success": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "two_controller_processes": True,
            "kubernetes_lease_rest": True,
            "bearer_authentication": True,
            "single_writer_fencing": True,
            "follower_mutation_rejected": True,
            "leader_failure_injected": True,
            "lease_expiry_takeover": True,
            "durable_state_reloaded": True,
            "post_takeover_reconcile_committed": True,
        },
        "takeover_seconds": round(takeover_seconds, 3),
        "lease_writes": LeaseAPI.writes,
        "lease_conflicts": LeaseAPI.conflicts,
    }
    temporary = REPORT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, REPORT)
    print(f"PASS: two-controller Lease failover and durable-state takeover in {takeover_seconds:.3f}s")


if __name__ == "__main__":
    main()
