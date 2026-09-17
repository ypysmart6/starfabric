FROM golang:1.27.1-alpine@sha256:cf6fca6641884b8433441b2b0652976f975e1d0fdd26d177eaaf8596087f3125 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd ./cmd
COPY internal ./internal
COPY gen ./gen
ARG VERSION=dev
RUN CGO_ENABLED=0 go test ./... && \
    CGO_ENABLED=0 go build -buildvcs=false -trimpath -ldflags="-s -w -X main.version=${VERSION}" -o /out/sf-controller ./cmd/sf-controller && \
    CGO_ENABLED=0 go build -buildvcs=false -trimpath -ldflags="-s -w -X main.version=${VERSION}" -o /out/sfctl ./cmd/sfctl && \
    CGO_ENABLED=0 go build -buildvcs=false -trimpath -o /out/sf-inventory ./cmd/sf-inventory && \
    mkdir -p /out/data /out/config && chown -R 65532:65532 /out/data /out/config

FROM scratch
COPY --from=build /out/sf-controller /usr/local/bin/sf-controller
COPY --from=build /out/sfctl /usr/local/bin/sfctl
COPY --from=build /out/sf-inventory /usr/local/bin/sf-inventory
COPY --from=build --chown=65532:65532 /out/data /data
COPY --from=build --chown=65532:65532 /out/config /config
COPY scenarios/leo-resilient.json /config/scenario.json
USER 65532:65532
EXPOSE 8080 8081
ENTRYPOINT ["/usr/local/bin/sf-controller"]
CMD ["--scenario=/config/scenario.json", "--state-dir=/data", "--listen=:8080"]
