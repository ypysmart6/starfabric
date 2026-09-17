# gNOI maintenance runbook

The controller uses gNOI `Ping` and `Time` as read-only health checks. It does
not invoke disruptive gNOI RPCs from the reconciliation loop. Diagnostics,
certificate rotation, software installation, packet capture, and reboot are
separate, operator-approved maintenance workflows.

## Mandatory controls

Before a maintenance RPC:

1. Record the target, change ticket, operator, intended RPC, maintenance window,
   and rollback owner.
2. Verify the target with gNMI Capabilities, gNOI Time, and gNOI Ping over mTLS.
3. Check the target's advertised gNOI service/version and authorize the exact
   RPC rather than granting a controller-wide maintenance role.
4. Capture pre-change configuration, software version, alarms, active route
   state, and traffic baseline. Keep secrets and certificate private keys out
   of logs and command history.
5. Start with one canary target. Set an explicit deadline and preserve the RPC
   response, device audit record, and correlation ID.

## Operation-specific gates

| Operation | Required gate | Rollback or recovery |
|---|---|---|
| Diagnostics/BERT | Confirm the test cannot disrupt a live link; reserve the link if it can | Stop the test and restore the port/service state |
| Packet capture | Limit interface, direction, duration, and byte count; protect captured user data | Cancel the capture and securely expire the artifact |
| Certificate rotate | Install and validate the new trust chain before revoking the old one | Retain an overlap window and restore the previous certificate |
| Software install/activate | Verify image signature, digest, compatibility, free space, and redundant peer health | Boot the known-good image or use the platform rollback RPC |
| Reboot | Drain traffic, prove path redundancy, and confirm console/out-of-band access | Cancel a scheduled reboot when possible; use out-of-band recovery otherwise |

## Completion evidence

After the RPC, repeat health, configuration, route/FIB, alarm, and traffic
checks. Close the change only when service objectives have recovered and the
audit record contains timestamps, RPC status, target version, before/after
evidence, and any rollback action.

`make test-openconfig` is the executable B-level software acceptance gate. It
runs the Ping/Time, scheduled reboot/cancel, BERT, X.509 load/readback, bounded
packet-capture, and OS transfer/validate/activate/verify lifecycles against a
local standards service and records `reports/openconfig-closed-loop.json`.
It does not claim that a particular vendor target supports every gNOI RPC or
that emulated diagnostics/software activation proves physical hardware.
