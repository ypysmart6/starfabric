"""Kubernetes control plane for the same live constellation and PDU session."""
from __future__ import annotations

import http.client
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from runtime import Cluster, ROOT, execute, helm, wait_for, write
from frr_agent import Agent


class Cloud:
    def __init__(self, runtime):
        self.runtime = runtime
        self.cluster = Cluster(2)
        self.cluster.artifacts.rmdir()
        self.cluster.artifacts = runtime.art / "cloud"
        self.cluster.artifacts.mkdir()
        self.agent = self.proxy = None
        self.installed = False

    def prepare(self):
        c, r = self.cluster, self.runtime
        c.build_application()
        c.create()
        nodes=execute(['docker','ps','--filter','label=io.x-k8s.kind.cluster='+c.name,'--format','{{.Names}}']).stdout.splitlines()
        assert len(nodes)==3,nodes
        # A kind node hosts many control-plane processes. Give it a larger
        # relative CPU share than one FRR namespace during fleet convergence.
        r.host_budget.limit_containers(nodes, cpu_shares=8192 if getattr(r,'live_mode',False) else None)
        c.prepare_application()
        bridge = json.loads(execute(["docker", "network", "inspect", "kind"]).stdout)[0]
        self.gateway = next(v["Gateway"] for v in bridge["IPAM"]["Config"] if ":" not in v["Gateway"])
        r.otlp_bind_address = self.gateway
        self.agent = Agent(r, self.gateway, secrets.token_urlsafe(32))
        c.apply({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "platform-frr-agent", "namespace": "starfabric"},
                 "stringData": {"token": self.agent.token}})
        # A hard link exposes exactly the pod's durable log to the NOC tailer.
        logfile = c.work / "shared/ha/controller.log"
        logfile.touch(mode=0o666)
        logfile.chmod(0o666)
        os.link(logfile, r.art / "controller.log")
        values = json.loads(c.values("ha").read_text())
        if getattr(r, 'live_mode', False):
            # The 6s/1s profile is for the bounded HA fault gate. During a
            # 120-router topology update its 1s API deadline can repeatedly
            # revoke a healthy writer. Keep lease fencing, with enough time
            # for the shared SIL host to service Kubernetes requests.
            values['ha'].update(leaseDuration='30s', retryPeriod='5s')
        values.update({"scenario": r.scenario, "reconcileInterval": "0s",
                       # A bounded gateway probe fan-out needs a fleet-wide
                       # verification window. Individual FRR subprocesses
                       # remain limited by the execution agent's 20s budget.
                       "controllerTuning": {"planTTL": "10m", "operationTimeout": "60s" if getattr(r,'live_mode',False) else f"{getattr(r, 'frr_operation_timeout', 8)+2}s", "httpWriteTimeout": "300s"},
                       "adapter": {"kind": "frr", "frrAgentEndpoint": self.agent.endpoint,
                                   "frrAgentSecret": "platform-frr-agent", "frrAgentInsecure": True},
                       "observability": {"otlpEndpoint": f"{self.gateway}:{r.noc.port('otlp')}", "otlpInsecure": True}})
        values["ciliumNetworkPolicy"]["extraEgress"] = [{"toCIDRSet": [{"cidr": self.gateway + "/32"}],
            "toPorts": [{"ports": [{"port": str(self.agent.server.server_port), "protocol": "TCP"},
                                    {"port": str(r.noc.port('otlp')), "protocol": "TCP"}]}]}]
        self.values = c.work / "platform-values.json"
        write(self.values, values)
        r.event("cloud_infrastructure_ready", cluster=c.name, nodes=3, adapter="frr", agent=self.agent.endpoint)

    def start(self):
        c, r = self.cluster, self.runtime
        if not self.installed:
            with c.operation('controller-install', '安装 3 副本地面控制器', 'Helm / starfabric / ha', 900):
                helm(["install", "ha", str(ROOT / "deploy/helm/starfabric"), "-n", "starfabric", "-f", str(self.values)], c.kubeconfig)
            self.installed = True
            service = c.get("service", "ha-starfabric")
            self.start_proxy(service["spec"]["clusterIP"], 8080)
        else:
            c.k("-n", "starfabric", "scale", "deployment/ha-starfabric", "--replicas=3")
        with c.operation('controller-leader', '等待控制器选主', '3 个副本中应有 1 个持有 Lease 的就绪主实例', 180):
            wait_for("one ready FRR controller Lease leader", lambda: len(c.ready_pods("ha")) == 1, 180)
        with c.operation('controller-ready', '检查控制器服务接口', 'GET /readyz', 60):
            wait_for("cloud service FRR readiness", lambda: r.request("/readyz"), 60)
        c.save("platform-controller-deployment", c.get("deployment", "ha-starfabric"))
        c.save("platform-lease", c.get("lease", "starfabric-controller"))
        r.checks.update({"helm_deploys_real_frr_adapter": True,
                         "lease_single_ready_writer": True, "cilium_cni_ready": True, "hubble_relay_ready": True})

    def start_proxy(self, host, port):
        node = self.cluster.name + "-control-plane"
        request_timeout = self.runtime.request_timeout
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def relay(self):
                try:
                    body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                    # The SIL ingress enters through the owned node's network
                    # namespace and its ClusterIP service. This keeps Cilium's
                    # normal host identity across HA endpoint changes.
                    args = ["docker", "exec", "-i", node, "curl", "-sS", "--connect-timeout", "1", "--max-time", str(request_timeout),
                            "-X", self.command, "-w", "\n%{http_code}"]
                    for key in ("Content-Type", "X-Request-ID", "traceparent", "Authorization"):
                        if key in self.headers:
                            args += ["-H", key + ": " + self.headers[key]]
                    if body:
                        args += ["--data-binary", "@-"]
                    result = execute(args + [f"http://{host}:{port}{self.path}"], stdin=body.decode() if body else None, timeout=request_timeout + 5)
                    raw, code = result.stdout.rsplit("\n", 1)
                    data = raw.encode()
                    self.send_response(int(code))
                    self.send_header("Content-Type", "text/plain" if self.path == "/metrics" else "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except (OSError, RuntimeError, ValueError):
                    try:
                        self.send_error(503, "cloud controller unavailable")
                    except (BrokenPipeError, ConnectionResetError):
                        pass

            do_GET = do_POST = do_PUT = do_DELETE = relay

        self.proxy = ThreadingHTTPServer(("127.0.0.1", self.runtime.port), Handler)
        self.proxy.daemon_threads = True
        threading.Thread(target=self.proxy.serve_forever, daemon=True).start()

    def stop(self):
        if self.installed:
            self.cluster.k("-n", "starfabric", "scale", "deployment/ha-starfabric", "--replicas=0")
            wait_for("all ground replicas stopped", lambda: not self.cluster.pods("ha"), 60)

    def failover(self):
        c, r = self.cluster, self.runtime
        before = c.get("lease", "starfabric-controller")
        topology = r.request("/api/v1/topology")
        old = before["spec"]["holderIdentity"]
        replicas = c.pods("ha")
        assert len(replicas) == 3
        assert len({p["spec"]["nodeName"] for p in replicas}) == 3
        for pod in replicas:
            if pod["metadata"]["name"] != old:
                code, body = c.request("ha", "/api/v1/reconcile", "POST", host=pod["status"]["podIP"] + ":8080")
                assert code == 503 and "not_leader" in json.dumps(body), (code, body)
        r.checks["lease_followers_reject_real_frr_mutations"] = True
        r.checks["lease_replicas_on_three_kubernetes_nodes"] = True
        c.save("platform-lease-before-fault", before)
        r.event("lease_leader_deleted", pod=old)
        started = time.monotonic()
        c.k("-n", "starfabric", "delete", "pod", old, "--wait=true", "--timeout=60s")
        wait_for("successor ready", lambda: len(c.ready_pods("ha")) == 1 and c.ready_pods("ha")[0]["metadata"]["name"] != old, 90)
        wait_for("successor API", lambda: r.request("/readyz"), 60)
        after = c.get("lease", "starfabric-controller")
        assert after["spec"]["holderIdentity"] != old
        assert after["spec"].get("leaseTransitions", 0) > before["spec"].get("leaseTransitions", 0)
        assert r.request("/api/v1/topology") == topology
        r.reconcile("lease-successor-real-frr-commit")
        r.ping("lease-successor-same-pdu", 20)
        c.save("platform-lease-after-fault", after)
        r.checks["lease_failover_preserves_real_frr_and_5g"] = True
        r.event("lease_successor_verified", pod=after["spec"]["holderIdentity"], elapsed_seconds=time.monotonic() - started)

    def policy(self):
        c, r = self.cluster, self.runtime
        host = c.get("service", "ha-starfabric")["spec"]["clusterIP"] + ":8080"
        assert c.request("ha", "/readyz", host=host)[0] == 200
        r.event("cilium_controller_api_denied")
        c.apply({"apiVersion": "cilium.io/v2", "kind": "CiliumNetworkPolicy", "metadata": {"name": "platform-deny-probe", "namespace": "starfabric"},
                    "spec": {"endpointSelector": {"matchLabels": {"app": "sf-probe"}}, "egress": [{"toEntities": ["all"]}],
                             "egressDeny": [{"toEndpoints": [{"matchLabels": {"app.kubernetes.io/instance": "ha"}}]}]}})
        def blocked():
            try:
                c.request("ha", "/readyz", host=host)
            except RuntimeError:
                return True
            return False
        try:
            wait_for("Cilium denies access to actual constellation controller", blocked, 60)
            r.ping("cilium-control-api-denied-data-plane-alive", 20)
        finally:
            c.k("-n", "starfabric", "delete", "ciliumnetworkpolicy", "platform-deny-probe")
        wait_for("Cilium controller API recovery", lambda: c.request("ha", "/readyz", host=host)[0] == 200, 60)
        flows = []
        for pod in c.get("pods", ns="kube-system")["items"]:
            if pod["metadata"].get("labels", {}).get("k8s-app") != "cilium":
                continue
            raw = c.k("-n", "kube-system", "exec", pod["metadata"]["name"], "-c", "cilium-agent", "--", "hubble", "observe",
                      "--server", "unix:///var/run/cilium/hubble.sock", "--since", "10m", "--namespace", "starfabric", "--output", "json")
            flows.extend(json.loads(line) for line in raw.splitlines() if line.startswith("{"))
        c.save("platform-hubble-flows", flows)
        selected = [v.get("flow", v) for v in flows]
        selected = [v for v in selected if v.get("source", {}).get("pod_name") == "probe"
                    and v.get("destination", {}).get("pod_name", "").startswith("ha-starfabric-")
                    and v.get("l4", {}).get("TCP", {}).get("destination_port") == 8080]
        forwarded = sum(v.get("verdict") == "FORWARDED" for v in selected)
        dropped = sum(v.get("verdict") == "DROPPED" and v.get("drop_reason_desc") in {"POLICY_DENIED", "POLICY_DENY"} for v in selected)
        assert forwarded and dropped, (forwarded, dropped)
        r.checks.update({"cilium_policy_on_same_controller": True, "hubble_same_controller_forwarded": forwarded,
                         "hubble_same_controller_policy_dropped": dropped})
        r.event("cilium_controller_api_recovered", forwarded=forwarded, policy_dropped=dropped)

    def cleanup(self):
        if self.proxy:
            self.proxy.shutdown()
            self.proxy.server_close()
        if self.agent:
            self.agent.close()
        self.cluster.cleanup()
