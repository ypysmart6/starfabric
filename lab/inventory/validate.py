#!/usr/bin/env python3
"""Exercise sf-inventory against a live NetBox/Nautobot-compatible REST API."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "lab/inventory/artifacts"
REPORT = ROOT / "reports/inventory-closed-loop.json"
TOKEN = "starfabric-inventory-lab-token"


class InventoryAPI(BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []

    def log_message(self, *_: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        self.requests.append(
            {
                "path": parsed.path,
                "query": query,
                "authorization": self.headers.get("Authorization"),
                "accept": self.headers.get("Accept"),
            }
        )
        if self.headers.get("Authorization") != f"Token {TOKEN}":
            self._reply(401, {"detail": "missing token"})
            return
        if query.get("tag") == ["cross-origin"]:
            self._reply(200, {"next": "https://attacker.invalid/api/dcim/devices/", "results": []})
            return
        if parsed.path == "/api/dcim/devices/":
            page = query.get("page", ["1"])[0]
            if page == "1":
                next_page = f"http://127.0.0.1:{self.server.server_port}/api/dcim/devices/?limit=200&tag=starfabric&page=2"
                results = [
                    {
                        "name": "sat-01",
                        "role": {"slug": "satellite"},
                        "platform": {"slug": "embedded-linux"},
                        "site": {"slug": "orbit-plane-a"},
                        "primary_ip6": {"address": "2001:db8::1/128"},
                        "custom_fields": {
                            "gnmi_target": "sat-01:9339",
                            "gribi_target": "sat-01:9340",
                            "network_instance": "DEFAULT",
                            "orbital_plane": "A",
                        },
                    }
                ]
            else:
                next_page = None
                results = [
                    {
                        "name": "gw-01",
                        "device_role": {"slug": "gateway"},
                        "platform": {"slug": "linux"},
                        "site": {"slug": "ground-ca"},
                        "primary_ip4": {"address": "192.0.2.10/32"},
                        "custom_fields": {"tls_server_name": "gw-01.test"},
                    }
                ]
            self._reply(200, {"next": next_page, "results": results})
            return
        if parsed.path == "/api/dcim/cables/":
            self._reply(
                200,
                {
                    "next": None,
                    "results": [
                        {
                            "id": 42,
                            "status": {"slug": "connected"},
                            "a_terminations": [{"object": {"device": {"name": "sat-01"}}}],
                            "b_terminations": [{"object": {"device": {"name": "gw-01"}}}],
                            "custom_fields": {
                                "link_type": "rf",
                                "latency_us": 18000,
                                "capacity_bps": 250000000,
                                "risk_groups": ["gateway-ca", "weather-zone-7"],
                            },
                        }
                    ],
                },
            )
            return
        self._reply(404, {"detail": "not found"})

    def _reply(self, status: int, value: object) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_import(base: str, token_file: Path, output: Path, tag: str = "starfabric") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(ROOT / "bin/sf-inventory"),
            "--url", base,
            "--token-file", str(token_file),
            "--tag", tag,
            "--output", str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    token_file = ARTIFACTS / "token"
    output = ARTIFACTS / "inventory-scenario.json"
    token_file.write_text(TOKEN + "\n", encoding="utf-8")
    os.chmod(token_file, 0o600)
    InventoryAPI.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), InventoryAPI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/"
    try:
        result = run_import(base, token_file, output)
        assert result.returncode == 0, result.stderr
        document = json.loads(output.read_text(encoding="utf-8"))
        topology = document["topology"]
        nodes = {node["id"]: node for node in topology["nodes"]}
        links = {link["id"]: link for link in topology["links"]}
        assert document["scenario_id"] == "netbox-import"
        assert len(nodes) == 2 and len(links) == 2
        assert nodes["sat-01"]["loopback"] == "2001:db8::1"
        assert nodes["sat-01"]["labels"]["gnmi_target"] == "sat-01:9339"
        assert nodes["gw-01"]["labels"]["tls_server_name"] == "gw-01.test"
        forward = links["netbox-cable-42-a-b"]
        reverse = links["netbox-cable-42-b-a"]
        assert forward["source"] == "sat-01" and reverse["source"] == "gw-01"
        assert forward["latency_us"] == 18000 and forward["capacity_bps"] == 250000000
        assert forward["risk_groups"] == ["gateway-ca", "weather-zone-7"]
        assert output.stat().st_mode & 0o777 == 0o640

        rejected_output = ARTIFACTS / "must-not-exist.json"
        rejected_output.unlink(missing_ok=True)
        failed = run_import(base, token_file, rejected_output, "cross-origin")
        assert failed.returncode != 0 and "refused cross-origin" in failed.stderr
        assert not rejected_output.exists()
        requests = InventoryAPI.requests
        assert len(requests) == 4, requests
        assert all(item["authorization"] == f"Token {TOKEN}" for item in requests)
        assert all(item["accept"] == "application/json" for item in requests)
        assert all(item["query"].get("limit") == ["200"] for item in requests)
        assert all(item["query"].get("tag") for item in requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        token_file.unlink(missing_ok=True)

    report = {
        "schema_version": 1,
        "success": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "live_rest_process": True,
            "token_authentication": True,
            "tag_and_limit_filter": True,
            "pagination": True,
            "device_ipam_mapping": True,
            "cable_bidirectional_topology": True,
            "shared_risk_metadata": True,
            "atomic_private_output": True,
            "cross_origin_pagination_rejected": True,
        },
        "nodes": len(nodes),
        "directed_links": len(links),
        "requests": len(requests),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, REPORT)
    print("PASS: live NetBox/Nautobot REST import, pagination, IPAM/cables/SRLG, auth and SSRF guard")


if __name__ == "__main__":
    main()
