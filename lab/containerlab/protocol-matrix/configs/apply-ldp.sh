#!/bin/sh
set -eu

case "$(hostname)" in
  sat-a) router_id=10.255.0.1 ;;
  sat-b) router_id=10.255.0.2 ;;
  sat-c) router_id=10.255.0.3 ;;
  gw-a) router_id=10.255.0.4 ;;
  *) echo "unsupported protocol-matrix node: $(hostname)" >&2; exit 2 ;;
esac

# The FRR container starts before containerlab has created all data interfaces.
# Apply LDP only after link creation, so discovery is enabled deterministically.
attempt=0
while [ "$attempt" -lt 30 ]; do
  if vtysh \
      -c 'configure terminal' \
      -c 'mpls ldp' \
      -c "router-id $router_id" \
      -c 'address-family ipv4' \
      -c "discovery transport-address $router_id" \
      -c 'interface eth1' \
      -c 'exit' \
      -c 'interface eth2' \
      -c 'end'; then
    exit 0
  fi
  attempt=$((attempt + 1))
  sleep 0.2
done

echo "FRR did not accept LDP configuration after $attempt attempts" >&2
exit 1
