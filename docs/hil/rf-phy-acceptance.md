# RF, PHY and antenna integration acceptance

StarFabric does not implement a modem, antenna controller, DSP chain, phased
array calibration algorithm, or beamformer. Those are separate real-time and
hardware products. It consumes their deterministic network-facing result and
turns it into a versioned `Link`:

- identity: link, satellite, gateway/terminal, beam/cell and shared RF or power
  failure domain;
- timing: observation time, valid-until, contact end and clock uncertainty;
- geometry: range and signed Doppler/range-rate input;
- service envelope: usable capacity, latency, packet loss and reliability;
- lifecycle: search/acquisition/locked/degraded/failed state;
- provenance: modem/antenna configuration revision and calibration version.

The detailed modem/antenna adapter may carry vendor fields, but only the
normalized capacity, delay, loss, reliability and validity enter deterministic
routing. Unknown, stale or uncalibrated measurements fail closed rather than
being guessed.

## HIL gates

| Capability from the source roadmap | Required non-simulated evidence |
|---|---|
| Phased-array calibration | Chamber/range procedure, phase/amplitude residuals, temperature sweep, stored calibration revision and invalidation rules |
| Wideband and hybrid beamforming | EIRP/G/T, gain-flatness, beam switching time, sidelobes, EVM and throughput over the declared bandwidth |
| RF/baseband co-design | Link budget tied to measured MODCOD/BLER, scheduler capacity and network packet loss/latency |
| Doppler compensation and timing | Acquisition range, residual frequency/time error and handover behavior over the mission dynamics envelope |
| Modem/PHY/DSP/FPGA interface | Versioned telemetry/control schema, rate limits, timeout/failure semantics, watchdog and safe state |
| Moving cell/beam handover | Beam/cell identity transition, NG-RAN procedure evidence, GTP-U continuity and application SLO |

The test sequence must replay nominal contact, edge-of-coverage, interference,
thermal drift, gateway/beam switch and injected modem/telemetry failure. Retain
raw measurements, calibrated configuration, packet capture, application
metrics, time synchronization evidence and controller correlation IDs.

Calibration and beam selection in this scope use documented deterministic or
vendor-qualified algorithms only. No learned model or generative component is
part of the data path, controller or acceptance decision.
