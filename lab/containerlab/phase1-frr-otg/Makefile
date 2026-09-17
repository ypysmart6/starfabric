.PHONY: up test down

up:
	containerlab deploy --reconfigure --topo topology.clab.yml

test:
	./run.sh

down:
	containerlab destroy --cleanup --topo topology.clab.yml
