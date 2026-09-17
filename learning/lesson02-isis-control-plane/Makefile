SHELL := /bin/bash

TOPOLOGY := topology.clab.yml
LAB_NAME := sf-l02-isis

.PHONY: up reconfigure down inspect test failover route path neighbors database capture-isis

up:
	containerlab deploy --topo $(TOPOLOGY)
	bash ./wait-ready.sh

reconfigure:
	containerlab deploy --topo $(TOPOLOGY) --reconfigure
	bash ./wait-ready.sh

down:
	containerlab destroy --topo $(TOPOLOGY) --cleanup

inspect:
	containerlab inspect --topo $(TOPOLOGY)

test:
	python3 ./verify.py

failover:
	python3 -u ./measure-failover.py

route:
	docker exec clab-$(LAB_NAME)-r1 vtysh -c 'show ip route 198.51.100.0/24'
	docker exec clab-$(LAB_NAME)-r1 ip route show 198.51.100.0/24

path:
	docker exec clab-$(LAB_NAME)-h1 traceroute -n -m 6 -w 1 -q 1 198.51.100.2

neighbors:
	docker exec clab-$(LAB_NAME)-r1 vtysh -c 'show isis neighbor detail'

database:
	docker exec clab-$(LAB_NAME)-r1 vtysh -c 'show isis database detail'

capture-isis:
	docker run --rm -it --network container:clab-$(LAB_NAME)-r1 \
		ghcr.io/srl-labs/network-multitool@sha256:9bc1e46dd105a054bce724b6744200e951e8fdf4c17658912fbc54fd2e8a4b06 \
		tcpdump -nnevi eth2 'llc or iso'
