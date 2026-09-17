# Production-readiness matrix

The repository delivers a production-style controller core and executable software-in-the-loop environment. It deliberately does not claim flight qualification or hardware-vendor certification.

| Area | Implemented | External gate before operational deployment |
|---|---|---|
| Dynamic topology | Version, validity, dedupe, per-subject order, atomic persistence, OEM/TLE contact inputs | Mission ephemeris/telemetry adapter and clock-sync error budget |
| Path/TE | Deterministic constrained path, priority-ordered bandwidth reservation, link/node/SRLG-disjoint backup, gateway selection, predictive shadow plan | Multi-commodity optimization only if mission scale requires it; policy approval |
| Safety | Expiry, loop, next-hop, topology version, desired/actual checks; Batfish gate supplied | Batfish/vendor validation run against the target NOS version |
| Rollout | Prepare, bounded retry, batches, verify, rollback | Device-native transaction semantics and failure-domain canary policy |
| Devices | Digital twin, FRR adapter, official gNMI/gNOI/gRIBI adapter with FIB ACK/actual Get | OpenConfig/FRR/SONiC conformance on production hardware |
| API security | Strict JSON, size limit, bearer token, TLS 1.3 mTLS, audit log, isolated health port | Identity provider, certificate issuance/rotation and fault API authorization |
| Persistence / HA | Atomic files, restart loading, Kubernetes Lease fencing and takeover reload | Transactional replicated database, backups and schema migration for regional scale |
| Observability | Structured logs, Prometheus, OTLP traces, Collector, Loki, Tempo, Grafana | Alert routing, retention, privacy and SLO calibration |
| Deployment | Scratch/non-root/read-only image, Compose, Helm, PDB, Lease RBAC, Cilium policy, Argo CD contract | Signed images/SBOM, actual cluster/storage/failover rehearsal |
| Testing | Unit/race/API/e2e, chaos, 1000-satellite smoke, Rust runtime test, plus a live controller/FRR/OTG FIB-and-packet closure | 24-hour soak and KNE/Ondatra/target-NOS execution |
| NTN | Pinned Open5GS/UERANSIM N2/PFCP/PDU/N3 packet baseline, live GTP-U transport-delay experiment, srsRAN NTN and O-RAN overlays | srsRAN NTN UE/channel, variable LEO delay/Doppler, commercial UE and 3GPP/O-RAN conformance evidence |
| Onboard | Rust autonomy/watchdog/state/signed A/B update, ARM64/QEMU/Yocto/Buildroot entry points | Mission BSP, secure boot, bootloader rollback, modem/OISL/SAI/FPGA HIL |

## Release gate

A production candidate must pass all of the following on a fixed testbed:

- no persistent blackhole after every declared single failure;
- route-plan programming and topology-to-forwarding latency within the declared SLO;
- controller restart reconstructs actual state and safely reconciles drift;
- stale and out-of-order telemetry cannot activate expired links;
- every partial device failure proves rollback with packet evidence;
- 24-hour link-flap/telemetry-chaos test has zero unbounded retry loops and zero leaked goroutines;
- backup/restore, upgrade, downgrade and certificate rotation runbooks are rehearsed;
- target FRR/NOS version is pinned and its adapter conformance suite passes.
