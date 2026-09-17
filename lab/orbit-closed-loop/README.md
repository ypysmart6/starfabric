# TLE-to-packet orbital closed loop

This lab joins the previously independent orbit and network pipelines:

1. pinned TLE catalog;
2. SGP4 propagation;
3. CCSDS OEM KVN files;
4. sampled ground visibility, OISL visibility, range, propagation delay and
   first-order carrier Doppler;
5. duplex contact windows and a complete StarFabric contact-plan scenario;
6. selection of a bent-pipe to optical inter-satellite-link handover;
7. shadow validation and pre-contact-end activation by the persistent predictive scheduler;
8. compressed replay into StarFabric's real FRR adapter;
9. physical primary-egress shutdown after pre-programming;
10. actual OISL-path FRR FIB verification and bidirectional Ixia-c packet SLOs.

Run with:

```bash
make test-orbit-live
```

The retained summary is `reports/orbit-network-closed-loop.json`; replay
inputs, both CCSDS OEM files, compiled contacts, controller events, FIB state
and traffic metrics remain under `lab/orbit-closed-loop/artifacts/`.

The orbital timestamp is not falsified or replaced: the report retains it,
while only wall-clock replay is compressed.  This is a software-in-the-loop
network proof, not flight-dynamics truth, RF, or flight-hardware evidence.
