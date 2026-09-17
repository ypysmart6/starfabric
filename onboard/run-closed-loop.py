#!/usr/bin/env python3
"""Exercise the real Rust onboard process, durable autonomy, and signed A/B update."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "onboard/target/release/satellite-node-runtime"
REPORT = ROOT / "reports/onboard-runtime.json"
RFC8032_PRIVATE_SEED = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc4"
    "4449c5697b326919703bac031cae7f60"
)
PKCS8_ED25519_PREFIX = bytes.fromhex("302e020100300506032b657004220420")


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def exchange(base: str, method: str, path: str, payload: Any = None) -> tuple[int, Any]:
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        base + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            decoded = raw.decode(errors="replace")
        return error.code, decoded


def wait_for(predicate: Any, timeout: float, description: str) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    raise AssertionError(f"timeout waiting for {description}; last={last!r}")


def start(config: Path, state: Path, port: int, heartbeat: bool = False) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        [
            str(BINARY),
            "--config", str(config),
            "--state-dir", str(state),
            "--listen", f"127.0.0.1:{port}",
            "--disconnect-hold-seconds", "1",
            "--dry-run-routes",
            *(["--heartbeat-timeout-seconds", "1"] if heartbeat else []),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    wait_for(lambda: exchange(base, "GET", "/readyz")[0] == 200, 5, "runtime readiness")
    return process


def stop(process: subprocess.Popen[str]) -> str:
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    assert process.returncode == 0, f"runtime exited {process.returncode}"
    return process.stdout.read() if process.stdout else ""


def sign_manifest(temp: Path, artifact: Path) -> Path:
    version = "2.0.0-test"
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    signed = b"\0".join([version.encode(), b"b", str(artifact).encode(), digest.encode()])
    key = temp / "update-key.der"
    message = temp / "update-message.bin"
    signature = temp / "update-signature.bin"
    key.write_bytes(PKCS8_ED25519_PREFIX + RFC8032_PRIVATE_SEED)
    message.write_bytes(signed)
    subprocess.run(
        [
            "openssl", "pkeyutl", "-sign", "-rawin", "-keyform", "DER",
            "-inkey", str(key), "-in", str(message), "-out", str(signature),
        ],
        check=True,
        capture_output=True,
    )
    manifest = temp / "update-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": version,
                "target_slot": "b",
                "artifact": str(artifact),
                "sha256": digest,
                "signature": signature.read_bytes().hex(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    assert BINARY.is_file() and os.access(BINARY, os.X_OK), f"missing release binary: {BINARY}"
    checks: dict[str, Any] = {}
    logs = ""
    with tempfile.TemporaryDirectory(prefix="starfabric-onboard-") as directory:
        temp = Path(directory)
        state = temp / "state"
        config = temp / "config.json"
        config.write_text((ROOT / "onboard/config.example.json").read_text(encoding="utf-8"), encoding="utf-8")
        artifact = temp / "candidate-runtime"
        artifact.write_bytes(b"signed deterministic satellite runtime v2\n")
        manifest = sign_manifest(temp, artifact)
        port = free_port()
        base = f"http://127.0.0.1:{port}"

        process = start(config, state, port)
        try:
            status, initial = exchange(base, "GET", "/v1/status")
            assert status == 200 and initial["active_slot"] == "a"
            fallback = wait_for(
                lambda: (lambda response: response[1] if response[0] == 200 and response[1]["fallback_active"] else None)(
                    exchange(base, "GET", "/v1/status")
                ),
                5,
                "disconnected fallback",
            )
            checks["disconnect_fallback_activated"] = fallback["fallback_active"]

            status, _ = exchange(base, "POST", "/v1/connectivity", {"connected": True, "generation": 2})
            assert status == 200
            status, connected = exchange(base, "GET", "/v1/status")
            assert status == 200 and connected["connected"] and not connected["fallback_active"]
            checks["reconnect_withdraws_fallback"] = True
            status, _ = exchange(base, "POST", "/v1/connectivity", {"connected": False, "generation": 2})
            assert status == 409
            checks["stale_generation_rejected"] = True

            status, _ = exchange(base, "POST", "/v1/faults", {"mode": "process"})
            assert status == 200
            assert exchange(base, "GET", "/healthz")[0] == 503
            assert exchange(base, "GET", "/readyz")[0] == 503
            status, _ = exchange(base, "POST", "/v1/faults", {"mode": None})
            assert status == 200 and exchange(base, "GET", "/healthz")[0] == 200
            checks["fault_health_propagation"] = True

            status, staged = exchange(base, "POST", "/v1/update/stage", {"manifest_path": str(manifest)})
            assert status == 200 and staged["status"] == "staged"
            status, confirmed = exchange(base, "POST", "/v1/update/confirm", {"slot": "b"})
            assert status == 200 and confirmed["status"] == "confirmed"
            assert exchange(base, "GET", "/v1/status")[1]["active_slot"] == "b"
            checks["ed25519_sha256_stage_confirm"] = True
        finally:
            logs += stop(process)

        process = start(config, state, port)
        try:
            status, recovered = exchange(base, "GET", "/v1/status")
            assert status == 200
            assert recovered["active_slot"] == "b" and recovered["generation"] == 2 and recovered["connected"]
            checks["restart_state_recovered"] = True
            status, rolled_back = exchange(base, "POST", "/v1/update/rollback", {})
            assert status == 200 and rolled_back["status"] == "rolled_back"
            assert exchange(base, "GET", "/v1/status")[1]["active_slot"] == "a"
            checks["ab_rollback"] = True
        finally:
            logs += stop(process)

        # A lost ground process cannot send an explicit disconnect event.
        # One heartbeat must expire locally, including after persisted recovery.
        process = start(config, state, port, heartbeat=True)
        try:
            status, _ = exchange(base, "POST", "/v1/connectivity", {"connected": True, "generation": 3})
            assert status == 200
            expired = wait_for(
                lambda: (lambda result: result if result["fallback_active"] and not result["connected"] else None)(
                    exchange(base, "GET", "/v1/status")[1]), 5, "ground heartbeat expiry")
            assert expired["generation"] == 3
            checks["missing_heartbeat_expires_connected_state"] = True
            status, _ = exchange(base, "POST", "/v1/connectivity", {"connected": True, "generation": 4})
            assert status == 200 and not exchange(base, "GET", "/v1/status")[1]["fallback_active"]
            checks["heartbeat_recovery_withdraws_fallback"] = True
        finally:
            logs += stop(process)

    report = {
        "schema_version": 1,
        "success": all(checks.values()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "binary": {
            "path": str(BINARY.relative_to(ROOT)),
            "sha256": hashlib.sha256(BINARY.read_bytes()).hexdigest(),
            "bytes": BINARY.stat().st_size,
        },
        "runtime_log_events": sum(1 for line in logs.splitlines() if line.strip()),
        "boundary": "Dry-run route mode proves autonomy state transitions without mutating the host FIB; flight BSP, secure boot root, and radiation-qualified hardware are HIL gates.",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, REPORT)
    print(f"PASS: onboard process closed loop ({len(checks)} checks), A/B rollback complete")


if __name__ == "__main__":
    main()
