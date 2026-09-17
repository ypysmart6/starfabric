SHELL := /bin/bash

TOPOLOGY := topology.clab.yml
LAB_NAME := sf-l01-data-plane

.PHONY: up down inspect test shell-h1 shell-r1 shell-h2 capture-r1

up:
	containerlab deploy --topo $(TOPOLOGY)

down:
	containerlab destroy --topo $(TOPOLOGY) --cleanup

inspect:
	containerlab inspect --topo $(TOPOLOGY)

test:
	bash ./verify.sh

shell-h1:
	docker exec -it clab-$(LAB_NAME)-h1 bash

shell-r1:
	docker exec -it clab-$(LAB_NAME)-r1 bash

shell-h2:
	docker exec -it clab-$(LAB_NAME)-h2 bash

capture-r1:
	docker exec -it clab-$(LAB_NAME)-r1 tcpdump -nnevi any 'arp or icmp'
