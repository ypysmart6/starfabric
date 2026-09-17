# P4/P4Runtime satellite forwarding extension

`starfabric.p4` is a P4_16 v1model pipeline with IPv4/IPv6 forwarding,
IPv4/IPv6 DSCP classification, TTL/hop-limit protection, a service meter, and
per-route packet/byte counters. `make test-p4` uses a digest-pinned official
`p4c`/BMv2 image and P4Runtime Shell 0.0.6 (P4Runtime 1.4.1) to compile and
install the pipeline, read back all table entries, execute packets through
BMv2 file ports, prove forwarding/drop/header rewrite behavior, and read live
direct counters. The verdict is `reports/p4-closed-loop.json`.

This is a complete software data-plane target. The default production route
path remains gRIBI/OpenConfig or FRR because BMv2 is not flight hardware.
Vendor ASIC/FPGA architecture mapping, SDK, physical ports, warm restart and
hardware resource exhaustion remain correctly excluded as hardware-only work.
