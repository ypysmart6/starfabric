# Security and operations closed loop

`validate.py` launches the real controller with a short-lived local CA and
requires both a trusted client certificate and a bearer token. It verifies TLS
1.3, rejects missing/untrusted client certificates and missing/wrong tokens,
checks that the plaintext health listener exposes no control API, and confirms
request-ID audit correlation without credential leakage.

The same run persists a topology mutation, stops the controller, copies and
hash-verifies its durable state as an offline backup, restores it into an empty
state directory, restarts the controller, and proves both the topology state and
event-idempotency record survived.

This is a single-computer software acceptance test. Enterprise PKI/identity,
off-host encrypted backup storage, and organizational recovery approval remain
deployment controls.

Run it with:

```bash
make test-security
```
