SHELL := /bin/bash

TOPOLOGY := topology.clab.yml
LAB_NAME := sf-l04-lfa

.PHONY: up reconfigure down inspect test failover prove-local baseline compare ab route path neighbors database bfd protection capture-bfd

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

prove-local:
	python3 -u ./prove-local-repair.py

baseline:
	$(MAKE) -C ../lesson03-bfd-convergence failover

compare:
	python3 ./compare-results.py

ab: baseline failover compare

route:
	docker exec clab-$(LAB_NAME)-r1 vtysh -c 'show ip route 198.51.100.0/24 json'
	docker exec clab-$(LAB_NAME)-r1 ip route show 198.51.100.0/24

path:
	docker exec clab-$(LAB_NAME)-h1 traceroute -n -m 6 -w 1 -q 1 198.51.100.2

neighbors:
	docker exec clab-$(LAB_NAME)-r1 vtysh -c 'show isis neighbor detail'

database:
	docker exec clab-$(LAB_NAME)-r1 vtysh -c 'show isis database detail'

bfd:
	docker exec clab-$(LAB_NAME)-r1 vtysh -c 'show bfd peers'

protection:
	docker exec clab-$(LAB_NAME)-r1 vtysh -c 'show isis fast-reroute summary level-2'
	docker exec clab-$(LAB_NAME)-r1 vtysh -c 'show isis route level-2 backup'
	docker exec clab-$(LAB_NAME)-r1 vtysh -c 'show ip route 198.51.100.0/24 json'

capture-bfd:
	docker run --rm -it --network container:clab-$(LAB_NAME)-r1 \
		ghcr.io/srl-labs/network-multitool@sha256:9bc1e46dd105a054bce724b6744200e951e8fdf4c17658912fbc54fd2e8a4b06 \
		tcpdump -nnevi eth2 'udp port 3784'
