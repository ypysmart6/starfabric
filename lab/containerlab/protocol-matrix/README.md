# FRR dual-stack and MPLS closed loop

The default lab is now a four-router diamond rather than a configuration-only
three-node chain. It proves all of the following against actual FRR and Linux
forwarding state:

- OSPFv2 and OSPFv3 reach Full state on both candidate paths;
- IPv4 and IPv6 routes are selected by OSPF and installed in the kernel FIB;
- LDP sessions become operational and advertise a numeric downstream label;
- IS-IS SR-MPLS advertises Prefix-SID 16004 and installs it in the LFIB;
- packet capture observes the expected LDP and SR labels on the wire;
- shutting both ends of the primary link moves both OSPF FIBs to the backup;
- LDP and SR-MPLS packet forwarding is re-established over that backup;
- continuous IPv4/IPv6 traffic remains within the acceptance loss budget;
- the primary path returns after the link is restored.

Linux MPLS is modular on Ubuntu. The harness deliberately does not load host
kernel modules by itself. If `/proc/sys/net/mpls/platform_labels` is absent,
review the host-level change and run once:

```bash
sudo modprobe mpls_router mpls_iptunnel mpls_gso
make test-protocol-live
```

To retain this prerequisite across host reboots, install the dedicated module
list from the repository root once:

```bash
sudo install -m 0644 deploy/host/starfabric-mpls.conf /etc/modules-load.d/starfabric-mpls.conf
```

This enables kernel modules, not a persistent test topology. The experiment
still creates and cleans up its own routers on every invocation.

The retained report is `reports/protocol-matrix-closed-loop.json`. The legacy
`bgpls.reference.conf`, `pcep.reference.conf`, `evpn.reference.conf`, and
`srv6.reference.conf` files are no longer the evidence boundary: their runnable
control-plane, kernel-state, packet, fault, and recovery tests are in
`../advanced-protocols` and run with `make test-advanced-live`.

The base integrated FRR configuration is parsed at daemon startup. LDP is
applied by `configs/apply-ldp.sh` after containerlab has created `eth1` and
`eth2`, avoiding a race between FRR startup and data-interface creation.
