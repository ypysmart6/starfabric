# StarFabric architecture and invariants

## Components

`Single-PC Acceptance Coordinator` runs the versioned `docs/single-pc-suite.json`
stage graph serially against one host. It records run identity, source hash,
command results, logs and immutable report snapshots. Final acceptance requires
all current-run gates and no open software capabilities. This coordinator is
an acceptance integration. `lab/unified/run.py` connects orbital prediction,
FRR, actual 5G GTP-U, Rust kernel-route autonomy and NOC backends under one run
identity and fault timeline. Its packet, FIB and backend artifacts are archived
with per-file hashes. Document-scale and 24-hour acceptance remain separate
mandatory gates; see `docs/remaining-systems.md`.

`Topology Store` owns the authoritative network snapshot. It accepts idempotent events and increments the snapshot version for each valid mutation. Sequence numbers are monotonic per subject, allowing independent links to report concurrently without creating a false global ordering.

`Planner` applies intents in deterministic priority/ID order to links that are administratively and operationally up, inside their contact window, fresh enough, and within SLA constraints. `demand_bps` is reserved from every primary-path link before the next intent is admitted. Dijkstra is deterministic. Backup computation removes both directions of each primary physical edge, all shared risk groups, and optionally every interior primary node.

`Validator` is the last pure safety gate. It rejects topology-version drift, expiry, unknown or disabled nodes, invalid prefixes, unreachable next hops, duplicate routes, and path loops.

`Reconciler` is a single-writer transaction coordinator. It partitions a RoutePlan by device, checks health, prepares all devices, commits deterministic batches, verifies actual state after every batch, and then performs a final verification. Failure causes reverse-order rollback. Retries are bounded, timed out, and exponentially backed off.

`DeviceAdapter` separates controller semantics from device protocols. `MemoryDevice` is the deterministic digital twin and fault injector. `frr.Device` invokes fixed-argument `vtysh`, then verifies the FRR JSON RIB. `openconfig.Device` uses official gNMI/gNOI/gRIBI clients, TLS/mTLS, FIB acknowledgements and actual AFT reads; it stages next-hop dependencies before switching the prefix and removes stale dependencies afterwards.

`API/CLI/Scenario` expose the same state machine for interactive operations and deterministic regression. Scenario reports record input topology version, seed, event order, plan IDs, durations, rollback count, final topology, and actual device routes.

## State transition

```text
idle
  → validating
  → preparing
  → committing (batch/canary verification)
  → verifying
  → committed

Any pre-commit validation error → failed
Any post-prepare error          → rolled_back
Rollback operation error        → failed (operator intervention required)
```

## Invariants

1. A plan only applies to exactly one topology version and before its expiration.
2. Every programmed next-hop maps to an enabled node joined by a currently usable directed link.
3. A path never repeats a node.
4. A plan is idempotent by ID; adapters treat repeated Prepare and Commit as success.
5. Desired state is only declared committed after actual state matches on every participating device.
6. No observability dependency is in the network-control success path.
7. Persisted files are never modified in place.
8. The embedded store has one active writer. Kubernetes Lease fencing and RWX handoff support small-cluster failover; regional scale requires a transactional replicated store.
9. An optical link enters the route graph only after its acquisition state is `locked` or `degraded`.
10. Observability exporter failure cannot fail a route transaction.
11. A later intent cannot consume primary-path capacity already reserved by a higher-priority intent.
12. A backup path cannot reuse a declared shared-risk group from an earlier path for the same intent.

## Deployment planes

The ground plane runs the Go controller, OpenConfig/FRR adapters, inventory import, validation and NOC pipeline. Kubernetes Lease elects one writer; followers stay live but return not-ready and reject mutations. The mTLS API is separate from the cluster-internal probe/metrics listener.

The onboard plane is the independent Rust runtime under `onboard/`. It persists monotonic generations, enters local fallback after a connectivity hold timer, drives the systemd watchdog, and stages signed artifacts to an inactive slot. It is deliberately deployable without Kubernetes and remains useful while disconnected from the ground plane.

## Trust boundaries

- Scenario, topology and intent inputs are untrusted and strictly validated.
- API mutation endpoints can require a bearer token and production Helm enables native TLS 1.3 client-certificate verification. The plain health/metrics listener is not a general API and must remain cluster-restricted.
- The fault-injection endpoint belongs only on a management/testing listener.
- FRR adapter access grants network configuration authority. Run the controller with the minimum Docker/SSH permissions possible; never expose the Docker socket to the public controller container.
- Secrets do not belong in scenarios, reports, logs, or Helm values committed to source.
