#!/usr/bin/env bash

set -Eeuo pipefail

readonly H1="clab-sf-l01-data-plane-h1"
readonly R1="clab-sf-l01-data-plane-r1"
readonly H2="clab-sf-l01-data-plane-h2"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

for node in "${H1}" "${R1}" "${H2}"; do
  state="$(docker inspect --format '{{.State.Status}}' "${node}" 2>/dev/null)" \
    || fail "${node} does not exist; run 'make up' first"
  [[ "${state}" == "running" ]] || fail "${node} is ${state}, not running"
done

[[ "$(docker exec "${R1}" sysctl -n net.ipv4.ip_forward)" == "1" ]] \
  || fail "IPv4 forwarding is disabled on r1"

h1_route="$(docker exec "${H1}" ip route get 10.0.2.2)"
grep -Fq "via 10.0.1.1 dev eth1" <<<"${h1_route}" \
  || fail "h1 does not select r1 as the next hop for 10.0.2.2"

h2_route="$(docker exec "${H2}" ip route get 10.0.1.2)"
grep -Fq "via 10.0.2.1 dev eth1" <<<"${h2_route}" \
  || fail "h2 does not select r1 as the next hop for 10.0.1.2"

docker exec "${H1}" ping -c 3 -W 1 10.0.2.2 >/dev/null \
  || fail "h1 cannot reach h2"
docker exec "${H2}" ping -c 3 -W 1 10.0.1.2 >/dev/null \
  || fail "h2 cannot reach h1"

docker exec "${H1}" ip neigh show 10.0.1.1 | grep -Eq \
  'lladdr .+ (REACHABLE|STALE|DELAY|PROBE)' \
  || fail "h1 has no usable neighbor entry for r1"

echo "PASS: all three containers are running"
echo "PASS: r1 has IPv4 forwarding enabled"
echo "PASS: forward and return route lookups select r1"
echo "PASS: h1 and h2 have bidirectional data-plane reachability"
echo "PASS: h1 resolved r1's link-layer address"
