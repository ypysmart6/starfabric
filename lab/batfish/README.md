# Batfish static validation gate

Run the complete local gate with:

```bash
make test-batfish
```

The gate starts a digest-pinned Batfish service, uploads a four-node candidate
configuration snapshot, and executes parse-status, undefined-reference,
forwarding-loop, and UDP reachability questions. The verdict is written to
`reports/batfish.json`.

Batfish currently classifies the native integrated FRR files as partial Cisco
syntax, so `model-configs` contains the equivalent vendor-neutral routed
candidate (same interfaces, prefixes, primary and backup next hops). This
static model is combined with the native FRR control-plane/FIB inspection and
OTG packets; it does not falsely claim unsupported FRR grammar coverage.
