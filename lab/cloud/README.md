# Single-PC cloud-native gate

This gate uses digest-pinned Helm and kubeconform containers. It renders both
single-replica and HA values, checks Kubernetes schemas and runtime hardening,
and proves unsafe replica, storage, and TLS combinations fail during rendering.
It also checks the Argo CD self-heal/prune contract and the local Compose
observability stack.

```bash
make test-cloud
```

`reports/cloud-native.json` is the render/schema evidence. Real runtime checks
are available through a separate local kind cluster:

```bash
make bootstrap-cloud-tools  # cache checksum-verified kind and the pinned Cilium chart
make test-cloud-runtime     # creates and removes its own three-node cluster
```

The runtime gate uses kind v0.31.0, digest-pinned Kubernetes v1.35.0, and the
Cilium 1.20.1 chart's digest-pinned images. It uses a dedicated kubeconfig under
`.cache/cloud-runtime`, binds the API to loopback, and does not use an existing
cluster. The checks cover Helm deployment, ConfigMap-triggered Pod restart,
single-replica upgrade/rollback, real Kubernetes Lease and RBAC, follower write
rejection, leader deletion with durable state recovery, PDB eviction denial,
three-node anti-affinity, Cilium policy denial/recovery and Hubble packet records.
The report is `reports/cloud-runtime.json`; failed runs produce failed evidence
and retain diagnostics under `lab/cloud/artifacts` before cluster cleanup.

This fixture uses memory device adapters and host directories shared by the
three local kind nodes. It does not validate production distributed storage.
Argo CD reconciliation and HA rolling upgrades remain required separate checks.
In particular, only the leader is Ready: Kubernetes Deployment availability
cannot currently reach the default three-replica rolling-update budget.
`--workers 0`/`--workers 1` are diagnostic modes and cannot satisfy the mandatory
three-node anti-affinity gate. Nested Docker networking requires privilege;
initial images need network access and several GiB of disk space.

Upstream references: [kind quick start](https://kind.sigs.k8s.io/docs/user/quick-start/),
[Cilium on kind](https://docs.cilium.io/en/stable/installation/kind/), and
[Hubble setup](https://docs.cilium.io/en/stable/observability/hubble/setup/).

`make test-observability` separately boots the complete Docker Compose NOC,
commits a real controller transaction, and queries Prometheus, Loki, Tempo and
Grafana for the resulting metrics, structured logs, traces, rules, dashboard,
and provisioned data sources. All host ports bind only to loopback and the test
removes its isolated Compose project and volumes afterward.
