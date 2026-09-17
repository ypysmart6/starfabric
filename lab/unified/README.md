# Base connected single-host satellite runtime

For the expanded shared protocol/cloud platform, use `make test-unified` or
`make test-platform`; see [the platform guide](../platform/README.md). This
directory supplies reusable FRR/5G/orbit/autonomy/NOC components and retains
the smaller base regression entry described below.

`make test-unified-base` prepares the current Go/Rust binaries, starts the isolated
Open5GS/UERANSIM lab, and connects its actual N3 endpoints through four FRR
routers. Two routers represent satellites and two represent gateways. The
software UE retains its real PDU session throughout the experiment.

The experiment regenerates TLE → SGP4 → CCSDS OEM → contact windows, gives the
resulting scenario a unique run ID, and feeds a contact-end prediction to the
same Go controller that programs the FRR routes. After predictive activation,
the corresponding FRR interfaces are physically disabled inside the lab.
GTP-U packets are captured on both satellite namespaces while continuous UE
traffic crosses the change.

The Rust onboard processes share the satellite network namespaces and run with
real `ip route` operations. A management heartbeat relay only emits heartbeats
while the ground controller answers `/readyz`. When that process stops, the
onboard processes expire their own heartbeat timers without an injected
disconnect message. The test then withdraws a ground-owned route and requires
the kernel lookup and UE packets to use the onboard fallback. Ground recovery
reloads the same durable state, verifies a reconciliation, resumes heartbeats,
and requires the fallback to disappear with working UE traffic.

```bash
make test-unified-base
# Lower-level retry after the current binaries have already been built:
bash ntn/single-pc/run.sh --unified
```

Evidence is written to `reports/unified-runtime.json` and a unique directory in
`lab/unified/artifacts/`: source scenario and orbital selection, predictive and
committed plan IDs, common fault timeline, FRR and kernel FIB snapshots, Rust
state/logs, continuous ping logs, GTP-U PCAPs and controller metrics. Failures
remain failures. The fixture removes its uniquely named routers, captures,
networks and added endpoint routes, then the parent script stops its 5G stack.
The Mongo test volume follows the existing 5G lifecycle and is retained.

The same run starts isolated Prometheus, Loki, Tempo, OpenTelemetry Collector
and Grafana backends. Prometheus scrapes the actual FRR controller and must
observe an outage alert during the injected ground failure. Loki must contain
the same run's plan IDs and fault/recovery events. Tempo must return API spans
under the trace ID propagated by this run's requests. Grafana provisions the
three backends and a dashboard tagged with the run ID. Backend queries and logs
are saved beside the packet and FIB evidence. All listeners bind to loopback;
the uniquely named test volumes are removed during cleanup.

This is a **software integration test**, with a compressed orbital time axis,
local plaintext management probes and a management relay. It does not replace
an external software Release 17 NTN UE/IQ chain, O-RAN/RIC,
full-scale/24-hour acceptance, or hardware evidence. Those remain separate
mandatory entries in the overall acceptance matrix. Continuous traffic in this
fixture is one PDU-session flow; the document's 100-flow budget is not inferred
from it. A successful script alone does not close those entries.
