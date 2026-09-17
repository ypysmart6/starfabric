#!/usr/bin/env python3
"""Exercise the controller's security and backup/restore boundary end to end."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports/security-operations.json"
TOKEN = "starfabric-security-lab-token"
REQUEST_ID = "security-restore-001"


def free_port() -> int:
    with socket.socket() as value:
        value.bind(("127.0.0.1", 0))
        return int(value.getsockname()[1])


def openssl(*arguments: str) -> None:
    result = subprocess.run(
        ["openssl", *arguments],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"openssl {' '.join(arguments)} failed: {result.stderr.strip()}")


def issue_certificates(directory: Path) -> dict[str, Path]:
    paths = {name: directory / name for name in (
        "ca.crt", "ca.key", "server.crt", "server.key", "server.csr",
        "client.crt", "client.key", "client.csr", "rogue.crt", "rogue.key",
    )}
    openssl(
        "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "1",
        "-subj", "/CN=StarFabric Single-PC CA",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        "-keyout", str(paths["ca.key"]), "-out", str(paths["ca.crt"]),
    )
    openssl(
        "req", "-newkey", "rsa:2048", "-nodes", "-sha256",
        "-subj", "/CN=localhost",
        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        "-addext", "keyUsage=critical,digitalSignature,keyEncipherment",
        "-addext", "extendedKeyUsage=serverAuth",
        "-keyout", str(paths["server.key"]), "-out", str(paths["server.csr"]),
    )
    openssl(
        "x509", "-req", "-sha256", "-days", "1", "-copy_extensions", "copy",
        "-in", str(paths["server.csr"]), "-CA", str(paths["ca.crt"]),
        "-CAkey", str(paths["ca.key"]), "-CAcreateserial", "-out", str(paths["server.crt"]),
    )
    openssl(
        "req", "-newkey", "rsa:2048", "-nodes", "-sha256",
        "-subj", "/CN=starfabric-operator",
        "-addext", "keyUsage=critical,digitalSignature",
        "-addext", "extendedKeyUsage=clientAuth",
        "-keyout", str(paths["client.key"]), "-out", str(paths["client.csr"]),
    )
    openssl(
        "x509", "-req", "-sha256", "-days", "1", "-copy_extensions", "copy",
        "-in", str(paths["client.csr"]), "-CA", str(paths["ca.crt"]),
        "-CAkey", str(paths["ca.key"]), "-CAcreateserial", "-out", str(paths["client.crt"]),
    )
    openssl(
        "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "1",
        "-subj", "/CN=untrusted-client", "-addext", "extendedKeyUsage=clientAuth",
        "-addext", "keyUsage=critical,digitalSignature",
        "-keyout", str(paths["rogue.key"]), "-out", str(paths["rogue.crt"]),
    )
    for name in ("ca.key", "server.key", "client.key", "rogue.key"):
        os.chmod(paths[name], 0o600)
    return paths


def tls_context(paths: dict[str, Path], client: str | None) -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=paths["ca.crt"])
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    if client == "trusted":
        context.load_cert_chain(paths["client.crt"], paths["client.key"])
    elif client == "rogue":
        context.load_cert_chain(paths["rogue.crt"], paths["rogue.key"])
    return context


def request(
    url: str,
    *,
    context: ssl.SSLContext | None = None,
    method: str = "GET",
    body: object | None = None,
    token: str | None = None,
    request_id: str | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    headers: dict[str, str] = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if request_id is not None:
        headers["X-Request-ID"] = request_id
    value = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(value, context=context, timeout=2) as response:
            return response.status, response.read(), dict(response.headers.items())
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers.items())


def expect_tls_rejection(url: str, context: ssl.SSLContext) -> bool:
    try:
        request(url, context=context)
    except (ssl.SSLError, urllib.error.URLError, ConnectionError, TimeoutError):
        return True
    return False


def wait_health(port: int, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    last = 0
    while time.monotonic() < deadline:
        try:
            last, _, _ = request(f"http://127.0.0.1:{port}/healthz")
            if last == 200:
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(0.05)
    raise AssertionError(f"health listener did not become ready; last status={last}")


def start_controller(
    binary: Path,
    directory: Path,
    state: Path,
    paths: dict[str, Path],
    token_file: Path,
    instance: str,
) -> tuple[subprocess.Popen[bytes], object, int, int, Path]:
    api_port, health_port = free_port(), free_port()
    log_path = directory / f"{instance}.jsonl"
    output_path = directory / f"{instance}.stdout.log"
    output = output_path.open("wb")
    command = [
        str(binary),
        "--scenario", str(ROOT / "scenarios/leo-resilient.json"),
        "--listen", f"127.0.0.1:{api_port}",
        "--health-listen", f"127.0.0.1:{health_port}",
        "--state-dir", str(state),
        "--log-file", str(log_path),
        "--reconcile-interval", "0",
        "--token-file", str(token_file),
        "--tls-cert", str(paths["server.crt"]),
        "--tls-key", str(paths["server.key"]),
        "--tls-client-ca", str(paths["ca.crt"]),
    ]
    process = subprocess.Popen(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
    wait_health(health_port)
    return process, output, api_port, health_port, log_path


def build_controller(directory: Path, version: str) -> tuple[Path, str]:
    binary = directory / f"sf-controller-{version}"
    environment = os.environ.copy()
    environment["GOCACHE"] = str(ROOT / ".cache/go-build")
    result = subprocess.run(
        [
            "go", "build", "-buildvcs=false", "-trimpath",
            "-ldflags", f"-X main.version={version}",
            "-o", str(binary), "./cmd/sf-controller",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"build controller {version}: {result.stdout}\n{result.stderr}")
    advertised = subprocess.run(
        [str(binary), "--version"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    if advertised != version:
        raise AssertionError(f"controller advertised {advertised!r}, want {version!r}")
    return binary, hashlib.sha256(binary.read_bytes()).hexdigest()


def stop_controller(process: subprocess.Popen[bytes] | None, output: object | None) -> None:
    if process is not None and process.poll() is None:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    if output is not None:
        output.close()


def tree_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(directory).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> None:
    process = None
    output = None
    with tempfile.TemporaryDirectory(prefix="starfabric-security-", dir="/tmp") as temporary:
        directory = Path(temporary)
        controller_v1, controller_v1_digest = build_controller(directory, "security-v1")
        controller_v2, controller_v2_digest = build_controller(directory, "security-v2")
        paths = issue_certificates(directory)
        token_file = directory / "api-token"
        token_file.write_text(TOKEN + "\n", encoding="utf-8")
        os.chmod(token_file, 0o600)
        state = directory / "state"
        state.mkdir(mode=0o750)
        try:
            process, output, api_port, health_port, log_path = start_controller(
                controller_v1, directory, state, paths, token_file, "primary-v1"
            )
            base = f"https://localhost:{api_port}"
            trusted = tls_context(paths, "trusted")
            no_client = tls_context(paths, None)
            rogue = tls_context(paths, "rogue")

            with socket.create_connection(("127.0.0.1", api_port), timeout=2) as raw:
                with trusted.wrap_socket(raw, server_hostname="localhost") as secure:
                    tls_version = secure.version()

            no_certificate_rejected = expect_tls_rejection(f"{base}/api/v1/status", no_client)
            rogue_certificate_rejected = expect_tls_rejection(f"{base}/api/v1/status", rogue)
            no_token_status, _, _ = request(f"{base}/api/v1/status", context=trusted)
            wrong_token_status, _, _ = request(
                f"{base}/api/v1/status", context=trusted, token="incorrect"
            )
            authorized_status, _, authorized_headers = request(
                f"{base}/api/v1/status", context=trusted, token=TOKEN, request_id=REQUEST_ID
            )
            health_api_status, _, _ = request(f"http://127.0.0.1:{health_port}/api/v1/status")
            health_metrics_status, health_metrics, _ = request(
                f"http://127.0.0.1:{health_port}/metrics"
            )

            event = {
                "event_id": "security-persist-001",
                "subject": "s1-s2",
                "sequence": 1,
                "type": "link_down",
                "link": {"id": "s1-s2"},
            }
            event_status, event_body, _ = request(
                f"{base}/api/v1/topology/events?reconcile=true",
                context=trusted,
                method="POST",
                body=event,
                token=TOKEN,
                request_id=REQUEST_ID,
            )
            event_response = json.loads(event_body)
            persisted_version = event_response["topology"]["version"]
            assert event_status == 202 and persisted_version > 1, event_body
            assert event_response["status"]["phase"] == "committed", event_body
        finally:
            stop_controller(process, output)
            process, output = None, None

        state_files_private = all(
            (path.stat().st_mode & 0o777) == 0o600 for path in state.rglob("*.json")
        )
        before_backup_digest = tree_digest(state)
        backup = directory / "backup"
        shutil.copytree(state, backup)
        shutil.rmtree(state)
        shutil.copytree(backup, state)
        restored_digest = tree_digest(state)

        try:
            process, output, restored_api, _, upgraded_log = start_controller(
                controller_v2, directory, state, paths, token_file, "upgraded-v2"
            )
            restored_base = f"https://localhost:{restored_api}"
            trusted = tls_context(paths, "trusted")
            topology_status, topology_body, _ = request(
                f"{restored_base}/api/v1/topology", context=trusted, token=TOKEN
            )
            topology = json.loads(topology_body)
            duplicate_status, duplicate_body, _ = request(
                f"{restored_base}/api/v1/topology/events",
                context=trusted,
                method="POST",
                body=event,
                token=TOKEN,
            )
            restored_link = next(link for link in topology["links"] if link["id"] == "s1-s2")
            upgrade_event = {
                "event_id": "security-upgrade-v2-001",
                "subject": "s1-s4",
                "sequence": 1,
                "type": "link_down",
                "link": {"id": "s1-s4"},
            }
            upgrade_status, upgrade_body, _ = request(
                f"{restored_base}/api/v1/topology/events?reconcile=true",
                context=trusted,
                method="POST",
                body=upgrade_event,
                token=TOKEN,
            )
            upgrade_response = json.loads(upgrade_body)
            upgraded_version = upgrade_response["topology"]["version"]
            assert upgrade_status == 202 and upgraded_version == persisted_version + 1
            assert upgrade_response["status"]["phase"] == "committed"
        finally:
            stop_controller(process, output)
            process, output = None, None

        try:
            process, output, downgraded_api, _, downgraded_log = start_controller(
                controller_v1, directory, state, paths, token_file, "downgraded-v1"
            )
            downgraded_base = f"https://localhost:{downgraded_api}"
            trusted = tls_context(paths, "trusted")
            downgraded_status, downgraded_body, _ = request(
                f"{downgraded_base}/api/v1/topology", context=trusted, token=TOKEN
            )
            downgraded_topology = json.loads(downgraded_body)
            downgraded_control_status, downgraded_control_body, _ = request(
                f"{downgraded_base}/api/v1/status", context=trusted, token=TOKEN
            )
            downgraded_control = json.loads(downgraded_control_body)
            upgraded_link_after_downgrade = next(
                link for link in downgraded_topology["links"] if link["id"] == "s1-s4"
            )
            upgraded_duplicate_status, upgraded_duplicate_body, _ = request(
                f"{downgraded_base}/api/v1/topology/events",
                context=trusted,
                method="POST",
                body=upgrade_event,
                token=TOKEN,
            )
        finally:
            stop_controller(process, output)

        log_entries = []
        for candidate in (log_path, upgraded_log, downgraded_log):
            if candidate.exists():
                for line in candidate.read_text(encoding="utf-8").splitlines():
                    try:
                        log_entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        audit_entries = [
            entry for entry in log_entries
            if entry.get("message") == "http request" and entry.get("request_id") == REQUEST_ID
        ]
        log_text = "\n".join(json.dumps(item, sort_keys=True) for item in log_entries)
        checks = {
            "tls_1_3_negotiated": tls_version == "TLSv1.3",
            "client_certificate_required": no_certificate_rejected,
            "untrusted_client_rejected": rogue_certificate_rejected,
            "missing_bearer_rejected": no_token_status == 401,
            "wrong_bearer_rejected": wrong_token_status == 401,
            "mtls_and_bearer_authorized": authorized_status == 200,
            "request_id_echoed": authorized_headers.get("X-Request-Id") == REQUEST_ID,
            "health_listener_api_isolated": health_api_status == 404,
            "health_metrics_available": health_metrics_status == 200 and b"starfabric_" in health_metrics,
            "audit_log_correlated": any(
                entry.get("method") in {"GET", "POST"}
                and isinstance(entry.get("status"), int)
                and isinstance(entry.get("duration_us"), int)
                for entry in audit_entries
            ),
            "credentials_not_logged": TOKEN not in log_text,
            "secret_files_private": all(
                (paths[name].stat().st_mode & 0o777) == 0o600
                for name in ("ca.key", "server.key", "client.key", "rogue.key")
            ) and (token_file.stat().st_mode & 0o777) == 0o600,
            "state_files_private": state_files_private,
            "backup_bitwise_verified": before_backup_digest == restored_digest,
            "restored_topology_version": topology_status == 200
            and topology["version"] == persisted_version,
            "restored_link_state": restored_link["operational_up"] is False,
            "restored_event_idempotency": duplicate_status == 409
            and b"duplicate topology event" in duplicate_body,
            "upgrade_distinct_artifact_started": controller_v1_digest != controller_v2_digest
            and any(
                entry.get("message") == "controller listening"
                and entry.get("version") == "security-v2"
                for entry in log_entries
            ),
            "upgrade_preserved_v1_state": topology_status == 200
            and topology["version"] == persisted_version
            and restored_link["operational_up"] is False,
            "upgrade_v2_mutation_committed": upgrade_status == 202
            and upgraded_version == persisted_version + 1,
            "downgrade_distinct_artifact_started": any(
                entry.get("message") == "controller listening"
                and entry.get("version") == "security-v1"
                and entry.get("address", "").endswith(str(downgraded_api))
                for entry in log_entries
            ),
            "downgrade_reads_v2_state": downgraded_status == 200
            and downgraded_topology["version"] == upgraded_version
            and upgraded_link_after_downgrade["operational_up"] is False
            and downgraded_control_status == 200
            and downgraded_control["reconcile"]["phase"] == "committed",
            "downgrade_preserves_v2_idempotency": upgraded_duplicate_status == 409
            and b"duplicate topology event" in upgraded_duplicate_body,
        }
        report = {
            "schema_version": 1,
            "success": all(checks.values()),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "evidence_level": "live TLS controller processes + authenticated mutation + offline backup/restore + distinct-artifact upgrade/downgrade",
            "checks": checks,
            "tls_version": tls_version,
            "restored_topology_version_value": topology.get("version"),
            "backup_sha256": restored_digest,
            "controller_v1_sha256": controller_v1_digest,
            "controller_v2_sha256": controller_v2_digest,
            "audit_request_id": REQUEST_ID,
            "external_boundaries": [
                "enterprise certificate issuance and rotation",
                "external identity provider",
                "off-host encrypted backup storage",
            ],
        }
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        temporary_report = REPORT.with_suffix(".json.tmp")
        temporary_report.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary_report, REPORT)
        if not report["success"]:
            failed = [name for name, passed in checks.items() if not passed]
            raise AssertionError(f"security operations checks failed: {failed}")
        print(
            f"PASS: TLS {tls_version}, mTLS/bearer isolation, backup/restore, "
            f"and v1 -> v2 -> v1 state-compatible rollout through version {upgraded_version}"
        )


if __name__ == "__main__":
    main()
