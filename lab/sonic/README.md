# SONiC/SAI hardware integration contract

This directory defines the two-port Ondatra/OTG contract for a SONiC virtual switch or physical SAI target. Supply a licensed/compatible SONiC image and its management endpoints, then run the same gNMI configuration, gRIBI FIB-ACK and OTG packet assertions in `tests/ondatra`.

The acceptance gate is:

1. gNMI capabilities and OpenConfig interface configuration succeed over mTLS.
2. gRIBI reports FIB-programmed next-hop, next-hop-group and IPv4/IPv6 entries.
3. OTG traffic proves forwarding and make-before-break loss SLOs.
4. Warm reboot retains or reconstructs intended state; actual state is reconciled after restart.
5. SAI/SDK resource limits and hardware counters agree with control-plane state.

No vendor NOS image or ASIC SDK is redistributed. This is therefore a runnable contract when a target is supplied, not a claim that SAI hardware was tested locally.
