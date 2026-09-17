#!/usr/bin/env bash

set -Eeuo pipefail

readonly R1="clab-sf-l02-isis-r1"
readonly DEADLINE_SECONDS=60
deadline=$((SECONDS + DEADLINE_SECONDS))

while (( SECONDS < deadline )); do
  neighbors="$(docker exec "${R1}" vtysh -c 'show isis neighbor' 2>/dev/null || true)"
  route="$(docker exec "${R1}" vtysh -c 'show ip route 198.51.100.0/24 json' 2>/dev/null || true)"

  if grep -q 'r2' <<<"${neighbors}" && \
     grep -q 'r3' <<<"${neighbors}" && \
     grep -q '"installed":true' <<<"${route}"; then
    echo "PASS: IS-IS adjacencies, SPF and FIB installation are ready"
    exit 0
  fi
  sleep 1
done

echo "FAIL: IS-IS did not install the end-to-end route within ${DEADLINE_SECONDS}s" >&2
docker logs "${R1}" >&2 || true
exit 1
