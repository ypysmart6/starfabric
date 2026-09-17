# Ondatra acceptance suite

The suite validates OpenConfig gNMI configuration/telemetry, OTG discovery,
and gRIBI FIB acknowledgements against the KNE lab. It is isolated as a nested
module because Ondatra/KNE have a large, hardware-lab-oriented dependency tree;
the controller's fast unit-test module remains small.

`go test -c` compiles the contract without reserving a testbed. A normal
`go test` must receive the `-testbed` and KNE binding flags shown in
`lab/kne/README.md`.
