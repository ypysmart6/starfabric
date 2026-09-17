SHELL := /bin/bash

TOPOLOGY := topology.clab.yml
LAB_NAME := sf-l03-bfd

.PHONY: up reconfigure down inspect test failover baseline compare ab route path neighbors database bfd capture-bfd

up:
	containerlab deploy --topo $(TOPOLOGY)
	python3 ./wait-ready.py

reconfigure:
	containerlab deploy --topo $(TOPOLOGY) --reconfigure
	python3 ./wait-ready.py

down:
	containerlab destroy --topo $(TOPOLOGY) --cleanup

inspect:
	containerlab inspect --topo $(TOPOLOGY)

test:
	python3 ./verify.py

failover:
	python3 -u ./measure-failover.py

baseline:
	$(MAKE) -C ../lesson02-isis-control-plane failover

compare:
	python3 ./compare-results.py

ab: baseline failover compare

route:
	docker exec clab-$(LAB_NAME)-r1 vtysh -c 'show ip route 198.51.100.0/24'
	docker exec clab-$(LAB_NAME)-r1 ip route show 198.51.100.0/24

path:
	docker exec clab-$(LAB_NAME)-h1 traceroute -n -m 6 -w 1 -q 1 198.51.100.2

neighbors:
	docker exec clab-$(LAB_NAME)-r1 vtysh -c 'show isis neighbor detail'

database:
	docker exec clab-$(LAB_NAME)-r1 vtysh -c 'show isis database detail'

bfd:
	docker exec clab-$(LAB_NAME)-r1 vtysh -c 'show bfd peers'

capture-bfd:
	docker run --rm -it --network container:clab-$(LAB_NAME)-r1 \
		ghcr.io/srl-labs/network-multitool@sha256:9bc1e46dd105a054bce724b6744200e951e8fdf4c17658912fbc54fd2e8a4b06 \
		tcpdump -nnevi eth2 'udp port 3784'
