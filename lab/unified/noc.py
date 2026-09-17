"""Isolated NOC backends observing the connected runtime's actual controller."""
from __future__ import annotations

import base64
import json
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


class NOC:
    def __init__(self, runtime: Any):
        self.runtime = runtime
        self.path = runtime.art / "noc"
        self.path.mkdir()
        self.project = runtime.identifier + "-noc"
        self.ports: dict[str, int] = {}
        self.started = False
        self.trace_id = runtime.identifier.removeprefix("sf-unified-").ljust(32, "0")

    def port(self, name: str) -> int:
        while name not in self.ports:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                value = listener.getsockname()[1]
            if value not in self.ports.values() and value != self.runtime.port:
                self.ports[name] = value
        return self.ports[name]

    def url(self, name: str) -> str:
        return f"http://127.0.0.1:{self.port(name)}"

    def write(self, name: str, value: Any) -> Path:
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, indent=2) + "\n")
        return target

    def compose(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["docker", "compose", "-p", self.project, "-f", str(self.path / "compose.json"), *args],
                              capture_output=True, text=True, timeout=180, cwd=ROOT)

    def request(self, service: str, path: str, auth: bool = False) -> Any:
        headers = {"Accept": "application/json"}
        if auth:
            headers["Authorization"] = "Basic " + base64.b64encode(b"admin:sil-local").decode()
        with urllib.request.urlopen(urllib.request.Request(self.url(service) + path, headers=headers), timeout=5) as response:
            body = response.read()
            try:
                return json.loads(body)
            except ValueError:
                return body.decode()

    def wait(self, label: str, predicate: Any, timeout: int = 90) -> Any:
        deadline, last = time.monotonic() + timeout, None
        while time.monotonic() < deadline:
            try:
                last = predicate()
                if last:
                    return last
            except (OSError, ValueError, KeyError) as error:
                last = str(error)
            time.sleep(1)
        raise AssertionError(f"NOC {label} timed out: {last}")

    def start(self) -> None:
        base = ROOT / "deploy/observability"
        configs = {name: yaml.safe_load((base / (name + ".yaml")).read_text())
                   for name in ("otel-collector", "loki", "tempo")}
        loki, tempo, collector = (configs[n] for n in ("loki", "tempo", "otel-collector"))
        if getattr(self.runtime, 'live_mode', False):
            loki['limits_config']['retention_period'] = '24h'
            loki['compactor'].update(retention_enabled=True, delete_request_store='filesystem')
        loki["common"]["instance_addr"] = "127.0.0.1"
        tempo["ingester"] = {"lifecycler": {"address": "127.0.0.1"}}
        for name, config in (("loki", loki), ("tempo", tempo)):
            config["server"].update({"http_listen_address": "127.0.0.1", "http_listen_port": self.port(name),
                                     "grpc_listen_address": "127.0.0.1", "grpc_listen_port": self.port(name + "-grpc")})
        tempo["distributor"]["receivers"]["otlp"]["protocols"] = {
            "http": {"endpoint": f"127.0.0.1:{self.port('tempo-otlp')}"}}
        collector["receivers"]["otlp"]["protocols"] = {
            "http": {"endpoint": f"{getattr(self.runtime, 'otlp_bind_address', '127.0.0.1')}:{self.port('otlp')}"}}
        collector["receivers"]["filelog"]["include"] = ["/evidence/controller.log", "/evidence/timeline.jsonl"]
        collector["processors"]["resource/starfabric"]["attributes"].append(
            {"key": "run.id", "action": "upsert", "value": self.runtime.identifier})
        collector["extensions"]["health_check"]["endpoint"] = f"127.0.0.1:{self.port('collector')}"
        collector["exporters"]["otlphttp/tempo"]["endpoint"] = self.url("tempo-otlp")
        collector["exporters"]["otlphttp/loki"]["endpoint"] = self.url("loki") + "/otlp"
        # Avoid the collector's default host metrics listener colliding with another lab.
        collector["service"]["telemetry"] = {"metrics": {"level": "none"}}
        for name, config in configs.items():
            self.write(name + ".json", config)
        self.write("prometheus.json", {"global": {"scrape_interval": "1s", "evaluation_interval": "1s"},
            "rule_files": ["/config/alerts.json"], "scrape_configs": [{"job_name": "starfabric-controller",
            "static_configs": [{"targets": [f"127.0.0.1:{self.runtime.port}"],
                                "labels": {"run_id": self.runtime.identifier}}]}]})
        self.write("alerts.json", {"groups": [{"name": "unified", "rules": [{
            "alert": "StarFabricUnifiedGroundDisconnected", "expr": 'up{job="starfabric-controller"} == 0',
            "labels": {"severity": "warning"}, "annotations": {"summary": "Ground controller unavailable"}}]}]})
        self.write("provisioning/datasources/sources.yaml", {"apiVersion": 1, "datasources": [
            {"name": name.title(), "uid": name, "type": name, "access": "proxy", "url": self.url(name),
             "isDefault": name == "prometheus"} for name in ("prometheus", "loki", "tempo")]})
        self.write("provisioning/dashboards/provider.yaml", {"apiVersion": 1, "providers": [
            {"name": "StarFabric", "type": "file", "options": {"path": "/dashboards"}}]})
        dashboard = json.loads((base / "grafana/dashboards/starfabric.json").read_text())
        dashboard["tags"] = [self.runtime.identifier]
        self.write("dashboards/starfabric-noc.json", dashboard)
        compose = yaml.safe_load((ROOT / "deploy/compose/docker-compose.yaml").read_text())
        services: dict[str, Any] = {}
        for name in ("loki", "tempo", "otel-collector", "prometheus", "grafana"):
            services[name] = {"image": compose["services"][name]["image"], "network_mode": "host",
                              "labels": {"starfabric.run": self.runtime.identifier},
                              "volumes": [f"{self.path}:/config:ro"], "restart": "no"}
        for name in ("loki", "tempo"):
            services[name]["command"] = [f"-config.file=/config/{name}.json"]
            services[name]["volumes"].append(f"{name}-data:" + ("/loki" if name == "loki" else "/var/tempo"))
        services["otel-collector"]["command"] = ["--config=/config/otel-collector.json"]
        services["otel-collector"]["volumes"].append(f"{self.runtime.art}:/evidence:ro")
        services["prometheus"]["command"] = ["--config.file=/config/prometheus.json", "--storage.tsdb.path=/prometheus",
                                             f"--web.listen-address=127.0.0.1:{self.port('prometheus')}"]
        services["prometheus"]["volumes"].append("prometheus-data:/prometheus")
        if getattr(self.runtime, 'live_mode', False):
            services['prometheus']['command'] += ['--storage.tsdb.retention.time=24h','--storage.tsdb.retention.size=1GB']
            for service in services.values():
                service['logging'] = {'driver':'json-file','options':{'max-size':'5m','max-file':'2'}}
        services["grafana"]["environment"] = {"GF_SERVER_HTTP_ADDR": "127.0.0.1", "GF_SERVER_HTTP_PORT": str(self.port("grafana")),
            "GF_SECURITY_ADMIN_PASSWORD": "sil-local", "GF_USERS_ALLOW_SIGN_UP": "false"}
        services["grafana"]["volumes"] += [f"{self.path}/provisioning:/etc/grafana/provisioning:ro",
                                             f"{self.path}/dashboards:/dashboards:ro", "grafana-data:/var/lib/grafana"]
        self.write("compose.json", {"services": services, "volumes": {n + "-data": {} for n in ("loki", "tempo", "prometheus", "grafana")}})
        self.started = True
        result = self.compose("up", "--detach", "--pull", "never")
        (self.path / "start.log").write_text(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError("NOC startup failed: " + result.stderr[-2000:])
        for service, path in (("loki", "/ready"), ("tempo", "/ready"), ("collector", "/"),
                              ("prometheus", "/-/ready"), ("grafana", "/api/health")):
            self.wait(service + " ready", lambda s=service, p=path: self.request(s, p))

    def verify(self) -> None:
        identifier = self.runtime.identifier
        query = urllib.parse.urlencode({"query": f'starfabric_topology_version{{run_id="{identifier}"}}'})
        topology = self.runtime.request("/api/v1/topology")
        def current_metric() -> Any:
            result = self.request("prometheus", "/api/v1/query?" + query)["data"]["result"]
            return result if result and int(float(result[0]["value"][1])) == topology["version"] else None
        metric = self.wait("actual topology scrape", current_metric)
        self.write("topology-metric.json", metric)
        self.runtime.checks["noc_scrapes_same_controller_topology"] = True
        query = urllib.parse.urlencode({"query": '{service_name="starfabric-controller"} |= "' + identifier + '"', "limit": 1000})
        def logs_complete() -> Any:
            streams = self.request("loki", "/loki/api/v1/query_range?" + query)["data"]["result"]
            text = json.dumps(streams)
            plans = [event["plan_id"] for event in self.runtime.timeline if event.get("plan_id")]
            return streams if all(plan in text for plan in plans) and "ground_controller_stopped" in text and "ground-restored-commit" in text else None
        logs = self.wait("correlated plans and fault timeline", logs_complete)
        self.write("loki-correlated-logs.json", logs)
        self.runtime.checks["noc_correlates_plan_fault_recovery_logs"] = True
        trace = self.wait("actual API trace", lambda: self.request("tempo", "/api/traces/" + self.trace_id))
        self.write("tempo-api-trace.json", trace)
        expected_trace = base64.b64encode(bytes.fromhex(self.trace_id)).decode()
        matched = []
        for batch in trace.get("batches", []):
            resources = {a["key"]: a["value"] for a in batch.get("resource", {}).get("attributes", [])}
            if resources.get("run.id", {}).get("stringValue") != identifier:
                continue
            for scope in batch.get("scopeSpans", []):
                for span in scope.get("spans", []):
                    attrs = {a["key"]: a["value"] for a in span.get("attributes", [])}
                    if (span.get("traceId") == expected_trace
                            and attrs.get("url.path", {}).get("stringValue") == "/api/v1/reconcile"
                            and attrs.get("http.request.method", {}).get("stringValue") == "POST"
                            and str(attrs.get("http.response.status_code", {}).get("intValue")) == "200"):
                        matched.append(span)
        assert len(matched) >= 2, f"expected initial and recovered commits in this run's trace; found {len(matched)}"
        self.runtime.checks["noc_receives_actual_controller_trace"] = True
        stopped = next(event["at"] for event in self.runtime.timeline if event["event"] == "ground_controller_stopped")
        recovered = next(event["at"] for event in self.runtime.timeline if event["event"] == "ground-restored-commit")
        query = urllib.parse.urlencode({"query": f'ALERTS{{alertname="StarFabricUnifiedGroundDisconnected",run_id="{identifier}",alertstate="firing"}}',
            "start": stopped, "end": recovered, "step": "1"})
        alerts = self.wait("ground outage firing alert", lambda: self.request("prometheus", "/api/v1/query_range?" + query)["data"]["result"])
        assert any(float(sample[1]) == 1 for item in alerts for sample in item["values"]), alerts
        self.write("outage-alert.json", alerts)
        self.runtime.checks["noc_observes_ground_outage_alert"] = True
        dashboard = self.request("grafana", "/api/dashboards/uid/starfabric-noc", auth=True)
        sources = self.request("grafana", "/api/datasources", auth=True)
        assert identifier in dashboard["dashboard"]["tags"]
        assert {item["type"] for item in sources} >= {"prometheus", "loki", "tempo"}
        self.write("grafana-dashboard.json", dashboard)
        self.write("grafana-datasources.json", sources)
        self.runtime.checks["noc_grafana_backends_connected"] = True

    def cleanup(self) -> list[str]:
        if not self.started:
            return []
        logs = self.compose("logs", "--no-color")
        (self.path / "services.log").write_text(logs.stdout + logs.stderr)
        stopped = self.compose("down", "--volumes", "--remove-orphans")
        (self.path / "cleanup.log").write_text(stopped.stdout + stopped.stderr)
        return [stopped.stderr] if stopped.returncode else []
