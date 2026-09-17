# Satellite Node Runtime

This Rust service is the onboard/SIL component of StarFabric. It persists each accepted controller generation, rejects stale updates, activates deterministic fallback routes after a disconnect hold timer, notifies the systemd watchdog, and stages only Ed25519-signed artifacts into the inactive A/B slot.

The management API binds to loopback by default. Put an authenticated mTLS sidecar or a flight-bus adapter in front of it before remote use. `/v1/update/confirm` is intentionally separate from staging: a boot supervisor should confirm only after the candidate passes its health deadline; otherwise it keeps the old active-slot marker.

```bash
cargo test --manifest-path onboard/Cargo.toml
cargo build --release --target aarch64-unknown-linux-gnu --manifest-path onboard/Cargo.toml
```

`qemu/run-arm64-sil.sh` boots a user-supplied ARM64 kernel/root filesystem. The Buildroot and Yocto files are integration fragments rather than redistributable BSPs. Secure Boot key enrollment, bootloader slot selection, switch SDK/SAI, FPGA and HIL interfaces are hardware/BSP responsibilities and are not simulated as completed here.

## Executable single-PC acceptance

`make test-onboard` uses the digest-pinned Rust 1.89 toolchain, runs all Rust
tests, builds the release process, and exercises its real HTTP interface. The
test covers disconnect hold/fallback, reconnect withdrawal, generation replay
protection, fault-to-health propagation, Ed25519/SHA-256 staging, A/B confirm,
process restart recovery, and rollback. Route installation uses
`--dry-run-routes` so the test does not change the workstation FIB.

The result is written to `reports/onboard-runtime.json`.

`make test-qemu` additionally cross-compiles and executes the Rust tests as
ARM64 through QEMU user mode, then boots a checksum-pinned Alpine ARM64 guest on
the QEMU `virt` machine with virtio network and entropy devices. It deliberately
uses TCG so KVM and `/dev/kvm` are not prerequisites. Its report is
`reports/qemu-arm64-sil.json`.
