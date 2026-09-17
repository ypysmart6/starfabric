# OpenConfig software target closure

`run-closed-loop.sh` starts four local standards targets. Each target serves
gNMI Capabilities/Get/Set/Subscribe, gNOI System, Diagnostics, Certificate
Management, Packet Capture and OS services, plus a gRIBI RIB with FIB
acknowledgements. The management probe executes BERT start/read/stop, exact
X.509 load/readback, bounded Ethernet-frame capture, scheduled reboot/cancel,
and OS transfer/validation/activation/verification as real gRPC exchanges.
The production controller programs a primary satellite path, reacts to a link
metric event, makes a transactional switch to the backup satellite, and is
then restarted to verify durable controller ownership and preserved AFT state.

Run `make test-openconfig`. The machine-readable verdict is written to
`reports/openconfig-closed-loop.json`. This proves the software control/RPC/FIB
contract and lifecycle state machines; it intentionally does not claim a
vendor ASIC, physical interface, vendor image or production trust anchor.
