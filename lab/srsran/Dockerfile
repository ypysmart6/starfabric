FROM rust:1.89-bookworm@sha256:948f9b08a66e7fe01b03a98ef1c7568292e07ec2e4fe90d88c07bb14563c84ff AS builder

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      cmake ninja-build pkg-config libfftw3-dev libmbedtls-dev libsctp-dev \
      libyaml-cpp-dev libzmq3-dev && \
    rm -rf /var/lib/apt/lists/*

COPY . /src
RUN cmake -S /src -B /build -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DBUILD_TESTING=OFF \
      -DENABLE_EXPORT=ON \
      -DENABLE_ZEROMQ=ON \
      -DENABLE_UHD=OFF \
      -DENABLE_DPDK=OFF \
      -DMARCH=native && \
    cmake --build /build --target gnb --parallel 4 && \
    strip /build/apps/gnb/gnb

FROM rust:1.89-bookworm@sha256:948f9b08a66e7fe01b03a98ef1c7568292e07ec2e4fe90d88c07bb14563c84ff

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      libfftw3-single3 libmbedtls14 libsctp1 libyaml-cpp0.7 libzmq5 && \
    rm -rf /var/lib/apt/lists/*

ARG SRSRAN_COMMIT=unknown
LABEL org.opencontainers.image.source="https://github.com/srsran/srsRAN_Project" \
      org.opencontainers.image.revision="${SRSRAN_COMMIT}" \
      org.opencontainers.image.version="25.10.0"

COPY --from=builder /build/apps/gnb/gnb /usr/local/bin/srsran-gnb
ENTRYPOINT ["/usr/local/bin/srsran-gnb"]
