# Linux satellite-node networking foundation

This root-only lab exercises the protocol substrate that the controller and
FRR/OpenConfig adapters depend on: Ethernet/veth, ARP, IPv6 ND, IPv4/IPv6,
VRF, RIB/FIB lookup, MTU, ICMP, TCP, UDP, network namespaces, `tc netem`,
packet capture and source NAT.

```bash
sudo ./run.sh
```

The script owns three explicitly named namespaces, removes them on exit, and
writes a PCAP only when `tcpdump` is installed. It is an integration lab, not
a high-throughput benchmark. QUIC uses UDP but requires a real QUIC endpoint;
run HTTP/3 or a mission transport implementation over this same topology and
retain the handshake/transfer PCAP as its acceptance artifact.

`make test-linux` runs both labs inside a read-only, network-isolated container
with only `NET_ADMIN` and `SYS_ADMIN`. It does not use Docker `--privileged` or
the host network namespace. The gate retains packet evidence and writes
`reports/linux-networking.json`.

`sudo ./l2_loop_prevention.sh` is a separate Ethernet-switching integration
lab. It builds two Linux bridges with redundant links, enables deterministic
STP loop prevention, verifies traffic and learned FDB entries, and proves that
one redundant port is non-forwarding. Linux bridge STP is the portable baseline;
an RSTP-capable NOS or `mstpd` target must repeat the same checks and retain its
sub-second convergence evidence before being marked production-ready.
