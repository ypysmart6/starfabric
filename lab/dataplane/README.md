# High-performance and programmable data-plane gates

The production order is Linux/FRR or OpenConfig/gRIBI first, then measure, then
select the smallest acceleration target that meets the mission budget:

- `xdp_probe.bpf.c`: XDP/eBPF ingress packet accounting for low-overhead SIL
  telemetry. `make test-xdp` compiles it, passes the kernel verifier, attaches
  it in an isolated privileged container network namespace, sends packets and
  asserts the per-CPU map increased. Cilium/Hubble remains the maintained
  eBPF-based Kubernetes observability implementation.
- AF_XDP: use when packets must be delivered to a user-space node runtime
  without adopting a complete poll-mode data plane.
- DPDK: use a pinned `testpmd`/srsRAN DPDK testbed only after CPU/latency
  profiling proves the kernel path insufficient. Acceptance includes NUMA,
  hugepage, queue, loss, latency and fallback tests.
- P4Runtime: `../p4` owns the P4_16 software-target contract; SONiC/SAI and a
  flight ASIC/FPGA remain target-specific gates.

These are alternative target integrations, not four simultaneous forwarding
stacks. None is represented as flight-qualified without the target BSP/NIC/
switch and HIL evidence.
