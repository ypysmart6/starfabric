# Single-PC 5G SA acceptance lab

This is the executable 5G baseline for a computer with no radio hardware. It
pins a community Docker integration at commit
`7464234a9f79718df1836d24d688be745a5272d1`, immutable container digests,
Open5GS `v2.8.0-48-gf87da61`, and UERANSIM `v3.2.6`.

```bash
make test-5g
```

The command downloads several gigabytes on its first run, provisions a lab-only
subscriber, starts the 5GC/gNB/UE, and fails unless all of these are observed:

- gNB-to-AMF SCTP/NGAP N2 setup;
- SMF-to-UPF PFCP association;
- UE authentication and registration;
- PDU-session establishment and a `uesimtun0` address;
- UE-to-UPF ping through the PDU session;
- matching uplink and downlink UDP/2152 GTP-U packets on N3.

It then starts StarFabric, reconciles the two-path N3 intent, injects telemetry
changes through the controller, and derives the live UPF `tc netem` delay from
the controller's selected path. The second JSON report must correlate committed
plan IDs, path changes, applied channel delay, decodable GTP-U, and packet SLOs.

JSON evidence is written to `reports/5g-sa-baseline.json` and
`reports/5g-ntn-transport.json`. Containers are
stopped after the run; the lab Mongo volume and pinned images remain for fast
reruns. Set `SF_5G_KEEP=1` to leave the stack running, then use
`ntn/single-pc/down.sh`.

The independent Rel-17 NTN software channel gate is:

```bash
make test-ntn-r17
```

It validates the band n256/SIB19/ECEF ephemeris, common timing advance,
extended timers, PRACH format and HARQ-off profile against the GEO geometry.
It then sends real complex64 IQ blocks through a bounded ZeroMQ channel and
measures propagation delay, Doppler injection, Doppler pre-compensation and
path loss. The channel implementation also supports delay and Doppler rate for
MEO/LEO traces. Evidence is written to `reports/ntn-r17-channel.json`.

Together these gates prove the local 5G SA protocol/packet baseline and the
software NTN channel/profile behavior. UERANSIM is not a commercial NTN UE and
the IQ channel is not over-the-air evidence. Commercial UE conformance and
satellite RF hardware remain explicit external/HIL boundaries; a live srsRAN
Project process remains a separate software gate.
