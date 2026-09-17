# NTN QoS and slicing software loop

This gate validates the explicit S-NSSAI → 5QI → DSCP → transport-class policy
against the Open5GS and srsRAN slice declarations. It then sends acknowledged
EF and AF31 UDP traffic through a capability-bounded Linux namespace, proves
the DSCP values from PCAP records, and checks the corresponding HTB class
counters. It proves the software policy/transport boundary; RF scheduler and
commercial-UE conformance remain hardware/test-equipment gates.

Run with `make test-qos`.
