FROM ghcr.io/herlesupreeth/docker_ueransim@sha256:dd94add6d0b2ab7a59a5564bd6d50b44c80afa0a64fe958f344fccaae5b4eda8 AS builder
RUN apt-get update && apt-get install -y --no-install-recommends g++ make cmake patch libssl-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY . .
RUN patch -p1 < starfabric-rlf.patch && cmake -S . -B out -DCMAKE_BUILD_TYPE=Release \
    && cmake --build out --target nr-gnb -j 4 \
    && install -D out/nr-gnb /built/nr-gnb && rm -rf out

FROM ghcr.io/herlesupreeth/docker_ueransim@sha256:dd94add6d0b2ab7a59a5564bd6d50b44c80afa0a64fe958f344fccaae5b4eda8
COPY --from=builder /built/nr-gnb /UERANSIM/build/nr-gnb
# Include the exact patched GPL-3.0 source and upstream license with the binary.
COPY --from=builder /src /usr/src/ueransim
