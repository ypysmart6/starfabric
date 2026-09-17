#!/usr/bin/env python3
"""Boot the full Compose NOC and prove metrics, logs, traces, alerts, and UI."""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy/compose/docker-compose.yaml"
PROJECT = "starfabric-observability-acceptance"
REPORT = ROOT / "reports/observability-closed-loop.json"


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def request(url: str, *, method: str = "GET", auth: bool = False) -> tuple[int, bytes]:
    headers = {}
    if auth:
        headers["Authorization"] = "Basic " + base64.b64encode(b"admin:change-me").decode()
    req = urllib.request.Request(url, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def json_request(url: str, **kwargs: Any) -> tuple[int, Any]:
    code, body = request(url, **kwargs)
    return code, json.loads(body) if body else None


def wait_for(predicate: Any, timeout: float, description: str) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            pass
        time.sleep(1)
    raise AssertionError(f"timeout waiting for {description}; last={last!r}")


def compose(environment: dict[str, str], *arguments: str, timeout: int = 1200) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE), *arguments],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def main() -> None:
    names = ["API", "OTLP_GRPC", "OTLP_HTTP", "OTEL_HEALTH", "TEMPO", "LOKI", "PROMETHEUS", "GRAFANA"]
    ports = {name: free_port() for name in names}
    environment = os.environ.copy()
    for name, port in ports.items():
        environment[f"STARFABRIC_{name}_PORT"] = str(port)
    environment["GRAFANA_ADMIN_PASSWORD"] = "change-me"
    bases = {name: f"http://127.0.0.1:{port}" for name, port in ports.items()}
    checks: dict[str, Any] = {}

    config = compose(environment, "config", "--quiet", timeout=60)
    if config.returncode:
        raise AssertionError(config.stderr)
    compose(environment, "down", "--volumes", "--remove-orphans", timeout=120)
    try:
        started = compose(environment, "up", "--build", "--detach", timeout=1200)
        if started.returncode:
            raise AssertionError(f"compose up failed:\n{started.stdout}\n{started.stderr}")
        wait_for(lambda: request(bases["API"] + "/healthz")[0] == 200, 120, "controller")
        wait_for(lambda: request(bases["OTEL_HEALTH"] + "/")[0] == 200, 120, "collector")
        wait_for(lambda: request(bases["PROMETHEUS"] + "/-/ready")[0] == 200, 120, "Prometheus")
        wait_for(lambda: request(bases["LOKI"] + "/ready")[0] == 200, 120, "Loki")
        wait_for(lambda: request(bases["TEMPO"] + "/ready")[0] == 200, 120, "Tempo")
        wait_for(lambda: json_request(bases["GRAFANA"] + "/api/health")[0] == 200, 120, "Grafana")
        checks["all_six_services_ready"] = True

        code, reconciliation = json_request(bases["API"] + "/api/v1/reconcile", method="POST")
        assert code == 200 and reconciliation["status"]["phase"] == "committed"
        for path in ["/api/v1/topology", "/api/v1/devices", "/api/v1/status"]:
            assert json_request(bases["API"] + path)[0] == 200
        checks["instrumented_control_transaction"] = True

        metric_query = urllib.parse.urlencode({"query": "starfabric_topology_version"})
        metric = wait_for(
            lambda: (lambda value: value if value[0] == 200 and value[1]["data"]["result"] else None)(
                json_request(bases["PROMETHEUS"] + "/api/v1/query?" + metric_query)
            ),
            60,
            "Prometheus scrape",
        )
        assert float(metric[1]["data"]["result"][0]["value"][1]) >= 1
        checks["prometheus_scraped_controller"] = True

        code, rules = json_request(bases["PROMETHEUS"] + "/api/v1/rules")
        alert_names = {
            rule["name"]
            for group in rules["data"]["groups"]
            for rule in group["rules"]
            if rule.get("type") == "alerting"
        }
        assert code == 200 and {
            "StarFabricDesiredActualMismatch", "StarFabricReconciliationFailures", "StarFabricNoActiveLinks"
        } <= alert_names
        checks["prometheus_alert_rules_loaded"] = len(alert_names)

        log_query = urllib.parse.urlencode({"query": '{service_name="starfabric-controller"}', "limit": "20"})
        logs = wait_for(
            lambda: (lambda value: value if value[0] == 200 and value[1]["data"]["result"] else None)(
                json_request(bases["LOKI"] + "/loki/api/v1/query_range?" + log_query)
            ),
            90,
            "Loki exported logs",
        )
        checks["loki_received_structured_logs"] = sum(len(stream["values"]) for stream in logs[1]["data"]["result"])

        trace_query = urllib.parse.urlencode({"tags": "service.name=starfabric-controller", "limit": "20"})
        traces = wait_for(
            lambda: (lambda value: value if value[0] == 200 and value[1].get("traces") else None)(
                json_request(bases["TEMPO"] + "/api/search?" + trace_query)
            ),
            90,
            "Tempo exported traces",
        )
        checks["tempo_received_api_traces"] = len(traces[1]["traces"])

        code, dashboard = json_request(bases["GRAFANA"] + "/api/dashboards/uid/starfabric-noc", auth=True)
        assert code == 200 and dashboard["dashboard"]["uid"] == "starfabric-noc"
        code, datasources = json_request(bases["GRAFANA"] + "/api/datasources", auth=True)
        assert code == 200 and {item["type"] for item in datasources} >= {"prometheus", "loki", "tempo"}
        checks["grafana_noc_and_datasources_provisioned"] = True

        report = {
            "schema_version": 1,
            "success": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "checks": checks,
            "plan_id": reconciliation["plan"]["id"],
            "network_exposure": "all published ports bound to 127.0.0.1",
        }
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        temporary = REPORT.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, REPORT)
        print(
            f"PASS: metrics + {checks['loki_received_structured_logs']} logs + "
            f"{checks['tempo_received_api_traces']} traces + alerts + Grafana NOC"
        )
    finally:
        stopped = compose(environment, "down", "--volumes", "--remove-orphans", timeout=180)
        if stopped.returncode:
            print("WARN: compose cleanup failed", stopped.stderr)


if __name__ == "__main__":
    main()
