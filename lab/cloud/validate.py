#!/usr/bin/env python3
"""Render, schema-check, and assert the single-PC cloud deployment contracts."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
CHART = "deploy/helm/starfabric"
HELM_IMAGE = os.environ.get(
    "STARFABRIC_HELM_IMAGE",
    "alpine/helm:3.19.0@sha256:aef9b56f64e866207d9591d0abd8f6d767b36aadd12edf68f8a719716d9d29c9",
)
KUBECONFORM_IMAGE = os.environ.get(
    "STARFABRIC_KUBECONFORM_IMAGE",
    "ghcr.io/yannh/kubeconform:v0.7.0@sha256:85dbef6b4b312b99133decc9c6fc9495e9fc5f92293d4ff3b7e1b30f5611823c",
)
REPORT = ROOT / "reports/cloud-native.json"


def docker(
    image: str,
    arguments: list[str],
    stdin: str | None = None,
    network: str = "none",
) -> subprocess.CompletedProcess[str]:
    command = [
        "docker",
        "run",
        "--rm",
        "-i",
        f"--network={network}",
        "-v",
        f"{ROOT}:/work:ro",
        "-w",
        "/work",
        image,
        *arguments,
    ]
    return subprocess.run(command, input=stdin, text=True, capture_output=True, check=False)


def helm(arguments: list[str], *, should_pass: bool = True) -> str:
    result = docker(HELM_IMAGE, arguments)
    if should_pass and result.returncode:
        raise AssertionError(f"Helm failed: {' '.join(arguments)}\n{result.stdout}\n{result.stderr}")
    if not should_pass and result.returncode == 0:
        raise AssertionError(f"unsafe Helm values unexpectedly rendered: {' '.join(arguments)}")
    return result.stdout + result.stderr if not should_pass else result.stdout


def documents(rendered: str) -> list[dict[str, Any]]:
    return [item for item in yaml.safe_load_all(rendered) if isinstance(item, dict)]


def resource(items: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    matches = [item for item in items if item.get("kind") == kind]
    assert len(matches) == 1, f"expected exactly one {kind}, got {len(matches)}"
    return matches[0]


def container(deployment: dict[str, Any]) -> dict[str, Any]:
    containers = deployment["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 1
    return containers[0]


def validate_kubernetes(rendered: str) -> int:
    result = docker(
        KUBECONFORM_IMAGE,
        ["-strict", "-summary", "-ignore-missing-schemas", "-"],
        stdin=rendered,
        network="bridge",
    )
    if result.returncode:
        raise AssertionError(f"kubeconform failed:\n{result.stdout}\n{result.stderr}")
    summary = result.stdout + result.stderr
    match = __import__("re").search(r"Valid:\s*(\d+)", summary)
    if not match or int(match.group(1)) < 5:
        raise AssertionError(f"unexpected kubeconform summary: {summary}")
    return int(match.group(1))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    helm(["lint", CHART])
    default_rendered = helm(["template", "sf", CHART, "--namespace", "starfabric"])
    ha_rendered = helm(
        [
            "template", "sf", CHART, "--namespace", "starfabric",
            "-f", f"{CHART}/values-ha.yaml", "--api-versions", "monitoring.coreos.com/v1",
        ]
    )
    default = documents(default_rendered)
    ha = documents(ha_rendered)

    default_deployment = resource(default, "Deployment")
    assert default_deployment["spec"]["replicas"] == 1
    assert default_deployment["spec"]["strategy"]["type"] == "Recreate"
    assert resource(default, "ServiceAccount")["automountServiceAccountToken"] is False
    checksum = default_deployment["spec"]["template"]["metadata"]["annotations"]["checksum/scenario"]
    changed = documents(helm(["template", "sf", CHART, "--namespace", "starfabric", "--set", "scenario.random_seed=2"]))
    assert checksum != resource(changed, "Deployment")["spec"]["template"]["metadata"]["annotations"]["checksum/scenario"]

    ha_kinds = {item["kind"] for item in ha}
    required = {
        "Deployment", "Service", "ServiceAccount", "PersistentVolumeClaim",
        "PodDisruptionBudget", "Role", "RoleBinding", "CiliumNetworkPolicy", "ServiceMonitor",
    }
    assert required <= ha_kinds, f"HA render missing {sorted(required - ha_kinds)}"
    deployment = resource(ha, "Deployment")
    pod_spec = deployment["spec"]["template"]["spec"]
    app_container = container(deployment)
    arguments = set(app_container["args"])
    assert deployment["spec"]["replicas"] == 3
    assert deployment["spec"]["strategy"]["type"] == "RollingUpdate"
    assert "affinity" in pod_spec and "podAntiAffinity" in pod_spec["affinity"]
    assert resource(ha, "ServiceAccount")["automountServiceAccountToken"] is True
    assert "--leader-election" in arguments
    assert "--tls-client-ca=/tls/ca.crt" in arguments
    assert "--token-file=/secrets/token" in arguments
    assert app_container["securityContext"]["allowPrivilegeEscalation"] is False
    assert app_container["securityContext"]["readOnlyRootFilesystem"] is True
    assert app_container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert app_container["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert app_container["livenessProbe"]["httpGet"]["path"] == "/healthz"
    assert app_container["resources"]["requests"] and app_container["resources"]["limits"]
    pvc = resource(ha, "PersistentVolumeClaim")
    assert pvc["spec"]["accessModes"] == ["ReadWriteMany"]
    role = resource(ha, "Role")
    assert role["rules"][0]["resources"] == ["leases"]
    assert {"get", "create", "update", "patch"} <= set(role["rules"][0]["verbs"])
    assert resource(ha, "PodDisruptionBudget")["spec"]["minAvailable"] == 1

    negative_checks = {
        "multi_replica_without_ha": (
            ["template", "sf", CHART, "--set", "replicaCount=2"],
            "replicaCount must be 1 unless ha.enabled=true",
        ),
        "ha_single_replica": (
            ["template", "sf", CHART, "--set", "ha.enabled=true", "--set", "replicaCount=1", "--set", "persistence.accessModes[0]=ReadWriteMany"],
            "HA requires replicaCount >= 2",
        ),
        "ha_without_rwx": (
            ["template", "sf", CHART, "--set", "ha.enabled=true", "--set", "replicaCount=2"],
            "HA requires a ReadWriteMany persistent volume",
        ),
        "tls_without_secret": (
            ["template", "sf", CHART, "--set", "tls.enabled=true"],
            "tls.existingSecret is required",
        ),
    }
    for name, (arguments, message) in negative_checks.items():
        failure = helm(arguments, should_pass=False)
        assert message in failure, f"{name}: expected error was not emitted"

    valid_default = validate_kubernetes(default_rendered)
    valid_ha = validate_kubernetes(ha_rendered)

    gitops = yaml.safe_load((ROOT / "deploy/gitops/application.yaml").read_text(encoding="utf-8"))
    automated = gitops["spec"]["syncPolicy"]["automated"]
    assert automated == {"prune": True, "selfHeal": True}
    assert gitops["spec"]["source"]["helm"]["valueFiles"] == ["values.yaml", "values-ha.yaml"]
    compose = yaml.safe_load((ROOT / "deploy/compose/docker-compose.yaml").read_text(encoding="utf-8"))
    assert {"controller", "otel-collector", "tempo", "loki", "prometheus", "grafana"} <= set(compose["services"])
    assert compose["services"]["controller"]["read_only"] is True
    assert compose["services"]["controller"]["cap_drop"] == ["ALL"]

    report = {
        "schema_version": 1,
        "success": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "single-PC render/schema/security/HA/GitOps/observability deployment gate",
        "checks": {
            "helm_lint": True,
            "default_resources": len(default),
            "ha_resources": len(ha),
            "kubeconform_default_valid": valid_default,
            "kubeconform_ha_valid": valid_ha,
            "negative_value_guards": len(negative_checks),
            "ha_lease_rbac": True,
            "ha_rwx_handoff": True,
            "mtls_and_token_mounts": True,
            "non_root_read_only_drop_all": True,
            "gitops_prune_and_self_heal": True,
            "compose_observability_services": 5,
        },
        "images": {"helm": HELM_IMAGE, "kubeconform": KUBECONFORM_IMAGE},
        "artifacts": {
            "chart_sha256": sha256(ROOT / CHART / "Chart.yaml"),
            "values_sha256": sha256(ROOT / CHART / "values.yaml"),
            "ha_values_sha256": sha256(ROOT / CHART / "values-ha.yaml"),
        },
        "boundary": "Render/schema checks only. Actual Kubernetes/Cilium/Hubble/Lease evidence is produced separately by make test-cloud-runtime on an isolated local kind cluster.",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, REPORT)
    print(
        f"PASS: Helm default={len(default)} HA={len(ha)} resources; "
        f"kubeconform valid={valid_default}+{valid_ha}; negative guards={len(negative_checks)}"
    )


if __name__ == "__main__":
    main()
