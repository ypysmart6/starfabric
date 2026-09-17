#!/usr/bin/env bash
set -euo pipefail

: "${STARFABRIC_ARM64_KERNEL:?set STARFABRIC_ARM64_KERNEL to an ARM64 Image}"
: "${STARFABRIC_ARM64_ROOTFS:?set STARFABRIC_ARM64_ROOTFS to a qcow2 root filesystem}"

acceleration="${STARFABRIC_QEMU_ACCEL:-tcg,thread=multi}"

exec qemu-system-aarch64 \
  -machine virt -cpu cortex-a72 -accel "$acceleration" -m 1024 -smp 2 -nographic \
  -kernel "$STARFABRIC_ARM64_KERNEL" \
  -drive "if=virtio,file=$STARFABRIC_ARM64_ROOTFS,format=qcow2" \
  -append "root=/dev/vda2 console=ttyAMA0 rw" \
  -netdev user,id=control,hostfwd=tcp::19080-:9080 \
  -device virtio-net-pci,netdev=control \
  -device virtio-rng-pci
