P4C_IMAGE ?= p4lang/p4c@sha256:cce724714a077ed6b476a2a66ca6906fa19dce4d8b385be91e61dfa359477bd3
P4RUNTIME_PYTHON ?= ../../.cache/p4runtime-venv/bin/python

.PHONY: build program test clean
build:
	mkdir -p build
	docker run --rm -v $(CURDIR):/work -w /work $(P4C_IMAGE) \
		p4c-bm2-ss --p4v 16 --p4runtime-files build/starfabric.p4info.txtpb \
		-o build/starfabric.json starfabric.p4

program:
	$(P4RUNTIME_PYTHON) program.py

test:
	bash run-closed-loop.sh

clean:
	rm -f build/starfabric.json build/starfabric.p4info.txtpb
