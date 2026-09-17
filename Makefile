SHELL := /bin/bash
GO ?= go
GOCACHE ?= $(CURDIR)/.cache/go-build
GOENV := GOCACHE=$(GOCACHE)
RUST_IMAGE ?= rust:1.89-bookworm@sha256:948f9b08a66e7fe01b03a98ef1c7568292e07ec2e4fe90d88c07bb14563c84ff
GOVULNCHECK ?= $(CURDIR)/.cache/tools/bin/govulncheck
GOVULNCHECK_VERSION ?= v1.8.0
SATELLITES ?= 120
GATEWAYS ?= 4
PLANES ?= 0
FLOWS ?= 0
CONSTELLATION_DIR ?= reports/constellations/leo-$(SATELLITES)-$(GATEWAYS)
CONSTELLATION_CONFIG ?=
PLATFORM_DIR ?= reports/platform-config
PLATFORM_MODEL ?= physical
PHYSICS_CONFIG ?= scenarios/constellations/physical-defaults.json
NETWORK_TOOL_IMAGE ?= ghcr.io/srl-labs/network-multitool@sha256:9bc1e46dd105a054bce724b6744200e951e8fdf4c17658912fbc54fd2e8a4b06
CONSTELLATION_FLAGS = $(if $(CONSTELLATION_CONFIG),--config "$(CONSTELLATION_CONFIG)",--satellites $(SATELLITES) --gateways $(GATEWAYS) --planes $(PLANES) --flows $(FLOWS))

.PHONY: all build test test-race test-e2e test-core-quality bootstrap-security-tools test-dependency-security test-linux test-live test-orbit-live test-protocol-live test-advanced-live test-5g test-ntn-r17 test-qos test-xdp test-openconfig test-p4 test-batfish test-api-contract test-inventory test-ha test-reliability test-security test-cloud test-observability test-onboard test-qemu closure-audit closure acceptance-single-pc test-python coverage api-contract vet fmt check demo clean compose-up compose-down

all: check build

build:
	mkdir -p bin
	$(GOENV) $(GO) build -buildvcs=false -trimpath -o bin/sf-controller ./cmd/sf-controller
	$(GOENV) $(GO) build -buildvcs=false -trimpath -o bin/sfctl ./cmd/sfctl
	$(GOENV) $(GO) build -buildvcs=false -trimpath -o bin/sf-inventory ./cmd/sf-inventory
	$(GOENV) $(GO) build -buildvcs=false -trimpath -o bin/sf-openconfig-emulator ./cmd/sf-openconfig-emulator
	$(GOENV) $(GO) build -buildvcs=false -trimpath -o bin/sf-openconfig-probe ./cmd/sf-openconfig-probe
	CGO_ENABLED=0 $(GOENV) $(GO) build -buildvcs=false -trimpath -o bin/sf-quic-probe ./cmd/sf-quic-probe

test:
	$(GOENV) $(GO) test ./...

test-race:
	$(GOENV) $(GO) test -race ./...

test-e2e:
	mkdir -p reports
	$(GOENV) $(GO) run ./cmd/sfctl experiment run --scenario scenarios/leo-resilient.json --output reports/latest.json --html reports/latest.html

test-core-quality:
	python3 tools/core_acceptance.py

bootstrap-security-tools:
	mkdir -p $(dir $(GOVULNCHECK))
	GOBIN=$(dir $(GOVULNCHECK)) $(GO) install golang.org/x/vuln/cmd/govulncheck@$(GOVULNCHECK_VERSION)

test-dependency-security:
	python3 tools/dependency_security.py --govulncheck $(GOVULNCHECK)

test-linux: build
	python3 lab/linux-networking/validate.py

test-live: build
	PATH="$(CURDIR)/bin:$$PATH" bash lab/containerlab/phase1-frr-otg/run-closed-loop.sh

test-orbit-live: build
	PATH="$(CURDIR)/bin:$$PATH" bash lab/orbit-closed-loop/run-closed-loop.sh

test-protocol-live:
	bash lab/containerlab/protocol-matrix/run-closed-loop.sh

test-advanced-live:
	bash lab/containerlab/advanced-protocols/run-closed-loop.sh

test-5g:
	bash ntn/single-pc/run.sh

.PHONY: test-unified test-unified-base
test-unified: test-platform

test-unified-base: build test-onboard
	bash ntn/single-pc/run.sh --unified

.PHONY: test-platform platform-configuration
platform-configuration: build
	./bin/sfctl constellation generate $(CONSTELLATION_FLAGS) --output "$(PLATFORM_DIR)/seed.json" --summary "$(PLATFORM_DIR)/seed-summary.json"
	$(if $(filter physical,$(PLATFORM_MODEL)),python3 tools/physical_constellation.py --scenario "$(PLATFORM_DIR)/seed.json" --config "$(PHYSICS_CONFIG)" --output "$(PLATFORM_DIR)/scenario.json" --summary "$(PLATFORM_DIR)/topology-summary.json",$(if $(filter logical,$(PLATFORM_MODEL)),cp "$(PLATFORM_DIR)/seed.json" "$(PLATFORM_DIR)/scenario.json",$(error PLATFORM_MODEL must be physical or logical)))
	python3 lab/platform/render.py --scenario "$(PLATFORM_DIR)/scenario.json" --output "$(PLATFORM_DIR)/deployment"

test-platform: build test-onboard platform-configuration
	SF_PLATFORM_SCENARIO="$(abspath $(PLATFORM_DIR))/scenario.json" bash ntn/single-pc/run.sh --platform

.PHONY: test-physical-packets test-physical-runtime
test-physical-runtime: build test-onboard platform-configuration
	SF_PLATFORM_FOCUS=physics SF_PLATFORM_SCENARIO="$(abspath $(PLATFORM_DIR))/scenario.json" bash ntn/single-pc/run.sh --platform

test-physical-packets: platform-configuration
	docker run --rm --network=none --cap-add=NET_ADMIN --cap-add=SYS_ADMIN --security-opt apparmor=unconfined -v "$(CURDIR):/work" -v "$(abspath $(PLATFORM_DIR))/scenario.json:/input.json:ro" --entrypoint python3 $(NETWORK_TOOL_IMAGE) /work/lab/platform/validate_physics_packets.py --scenario /input.json --output /work/reports/physical-packets.json

test-ntn-r17:
	python3 ntn/single-pc/validate_r17_channel.py

.PHONY: test-srsran acceptance-plan acceptance-core acceptance-status
test-srsran:
	python3 lab/srsran/validate.py

test-qos:
	python3 lab/qos/validate.py

test-xdp:
	python3 lab/dataplane/run_xdp.py

test-openconfig: build
	bash lab/openconfig/run-closed-loop.sh

test-p4:
	$(MAKE) -C lab/p4 test

test-batfish:
	bash lab/batfish/run.sh

test-api-contract:
	python3 lab/api/validate_contract.py
	$(GOENV) $(GO) test ./internal/api

test-inventory: build
	python3 lab/inventory/validate.py

test-ha: build
	python3 lab/reliability/ha_closed_loop.py

test-reliability:
	python3 lab/reliability/validate.py

test-security: build
	python3 lab/security/validate.py

test-cloud:
	python3 lab/cloud/validate.py

.PHONY: bootstrap-cloud-tools test-cloud-runtime
bootstrap-cloud-tools:
	python3 lab/cloud/runtime.py bootstrap

test-cloud-runtime:
	python3 lab/cloud/runtime.py run

test-observability:
	python3 lab/cloud/observability.py

.PHONY: onboard-build live-start live-stop live-status test-continuous
onboard-build:
	docker run --rm -v "$(CURDIR)/onboard:/work" -w /work $(RUST_IMAGE) cargo build --locked --release

live-start:
	python3 lab/live/service.py start

live-stop:
	python3 lab/live/service.py stop

live-status:
	python3 lab/live/service.py status

test-continuous:
	python3 -m unittest lab.live.test_live lab.live.test_progress lab.live.test_workloads -v

.PHONY: test-live-workloads
test-live-workloads: build
	python3 lab/live/validate_workloads.py --faults
	$(GOENV) $(GO) test ./internal/topology ./internal/app ./internal/api

test-onboard:
	docker run --rm -v "$(CURDIR)/onboard:/work" -w /work $(RUST_IMAGE) cargo test --locked
	docker run --rm -v "$(CURDIR)/onboard:/work" -w /work $(RUST_IMAGE) cargo build --locked --release
	python3 onboard/run-closed-loop.py

test-qemu:
	python3 onboard/qemu/validate.py

closure-audit:
	python3 tools/verify_single_pc_closure.py

closure:
	python3 tools/single_pc.py audit

acceptance-single-pc:
	python3 tools/single_pc.py run --profile full

acceptance-plan:
	python3 tools/single_pc.py plan

acceptance-core:
	python3 tools/single_pc.py run --profile core

acceptance-status:
	python3 tools/single_pc.py status

test-python:
	python3 -m unittest ntn.test_experiment
	python3 -m unittest ntn.test_lab_lifecycle
	python3 -m unittest tools.test_ephemeris_contacts
	python3 -m unittest tools.test_physical_constellation
	python3 -m unittest tools.test_single_pc
	python3 -m unittest discover -s lab/platform -p 'test_*.py'
	python3 -m py_compile lab/platform/*.py
	python3 -m py_compile lab/unified/*.py
	python3 -m py_compile tools/*.py ntn/*.py ntn/single-pc/*.py onboard/*.py onboard/qemu/*.py lab/api/*.py lab/cloud/*.py lab/inventory/*.py lab/qos/*.py lab/reliability/*.py lab/security/*.py lab/linux-networking/*.py lab/containerlab/phase1-frr-otg/*.py lab/containerlab/protocol-matrix/*.py lab/containerlab/advanced-protocols/*.py lab/orbit-closed-loop/*.py lab/openconfig/*.py lab/batfish/*.py lab/p4/*.py
	for manifest in ntn/experiments/*.json; do python3 ntn/experiment.py --manifest "$$manifest" --validate-only; done
	python3 ntn/experiment.py --manifest ntn/single-pc/ntn-transport.json --validate-only

coverage:
	python3 tools/verify_coverage.py

api-contract: test-api-contract

vet:
	$(GOENV) $(GO) vet ./...

fmt:
	$(GOENV) $(GO) fmt ./...

check: fmt vet test test-python coverage test-e2e test-api-contract

demo: test-e2e

.PHONY: constellation test-constellation constellation-examples
constellation: build
	./bin/sfctl constellation generate $(CONSTELLATION_FLAGS) --output "$(CONSTELLATION_DIR)/scenario.json" --summary "$(CONSTELLATION_DIR)/topology-summary.json"

test-constellation: constellation
	./bin/sfctl experiment run --scenario "$(CONSTELLATION_DIR)/scenario.json" --output "$(CONSTELLATION_DIR)/report.json" --html "$(CONSTELLATION_DIR)/report.html"

constellation-examples: build
	./bin/sfctl constellation generate --config scenarios/constellations/leo-120.config.json --output reports/constellations/leo-120-4/scenario.json --summary reports/constellations/leo-120-4/topology-summary.json
	./bin/sfctl experiment run --scenario reports/constellations/leo-120-4/scenario.json --output reports/constellations/leo-120-4/report.json --html reports/constellations/leo-120-4/report.html
	./bin/sfctl constellation generate --config scenarios/constellations/leo-360.config.json --output reports/constellations/leo-360-8/scenario.json --summary reports/constellations/leo-360-8/topology-summary.json
	./bin/sfctl experiment run --scenario reports/constellations/leo-360-8/scenario.json --output reports/constellations/leo-360-8/report.json --html reports/constellations/leo-360-8/report.html

compose-up:
	docker compose -f deploy/compose/docker-compose.yaml up --build -d

compose-down:
	docker compose -f deploy/compose/docker-compose.yaml down

clean:
	$(GO) clean
