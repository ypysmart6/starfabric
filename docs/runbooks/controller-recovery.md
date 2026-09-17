# Controller recovery runbook

## Symptoms

- `/healthz` fails or the process restarts repeatedly;
- `/api/v1/status` reports `failed` or `rolled_back`;
- `starfabric_desired_actual_mismatch` is non-zero;
- devices retain a previous route-plan ID.

## Safe response

1. Stop automated event ingestion but keep the distributed FRR baseline active.
2. Preserve `data/topology.json`, `data/reconciler.json`, controller logs and device RIB/FIB output.
3. Validate the configured scenario with `sfctl scenario validate`.
4. Restart one controller instance only. The embedded state store is not multi-writer safe.
5. Inspect `sfctl get topology`, `sfctl get status`, and `sfctl get devices`.
6. Run `sfctl reconcile`. A new plan is generated against the current topology version and actual device state is verified.
7. If reconciliation rolls back, do not retry indefinitely. Identify the listed failed device and test its adapter health.

## FRR checks

```bash
docker exec clab-sf-l04-lfa-r1 vtysh -c 'show version'
docker exec clab-sf-l04-lfa-r1 vtysh -c 'show ipv6 route static json'
docker exec clab-sf-l04-lfa-r1 vtysh -c 'show bfd peers json'
docker exec clab-sf-l04-lfa-r1 vtysh -c 'show isis topology'
```

Do not delete unknown static routes. The FRR adapter only removes entries previously managed in its current transaction history. If controller state and device ownership are ambiguous, keep the distributed route baseline and escalate for manual comparison.

## Corrupt local state

Do not overwrite it. Stop the controller, copy the two state files to an incident directory, validate their JSON and restore the most recent known-good backup. Starting with an empty state is authorized only when the operator has first captured and reconciled every target device's actual RIB/FIB.

## Upgrade and downgrade rehearsal

`make test-security` builds distinct `security-v1` and `security-v2` controller
artifacts, records their SHA-256 identities, then runs a live v1 → v2 → v1
sequence against the same durable state. The gate requires v2 to read v1 state,
commit a new topology event, and v1 to read that newer state while preserving
event idempotency. A release is not promoted when this compatibility gate
fails. Production rollout additionally requires the deployment health, canary,
PDB and rollback controls in the cluster runbook.
