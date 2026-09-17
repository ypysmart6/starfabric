FROM rust:1.89-bookworm@sha256:948f9b08a66e7fe01b03a98ef1c7568292e07ec2e4fe90d88c07bb14563c84ff

ARG ALPINE_ISO_URL=https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/aarch64/alpine-virt-3.24.1-aarch64.iso
ARG ALPINE_ISO_SHA256=c81699152db11d2a6dbb7d75348d632fcf5811eff414d7e71876a8bb6d48bc02

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl gcc-aarch64-linux-gnu libc6-dev-arm64-cross \
       qemu-efi-aarch64 qemu-system-arm qemu-user-static \
    && rm -rf /var/lib/apt/lists/* \
    && rustup target add aarch64-unknown-linux-gnu \
    && curl -fsSL "$ALPINE_ISO_URL" -o /opt/alpine-virt-aarch64.iso \
    && echo "$ALPINE_ISO_SHA256  /opt/alpine-virt-aarch64.iso" | sha256sum -c -

WORKDIR /work
