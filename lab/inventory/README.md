# Source-of-truth closed loop

`validate.py` starts a loopback-only HTTP service with the common NetBox and
Nautobot DCIM response shape, then executes the compiled `sf-inventory` process.
It proves authenticated pagination, tag filtering, device/IPAM labels, cable to
bidirectional-link conversion, shared-risk metadata, atomic private output, and
cross-origin pagination rejection. Dynamic telemetry is intentionally not
written back to the source of truth.

Run with `make test-inventory`.
