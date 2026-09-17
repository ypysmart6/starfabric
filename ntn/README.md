# 5G NTN integration

## One-computer executable baseline

`single-pc/` is the first runnable gate. It launches pinned Open5GS and
UERANSIM images, provisions a lab subscriber, proves N2/NGAP, PFCP, PDU-session
setup and bidirectional N3/GTP-U, then applies satellite-like transport delay to
the live user plane:

```bash
make test-5g
```

This is a real 5G SA packet path with an emulated UE/radio. It is the transport
integration baseline, not evidence of Release 17 NTN PHY or real RF behavior.

## srsRAN Release 17 NTN gate

This directory joins—not reimplements—the upstream components:

```text
supported UE / emulator → srsRAN gNB → N2/N3 → StarFabric transport
                                              → Open5GS 5GC → application
```

1. Build srsRAN with ZeroMQ and run the upstream `docker compose ... 5gc`
   Open5GS profile. Apply the matching sections from `open5gs/amf.yaml`,
   `open5gs/smf.yaml`, and `open5gs/upf.yaml`; these pin N2, N3, PFCP, PLMN,
   TAC, S-NSSAI, UE IPv4/IPv6 pools and MTU instead of assuming upstream
   defaults.
2. Run the official GNU Radio GEO channel emulator (or a qualified
   delay/Doppler emulator for LEO).
3. Start the gNB with upstream `gnb_zmq.yml`, then the two overlays here:

   ```bash
   gnb -c gnb_zmq.yml -c ntn/srsran/geo_ntn.yml -c ntn/srsran/gnb_transport.yml
   ```

4. Start a supported NTN UE and ensure its data namespace is `ue1` (or edit an
   experiment manifest).
5. Start StarFabric with `ntn/starfabric-transport.json`, wire its selected
   routes into the N3 transport, then run an experiment:

   ```bash
   sudo python3 ntn/experiment.py --manifest ntn/experiments/01-gateway-switch.json
   ```

The runner grades gateway switching/GTP-U continuity, interruption recovery,
transport delay/loss variation, and two DSCP service classes. It can execute in
host network namespaces or Docker containers. ICMP, TCP and UDP echo probes are
supported; all set the requested six-bit DSCP consistently. Reports include
loss, longest outage and P50/P95/P99/max latency, and can require a PCAP that
actually decodes UDP/2152 rather than merely checking file size.
`--validate-only` validates manifests without requiring root or a UE.

## O-RAN E2 (optional, non-autonomous)

`srsran/oran_e2.yml` enables the srsRAN DU E2 agent, E2SM-KPM telemetry,
E2SM-RC, metrics and E2AP packet capture for a supplied O-RAN SC Near-RT RIC
or FlexRIC testbed:

```bash
gnb -c gnb_zmq.yml -c ntn/srsran/geo_ntn.yml \
  -c ntn/srsran/gnb_transport.yml -c ntn/srsran/oran_e2.yml
```

The acceptance evidence is E2 Setup, a KPM subscription/indication stream,
E2AP PCAP and a reviewed RC action. No learned policy or autonomous xApp is
part of this repository, and E2 never bypasses StarFabric validation,
canarying or rollback.

The included GEO values track the official srsRAN tutorial. That reference
channel is fixed-delay, satellite-gNB, no-feeder-link, and HARQ-disabled; it is
not evidence of an LEO RF-channel implementation. For LEO, feed the generated
range/Doppler profile from `tools/ephemeris_contacts.py` into a qualified
channel emulator or HIL system.
