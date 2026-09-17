# Phase 1 FRR + OTG acceptance lab

This product lab runs four FRR routers with IS-IS, 300 ms BFD detection, LFA,
and an iBGP service-prefix overlay. Ixia-c sends bidirectional UDP/GTP-U-shaped
traffic while both endpoints of the primary r1-r2 link are failed. Disabling
both veth endpoints avoids treating a containerlab one-sided carrier state as
a physical bidirectional link failure. `run.sh` measures the route
transition and fails if convergence exceeds 2 s, any flow loses over 6%, or
lost packets exceed a 600 ms outage equivalent at the configured 2500 pps.

The lab requires an `otgen` build using the same OTG API model as the pinned
controller image (1.61). The CLI prints cumulative JSON samples; the grader
uses the final sample.

```bash
GOBIN="$PWD/bin" go install github.com/open-traffic-generator/otgen@v0.7.4-0.20260901151215-a2873adc0f5c
PATH="$PWD/bin:$PATH" make test-live
```

`run-closed-loop.sh` is the P0 product acceptance path. In one process tree it
deploys the lab, starts StarFabric with the real FRR adapter, reconciles two
directional service intents, proves the installed static FIB, starts OTG,
applies a physical and controller topology failure, reconciles to the alternate
path, and requires the plan, actual FIB and packet metrics to agree. Its final
evidence is `artifacts/closed-loop/summary.json`. The local FRR-only baseline
has a 2 s SLO; the controller/FIB/packet transaction has a separate 3 s SLO
because it includes four adapter state reads and synchronous packet probes.
The 6% loss ceiling corresponds to 600 ms over the fixed 10-second flow; the
grader records that duration-independent packet-outage equivalent explicitly.

Outputs are `artifacts/metrics.json`, `artifacts/summary.json`, and, when run as
root with tcpdump installed, `artifacts/failover.pcap`.

The Ixia-c deployment pattern and OTG schema are derived from the MIT-licensed
Open Traffic Generator `otg-examples/clab/ixia-c-te-frr` reference lab.
