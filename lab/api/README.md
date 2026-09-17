# API contract gate

`validate_contract.py` compiles both Protobuf files into a descriptor, resolves
every local OpenAPI reference, and requires the HTTP method/path set in
`internal/api/server.go` to exactly equal the OpenAPI surface. It also verifies
the checked-in generated Go messages/client/server and runs the generated v1
client through all 13 RPCs against the real gRPC server over an in-memory
transport. Go API tests execute every documented REST route as well.

Run from the repository root:

```bash
make test-api-contract
```

The machine-readable result is `reports/api-contract.json`.

The controller exposes the same state machine on gRPC when started with
`--grpc-listen HOST:PORT`. Bearer authentication uses the `authorization`
metadata field; configured HTTP TLS/mTLS credentials are also applied to the
gRPC listener.
