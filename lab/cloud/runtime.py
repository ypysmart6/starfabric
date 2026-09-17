#!/usr/bin/env python3
"""Real, isolated single-host Kubernetes/Cilium/Hubble and controller acceptance.

Never uses the user's kubeconfig, cluster, volumes, or Kubernetes credentials.
Shared hostPath storage is deliberately a SIL fixture, not production RWX.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import time
import urllib.request
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from validate import HELM_IMAGE

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / ".cache/cloud-runtime"
KIND_VERSION = "v0.31.0"
KIND = CACHE / f"kind-{KIND_VERSION}"
NODE_IMAGE = "kindest/node:v1.35.0@sha256:452d707d4862f52530247495d180205e029056831160e22870e37e3f6c1ac31f"
CILIUM_VERSION = "1.20.1"
CHART = CACHE / f"cilium-{CILIUM_VERSION}.tgz"
REPORT = ROOT / "reports/cloud-runtime.json"


def cilium_images() -> list[str]:
    with tarfile.open(CHART) as archive:
        values = yaml.safe_load(archive.extractfile("cilium/values.yaml"))
    agent, operator, relay = values["image"], values["operator"]["image"], values["hubble"]["relay"]["image"]
    return [f"{agent['repository']}:{agent['tag']}@{agent['digest']}",
            f"{operator['repository']}-generic:{operator['tag']}@{operator['genericDigest']}",
            f"{relay['repository']}:{relay['tag']}@{relay['digest']}"]


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def execute(args: list[str], *, stdin: str | None = None, timeout: int = 300,
            check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, input=stdin, capture_output=True, text=True,
                            cwd=ROOT, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"{args[:5]} exited {result.returncode}:\n{result.stdout[-6000:]}\n{result.stderr[-6000:]}")
    return result


def helm(args: list[str], kubeconfig: Path | None = None) -> str:
    command = ["docker", "run", "--rm", "--network=host", "--user", f"{os.getuid()}:{os.getgid()}",
               "-v", f"{ROOT}:{ROOT}", "-w", str(ROOT), "-e", "HELM_CACHE_HOME=/tmp/helm/cache",
               "-e", "HELM_CONFIG_HOME=/tmp/helm/config", "-e", "HELM_DATA_HOME=/tmp/helm/data", HELM_IMAGE]
    if kubeconfig:
        command += ["--kubeconfig", str(kubeconfig)]
    return execute(command + args, timeout=900).stdout


def bootstrap() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())
    if platform.system() != "Linux" or arch is None:
        raise RuntimeError("this SIL gate currently requires Linux amd64 or arm64")
    if not KIND.exists():
        url = f"https://github.com/kubernetes-sigs/kind/releases/download/{KIND_VERSION}/kind-linux-{arch}"
        expected = urllib.request.urlopen(url + ".sha256sum", timeout=60).read().decode().split()[0]
        target = KIND.with_suffix(".download")
        with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if len(expected) != 64 or actual != expected:
            target.unlink()
            raise RuntimeError("kind release checksum mismatch")
        target.chmod(0o755)
        target.replace(KIND)
        write(CACHE / "kind-source.json", {"url": url, "sha256": actual})
    if not CHART.exists():
        helm(["pull", "cilium", "--repo", "https://helm.cilium.io", "--version", CILIUM_VERSION,
              "--destination", str(CACHE)])
    for image in cilium_images():
        if execute(["docker", "image", "inspect", image], check=False).returncode:
            print(f"Caching {image}", flush=True)
            execute(["docker", "pull", image], timeout=1800)
    print(f"Cached kind {KIND_VERSION} and Cilium chart {CILIUM_VERSION}", flush=True)


def wait_for(description: str, check: Callable[[], Any], timeout: int = 180) -> Any:
    deadline, last = time.monotonic() + timeout, None
    while time.monotonic() < deadline:
        try:
            last = check()
            if last:
                return last
        except (RuntimeError, KeyError, ValueError, OSError) as error:
            last = str(error)
        time.sleep(2)
    raise AssertionError(f"timeout waiting for {description}: {last}")


class Cluster:
    def __init__(self, workers: int):
        self.name = "sf-cloud-" + uuid.uuid4().hex[:10]
        self.work = CACHE / self.name
        self.work.mkdir(parents=True)
        self.artifacts = ROOT / "lab/cloud/artifacts" / self.name
        self.artifacts.mkdir(parents=True)
        self.kubeconfig = self.work / "kubeconfig"
        self.kubectl = self.work / "kubectl"
        self.image = f"starfabric/controller:{self.name}"
        self.workers = workers
        self.created = False
        self.image_built = False
        self.checks: dict[str, Any] = {}
        self.evidence: dict[str, Any] = {}

    def save(self, name: str, value: Any) -> None:
        write(self.artifacts / f"{name}.json", value)

    def k(self, *args: str, stdin: str | None = None, check: bool = True) -> str:
        return execute([str(self.kubectl), "--kubeconfig", str(self.kubeconfig),
                        "--request-timeout=30s", *args], stdin=stdin, timeout=660, check=check).stdout

    def get(self, kind: str, name: str = "", ns: str = "starfabric") -> Any:
        return json.loads(self.k("-n", ns, "get", kind, *([name] if name else []), "-o", "json"))

    def apply(self, value: Any) -> None:
        self.k("apply", "-f", "-", stdin=json.dumps(value))

    def progress(self, message: str) -> None:
        print(f"[{stamp()}] {message}", flush=True)

    def operation(self, key, title, detail='', timeout=None):
        observer = getattr(self, 'observe_operation', None)
        return observer(key, title, detail, timeout) if observer else nullcontext()

    def load_images(self, images: list[str]) -> None:
        # Docker's containerd image store can retain a multi-platform index
        # with only the host architecture downloaded. kind's --all-platforms
        # import then asks for absent foreign layers. Import the host platform.
        arch = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
        nodes = execute([str(KIND), "get", "nodes", "--name", self.name]).stdout.split()
        archive = self.work / "image-import.tar"
        try:
            for index, image in enumerate(images, 1):
                with self.operation('image-export:'+image, f'导出镜像 {index}/{len(images)}', image, 300):
                    execute(["docker", "image", "save", "--platform", "linux/" + arch, "-o", str(archive), image], timeout=300)
                reference = image.split("@")[0]
                if "." not in reference.split("/")[0] and ":" not in reference.split("/")[0]:
                    reference = "docker.io/" + ("library/" if "/" not in reference else "") + reference
                repository = reference.rsplit(":", 1)[0]
                for node_index, node in enumerate(nodes, 1):
                    with self.operation('image-import:'+image+':'+node, f'导入镜像 {index}/{len(images)} · 节点 {node_index}/{len(nodes)}', image+' → '+node, 300):
                        with archive.open("rb") as stream:
                            result = subprocess.run(["docker", "exec", "--privileged", "-i", node, "ctr", "--namespace=k8s.io", "images", "import",
                                "--platform", "linux/" + arch, "--base-name", repository, "--index-name", reference,
                                "--digests", "--snapshotter=overlayfs", "-"], stdin=stream,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
                        if result.returncode:
                            raise RuntimeError(f"image import {image}: {result.stderr.decode(errors='replace')}")
        finally:
            archive.unlink(missing_ok=True)

    def create(self) -> None:
        self.progress(f"Creating isolated Kubernetes cluster {self.name}, {self.workers + 1} nodes")
        nodes = []
        shared = self.work / "shared"
        shared.mkdir()
        for directory in (shared / "single", shared / "ha"):
            directory.mkdir(mode=0o777)
            directory.chmod(0o777)
        for role in ["control-plane"] + ["worker"] * self.workers:
            nodes.append({"role": role, "extraMounts": [{"hostPath": str(shared), "containerPath": "/sf-sil"}]})
        config = self.work / "kind.json"
        write(config, {"kind": "Cluster", "apiVersion": "kind.x-k8s.io/v1alpha4", "nodes": nodes,
                       "networking": {"apiServerAddress": "127.0.0.1", "disableDefaultCNI": True}})
        # A unique name establishes ownership even if creation fails partway.
        self.created = True
        with self.operation('kind-create', '创建 Kubernetes 集群', f'{self.name} · 1 个控制节点 + {self.workers} 个工作节点', 900):
            result = execute([str(KIND), "create", "cluster", "--name", self.name, "--image", NODE_IMAGE,
                              "--config", str(config), "--kubeconfig", str(self.kubeconfig)], timeout=900)
            (self.artifacts / "create.log").write_text(result.stdout + result.stderr)
        with self.operation('kubectl-prepare', '准备集群管理工具', '复制本轮 kubectl，配置节点调度'):
            execute(["docker", "cp", f"{self.name}-control-plane:/usr/bin/kubectl", str(self.kubectl)])
            self.kubeconfig.chmod(0o600)
            self.k("taint", "nodes", "--all", "node-role.kubernetes.io/control-plane-", check=False)
        self.progress("Loading cached digest-pinned Cilium images into all nodes")
        self.load_images(cilium_images())
        self.progress("Installing Cilium and Hubble relay")
        with self.operation('cilium-install', '安装 Cilium 与 Hubble', 'Helm 安装集群网络与流量观测组件', 900):
            helm(["install", "cilium", str(CHART), "--namespace", "kube-system",
                  "--set", "ipam.mode=kubernetes", "--set", "kubeProxyReplacement=false",
                  "--set", "operator.replicas=1", "--set", "hubble.enabled=true",
                  "--set", "hubble.relay.enabled=true", "--set", "envoy.enabled=false"], self.kubeconfig)
        with self.operation('cilium-ready', '等待 Cilium 就绪', 'kube-system / daemonset/cilium', 600):
            self.k("-n", "kube-system", "rollout", "status", "daemonset/cilium", "--timeout=600s")
        with self.operation('hubble-ready', '等待 Hubble Relay 就绪', 'kube-system / deployment/hubble-relay', 180):
            self.k("-n", "kube-system", "rollout", "status", "deployment/hubble-relay", "--timeout=180s")
        with self.operation('nodes-ready', '等待全部节点 Ready', f'期望 {self.workers + 1} 个节点 Ready', 180):
            self.k("wait", "--for=condition=Ready", "nodes", "--all", "--timeout=180s")
        self.save("nodes", self.get("nodes"))
        self.save("cilium-pods", self.get("pods", ns="kube-system"))
        self.checks["kubernetes_nodes_ready"] = self.workers + 1
        self.checks["cilium_daemonset_ready"] = True
        self.checks["hubble_relay_ready"] = True
        self.evidence["node_image"] = NODE_IMAGE
        self.evidence["cilium_chart_sha256"] = hashlib.sha256(CHART.read_bytes()).hexdigest()

    def build_application(self) -> None:
        self.progress("Building current controller before starting cluster resources")
        with self.operation('controller-image-build', '构建控制器镜像', self.image, 1200):
            result = execute(["docker", "build", "--progress=plain", "-t", self.image, "."], timeout=1200, check=False)
            (self.artifacts / "controller-build.log").write_text(result.stdout + result.stderr)
            if result.returncode:
                raise RuntimeError(f"controller build failed; see {self.artifacts / 'controller-build.log'}")
        self.image_built = True

    def prepare_application(self) -> None:
        self.progress("Loading application/probe images")
        # The probe uses curl shipped in our existing pinned Helm tool image.
        self.load_images([self.image, HELM_IMAGE.split("@")[0]])
        self.apply({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "starfabric"}})
        self.apply({"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "probe", "namespace": "starfabric",
                    "labels": {"app": "sf-probe"}}, "spec": {"containers": [{"name": "probe",
                    "image": HELM_IMAGE.split("@")[0], "imagePullPolicy": "Never", "command": ["sh", "-c", "sleep 86400"]}]}})
        with self.operation('probe-ready', '等待探测 Pod 就绪', 'starfabric / pod/probe', 180):
            self.k("-n", "starfabric", "wait", "--for=condition=Ready", "pod/probe", "--timeout=180s")
        for release in ("single", "ha"):
            self.apply({"apiVersion": "v1", "kind": "PersistentVolume", "metadata": {"name": f"sf-{release}"},
                        "spec": {"capacity": {"storage": "1Gi"}, "accessModes": ["ReadWriteMany"],
                                 "persistentVolumeReclaimPolicy": "Retain", "storageClassName": "sf-sil",
                                 "claimRef": {"namespace": "starfabric", "name": f"{release}-starfabric"},
                                 "hostPath": {"path": f"/sf-sil/{release}", "type": "Directory"}}})

    def values(self, release: str, seed: int = 1) -> Path:
        path = self.work / f"{release}-{seed}.json"
        write(path, {"image": {"repository": "starfabric/controller", "tag": self.name, "pullPolicy": "Never"},
                     "persistence": {"storageClassName": "sf-sil", "accessModes": ["ReadWriteMany"]},
                     "ciliumNetworkPolicy": {"enabled": True}, "scenario": {"random_seed": seed},
                     "replicaCount": 3 if release == "ha" else 1,
                     "ha": {"enabled": release == "ha", "leaseDuration": "6s", "retryPeriod": "1s"}})
        return path

    def request(self, release: str, path: str, method: str = "GET", body: Any = None,
                host: str | None = None) -> tuple[int, Any]:
        args = ["-n", "starfabric", "exec", "probe", "--", "curl", "-sS", "--connect-timeout", "3",
                "--max-time", "8", "-w", "\n%{http_code}", "-X", method]
        if body is not None:
            args += ["-H", "Content-Type: application/json", "--data-binary", json.dumps(body)]
        args += [f"http://{host or release + '-starfabric:8080'}{path}"]
        response = self.k(*args).rsplit("\n", 1)
        return int(response[1]), json.loads(response[0])

    def reconcile(self, release: str) -> Any:
        # Deployment readiness can precede EndpointSlice/kube-proxy propagation.
        wait_for(release + " service readiness", lambda: self.request(release, "/readyz")[0] == 200, 60)
        code, result = self.request(release, "/api/v1/reconcile", "POST")
        assert code == 200 and result["status"]["phase"] == "committed", result
        return result

    def pods(self, release: str) -> list[Any]:
        return [p for p in self.get("pods")["items"]
                if p["metadata"].get("labels", {}).get("app.kubernetes.io/instance") == release
                and not p["metadata"].get("deletionTimestamp")]

    def ready_pods(self, release: str) -> list[Any]:
        return [p for p in self.pods(release) if any(c["type"] == "Ready" and c["status"] == "True"
                for c in p.get("status", {}).get("conditions", []))]

    def single(self) -> None:
        self.progress("Verifying Helm deployment, scenario restart, rollback and persistent state")
        helm(["install", "single", str(ROOT / "deploy/helm/starfabric"), "-n", "starfabric", "-f", str(self.values("single"))], self.kubeconfig)
        self.k("-n", "starfabric", "rollout", "status", "deployment/single-starfabric", "--timeout=180s")
        initial = self.reconcile("single")
        self.save("single-initial", initial)
        before = self.ready_pods("single")[0]["metadata"]["uid"]
        annotations = self.get("deployment", "single-starfabric")["spec"]["template"]["metadata"]["annotations"]
        helm(["upgrade", "single", str(ROOT / "deploy/helm/starfabric"), "-n", "starfabric", "-f", str(self.values("single", 2))], self.kubeconfig)
        self.k("-n", "starfabric", "rollout", "status", "deployment/single-starfabric", "--timeout=180s")
        assert self.ready_pods("single")[0]["metadata"]["uid"] != before
        assert self.get("deployment", "single-starfabric")["spec"]["template"]["metadata"]["annotations"] != annotations
        self.save("single-upgraded", self.reconcile("single"))
        helm(["rollback", "single", "1", "-n", "starfabric"], self.kubeconfig)
        self.k("-n", "starfabric", "rollout", "status", "deployment/single-starfabric", "--timeout=180s")
        assert self.get("deployment", "single-starfabric")["spec"]["template"]["metadata"]["annotations"] == annotations
        self.save("single-rollback", self.reconcile("single"))
        self.checks.update({"helm_runtime_deploy": True, "scenario_change_restarts_pod": True,
                            "single_replica_upgrade_and_rollback": True})

    def ha(self) -> None:
        self.progress("Verifying real Kubernetes Lease, RBAC, PDB and durable leader handoff")
        helm(["install", "ha", str(ROOT / "deploy/helm/starfabric"), "-n", "starfabric", "-f", str(self.values("ha"))], self.kubeconfig)
        wait_for("one elected ready leader", lambda: len(self.ready_pods("ha")) == 1)
        wait_for("three running replicas", lambda: len(self.pods("ha")) == 3 and
                 all(p.get("status", {}).get("phase") == "Running" for p in self.pods("ha")))
        leader = self.ready_pods("ha")[0]
        name = leader["metadata"]["name"]
        lease = self.get("lease", "starfabric-controller")
        assert lease["spec"]["holderIdentity"] == name
        self.save("lease-before", lease)
        for pod in self.pods("ha"):
            host = pod["status"]["podIP"] + ":8080"
            assert self.request("ha", "/healthz", host=host)[0] == 200
            if pod["metadata"]["name"] != name:
                code, body = self.request("ha", "/api/v1/reconcile", "POST", host=host)
                assert code == 503 and "not_leader" in json.dumps(body), body
        self.reconcile("ha")
        before = self.request("ha", "/api/v1/topology")[1]
        link = next(link for link in before["links"] if link["id"] == "ab")
        link["latency_us"] = 9000
        event = {"event_id": "cloud-handoff", "type": "link_update", "subject": "ab",
                 "sequence": 1, "observed_at": stamp(), "effective_at": stamp(), "link": link}
        code, body = self.request("ha", "/api/v1/topology/events", "POST", event)
        assert code == 202, body
        self.reconcile("ha")
        topology = self.request("ha", "/api/v1/topology")[1]
        assert topology["version"] > before["version"]
        assert next(item for item in topology["links"] if item["id"] == "ab")["latency_us"] == 9000
        self.save("ha-topology-before", topology)
        self.save("ha-pods-before", self.pods("ha"))
        nodes = {p["spec"]["nodeName"] for p in self.pods("ha")}
        assert len(nodes) == min(3, self.workers + 1), nodes
        allowed = self.k("auth", "can-i", "update", "leases", "-n", "starfabric",
                         "--as=system:serviceaccount:starfabric:ha-starfabric").strip()
        denied = self.k("auth", "can-i", "list", "secrets", "-n", "starfabric",
                        "--as=system:serviceaccount:starfabric:ha-starfabric", check=False).strip()
        assert allowed == "yes" and denied == "no", (allowed, denied)
        wait_for("PDB accounting", lambda: self.get("pdb", "ha-starfabric").get("status", {}).get("currentHealthy") == 1)
        eviction = {"apiVersion": "policy/v1", "kind": "Eviction", "metadata": {"name": name, "namespace": "starfabric"}}
        result = execute([str(self.kubectl), "--kubeconfig", str(self.kubeconfig), "create", "--raw",
                          f"/api/v1/namespaces/starfabric/pods/{name}/eviction", "-f", "-"], stdin=json.dumps(eviction), check=False)
        assert result.returncode != 0 and "disruption budget" in result.stderr.lower(), result.stderr
        started = time.monotonic()
        self.k("-n", "starfabric", "delete", "pod", name, "--wait=true", "--timeout=60s")
        wait_for("replacement leader", lambda: len(self.ready_pods("ha")) == 1 and self.ready_pods("ha")[0]["metadata"]["name"] != name, 90)
        wait_for("service routes to successor", lambda: self.request("ha", "/readyz")[0] == 200, 30)
        successor = self.get("lease", "starfabric-controller")
        assert successor["spec"]["holderIdentity"] != name
        assert successor["spec"].get("leaseTransitions", 0) > lease["spec"].get("leaseTransitions", 0)
        recovered = self.request("ha", "/api/v1/topology")[1]
        assert recovered == topology, (recovered, topology)
        self.save("ha-successor-commit", self.reconcile("ha"))
        self.save("lease-after", successor)
        self.save("ha-topology-after", recovered)
        self.checks.update({"real_lease_single_writer": True, "follower_mutations_rejected": True,
                            "rbac_least_privilege": True, "pdb_blocks_leader_eviction": True,
                            "anti_affinity_node_count": len(nodes), "durable_state_after_leader_deletion": True,
                            "anti_affinity_three_nodes": len(nodes) == 3,
                            "health_probes_runtime": True})
        self.evidence["leader_handoff_seconds"] = time.monotonic() - started

    def network(self) -> None:
        self.progress("Verifying Cilium policy denial/recovery and observed Hubble packets")
        host = self.get("service", "single-starfabric")["spec"]["clusterIP"] + ":8080"
        assert self.request("single", "/readyz", host=host)[0] == 200
        self.apply({"apiVersion": "cilium.io/v2", "kind": "CiliumNetworkPolicy", "metadata": {"name": "deny-probe", "namespace": "starfabric"},
                    "spec": {"endpointSelector": {"matchLabels": {"app": "sf-probe"}},
                             "egress": [{"toEntities": ["all"]}],
                             "egressDeny": [{"toEndpoints": [{"matchLabels": {"app.kubernetes.io/instance": "single"}}]}]}})
        def blocked() -> bool:
            try:
                self.request("single", "/readyz", host=host)
            except RuntimeError:
                return True
            return False
        wait_for("Cilium policy denial", blocked, 60)
        self.k("-n", "starfabric", "delete", "ciliumnetworkpolicy", "deny-probe")
        wait_for("Cilium policy recovery", lambda: self.request("single", "/readyz", host=host)[0] == 200, 60)
        flows = []
        agents = [p for p in self.get("pods", ns="kube-system")["items"] if p["metadata"].get("labels", {}).get("k8s-app") == "cilium"]
        for agent in agents:
            raw = self.k("-n", "kube-system", "exec", agent["metadata"]["name"], "-c", "cilium-agent", "--",
                         "hubble", "observe", "--server", "unix:///var/run/cilium/hubble.sock", "--since", "10m",
                         "--namespace", "starfabric", "--output", "json")
            flows += [json.loads(line) for line in raw.splitlines() if line.startswith("{")]
        self.save("hubble-flows", flows)
        selected = [v.get("flow", v) for v in flows]
        business = [v for v in selected if v.get("source", {}).get("pod_name") == "probe"
                    and v.get("destination", {}).get("pod_name", "").startswith("single-starfabric-")
                    and v.get("l4", {}).get("TCP", {}).get("destination_port") == 8080]
        forwarded = [v for v in business if v.get("verdict") == "FORWARDED"]
        dropped = [v for v in business if v.get("verdict") == "DROPPED"
                   and v.get("drop_reason_desc") in {"POLICY_DENIED", "POLICY_DENY"}]
        assert forwarded and dropped, {"forwarded": len(forwarded), "dropped": len(dropped)}
        self.checks.update({"cilium_policy_denies_and_recovers": True, "hubble_forwarded_packets": len(forwarded),
                            "hubble_dropped_packets": len(dropped)})

    def cleanup(self) -> None:
        if self.created:
            try:
                if self.kubectl.exists():
                    for resource in ("pods", "events", "deployments", "leases", "services", "endpointslices"):
                        output = self.k("get", resource, "-A", "-o", "json", check=False)
                        (self.artifacts / f"final-{resource}.json").write_text(output)
                    for pod in self.pods("ha") + self.pods("single"):
                        output = self.k("-n", "starfabric", "logs", pod["metadata"]["name"], "--tail=200", check=False)
                        (self.artifacts / f"{pod['metadata']['name']}.log").write_text(output)
            except Exception as error:
                (self.artifacts / "diagnostic-error.txt").write_text(str(error))
            finally:
                self.progress(f"Removing owned cluster {self.name}")
                execute([str(KIND), "delete", "cluster", "--name", self.name, "--kubeconfig", str(self.kubeconfig)], timeout=180)
                self.kubeconfig.unlink(missing_ok=True)
                self.kubectl.unlink(missing_ok=True)
        if self.image_built:
            execute(["docker", "image", "rm", self.image], check=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["bootstrap", "run"])
    parser.add_argument("--workers", type=int, default=2, choices=[0, 1, 2])
    args = parser.parse_args()
    if args.command == "bootstrap":
        bootstrap()
        return
    report: dict[str, Any] = {"schema_version": 1, "success": False, "generated_at": stamp(), "checks": {},
        "scope": "real single-host kind Kubernetes/Cilium/Hubble/Helm/controller Lease runtime",
        "boundary": "Memory device adapter and shared hostPath SIL storage. GitOps/Argo CD and HA rolling upgrade remain separate gates; this does not establish a connected satellite/5G/onboard runtime."}
    cluster = None
    try:
        if not KIND.exists() or not CHART.exists():
            raise RuntimeError("run make bootstrap-cloud-tools first")
        if shutil.disk_usage(ROOT).free < 5 * 1024**3:
            raise RuntimeError("requires at least 5 GiB free before creating the isolated cluster")
        cluster = Cluster(args.workers)
        report["run_id"] = cluster.name
        cluster.build_application()
        cluster.create()
        cluster.prepare_application()
        cluster.single()
        cluster.ha()
        cluster.network()
        report["success"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        if cluster:
            report["checks"] = cluster.checks
            report["evidence"] = cluster.evidence
            report["artifacts"] = str(cluster.artifacts.relative_to(ROOT))
            try:
                cluster.cleanup()
                report["checks"]["owned_cluster_cleaned"] = True
            except Exception as error:
                report["success"] = False
                report["cleanup_error"] = str(error)
        report["generated_at"] = stamp()
        write(REPORT, report)
    print(json.dumps(report, indent=2), flush=True)
    raise SystemExit(0 if report["success"] else 1)


if __name__ == "__main__":
    main()
