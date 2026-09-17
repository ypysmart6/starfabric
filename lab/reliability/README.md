# Reliability and HA closed loop

`ha_closed_loop.py` starts two real `sf-controller` processes and a loopback
Kubernetes Lease API. It verifies bearer-authenticated acquisition and renewal,
single-writer fencing, follower mutation rejection, leader termination, timed
takeover, durable-state reload, and a committed reconciliation by the new
leader. Loopback HTTP is accepted only for this test path; non-loopback
Kubernetes endpoints require HTTPS and a CA bundle.

Run with `make test-ha`.

`validate.py` runs the named chaos, rollback, restart, stale-event, node/link
failure, failure-domain sharding and 1,000-satellite scale gates and records the
measured one-plan benchmark. Run it with `make test-reliability`.
