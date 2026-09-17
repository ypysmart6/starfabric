# KNE multi-device validation

`starfabric.textproto` creates two OpenConfig Lemming DUTs and one Ixia-c OTG
ATE. It is the scale/multi-vendor-compatible counterpart to the small
containerlab lab.

```bash
kne create lab/kne/starfabric.textproto
cd tests/ondatra
go test -v -testbed ../../lab/kne/testbed.textproto \
  -topology ../../lab/kne/starfabric.textproto -skip_reset
```

KNE must first be installed with the Lemming and IxiaTG controllers. The
topology/testbed shape follows the Apache-2.0 Ondatra `knebind/integration`
reference and the KNE Lemming example.
