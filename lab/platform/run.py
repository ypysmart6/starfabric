#!/usr/bin/env python3
"""One run identity, live FRR constellation, cloud controller and 5G PDU session."""
from __future__ import annotations

import argparse
import threading
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "lab/unified"), str(ROOT / "lab/cloud")]
import run as unified
from cloud import Cloud
from protocols import Protocols
from resources import HostBudget
from constellation_runtime import ConstellationRuntime


class Platform(ConstellationRuntime):
    def __init__(self, input_scenario):
        super().__init__()
        self.input_scenario = input_scenario
        self.resource_lock = threading.Lock()
        self.cloud = Cloud(self)
        self.protocols = Protocols(self)
        self.host_budget = HostBudget(self.art)
        self.business_duration = 7200
        self.frr_config_dir = ROOT / "lab/containerlab/protocol-matrix/configs"
        self.frr_extra_args = ["--sysctl", "net.ipv6.conf.all.forwarding=1", "--sysctl", "net.ipv6.conf.all.seg6_enabled=1",
                              "--sysctl", "net.ipv6.conf.default.seg6_enabled=1", "--sysctl", "net.mpls.platform_labels=1048575",
                              "--sysctl", "net.mpls.conf.lo.input=1"]

    def start_controller(self):
        self.cloud.start()

    def stop_controller(self):
        self.cloud.stop()

    def pdu_identity(self):
        info = json.loads(unified.command("docker", "inspect", "nr_ue").stdout)[0]
        log = unified.command("docker", "logs", "nr_ue")
        return {"container_id": info["Id"], "pdu_establishments": (log.stdout + log.stderr).count("PDU Session establishment is successful"),
                "tun_addresses": json.loads(self.exec("nr_ue", "ip", "-j", "address", "show", "uesimtun0"))[0]["addr_info"]}

    def route_n3(self):
        self.initial_pdu = self.pdu_identity()
        assert self.initial_pdu["pdu_establishments"] > 0
        unified.save(self.art / "pdu-identity-initial.json", self.initial_pdu)
        super().route_n3()

    def capture_evidence(self):
        final_pdu = self.pdu_identity()
        assert final_pdu == self.initial_pdu, (self.initial_pdu, final_pdu)
        unified.save(self.art / "pdu-identity-final.json", final_pdu)
        self.checks['same_pdu_session_across_runtime'] = True
        if getattr(self, 'focus', 'full') == 'full':
            self.checks["same_pdu_session_across_all_protocols_and_cloud_faults"] = True
        # End only this run's long-lived UE ping, leaving the PDU session alive.
        if self.continuous and self.continuous.poll() is None:
            unified.command("docker", "exec", "nr_ue", "pkill", "-INT", "-f", "^ping -n -D -I uesimtun0 -i 0.1 -w 7200 ")
        super().capture_evidence()

    def cleanup(self):
        if self.continuous and self.continuous.poll() is None:
            unified.command("docker", "exec", "nr_ue", "pkill", "-INT", "-f", "^ping -n -D -I uesimtun0 -i 0.1 -w 7200 ", check=False)
        return super().cleanup()


def source_hashes(runtime):
    code_paths = [ROOT / name for name in ("Dockerfile", "Makefile", "go.mod", "go.sum",
                  "onboard/Cargo.toml", "onboard/Cargo.lock", "onboard/config.example.json")]
    code_paths.extend(runtime.frr_config_dir / name for name in ("daemons", "vtysh.conf"))
    for directory, pattern in (("cmd", "*.go"), ("internal", "*.go"), ("gen", "*.go"),
                               ("onboard/src", "*.rs"), ("ntn", "*.py"), ("tools", "*.py"), ("lab/platform", "*.py"),
                               ("lab/unified", "*.py"), ("lab/cloud", "*.py"), ("deploy/helm/starfabric", "*.yaml"),
                               ("ntn/single-pc", "*.sh"), ("ntn/single-pc", "*.py"),
                               ("ntn/single-pc", "*.yaml"), ("ntn/single-pc", "*.env")):
        code_paths.extend((ROOT / directory).rglob(pattern))
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(code_paths)}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default=os.environ.get("SF_PLATFORM_SCENARIO"), type=Path)
    parser.add_argument("--focus", choices=['full', 'physics'], default=os.environ.get('SF_PLATFORM_FOCUS', 'full'),
                        help='physics runs the physical/5G/onboard pipeline; full also exercises every carrier and cloud fault')
    args = parser.parse_args()
    if not args.scenario or not args.scenario.is_file():
        parser.error("supply a generated constellation --scenario or use make test-unified SATELLITES=... GATEWAYS=...")
    runtime = Platform(args.scenario)
    runtime.focus = args.focus
    report = {"schema_version": 1, "success": False, "run_id": runtime.identifier,
              "suite_run_id": os.environ.get("SF_RUN_ID"),
              "scope": "one live satellite/5G runtime with Kubernetes FRR controller and protocol profiles",
              "boundary": "Single-host kind/hostPath and bounded SR PCE. Physical mode uses per-satellite propagation, geographic gateways and packet shaping; protocol regressions hold the initial snapshot before a 1:1 physical replay.",
              "kubernetes_nodes": 3,
              "focus": args.focus,
              "physical_runtime_integration": False,
              "full_protocol_integration": False}
    if args.focus == 'physics':
        report['scope'] = 'all configured physical satellites/gateways, cloud FRR controller, same 5G PDU, onboard recovery and NOC; carrier/cloud fault matrix excluded'
    report["source_sha256"] = source_hashes(runtime)
    try:
        runtime.host_budget.prepare()
        runtime.fabric_cpus = runtime.host_budget.data_cpu_set
        runtime.compile_orbit()
        report['physical_model'] = bool(runtime.fabric.physical_model)
        if args.focus == 'physics' and not runtime.fabric.physical_model:
            raise ValueError('physics focus requires a compiled physical model')
        report['input_scenario_sha256'] = hashlib.sha256(args.scenario.read_bytes()).hexdigest()
        report.update(satellites=len(runtime.fabric.satellites), gateways=len(runtime.fabric.gateways), frr_nodes=len(runtime.fabric.nodes),
                      configured_gateway_intents=len(runtime.fabric.gateway_intents), input_scenario=str(args.scenario))
        runtime.check_resources()
        # Install/build the cloud before starting hundreds of routing daemons:
        # image decompression must not starve their adjacency hold timers.
        runtime.cloud.prepare()
        runtime.network()
        runtime.protocols.setup()
        runtime.fleet_probe("initial")
        runtime.noc.start()
        runtime.host_budget.limit_containers(unified.command('docker','ps','--filter','label=starfabric.run='+runtime.identifier,
                                                             '--format','{{.Names}}').stdout.splitlines())
        runtime.start_controller()
        initial = runtime.reconcile("initial-commit")
        operations = [json.loads(line) for line in (runtime.art / "frr-agent.jsonl").read_text().splitlines()]
        changed = {op["node"] for op in operations if op["program"] == "vtysh" and op["exit_code"] == 0
                   and any(arg.startswith("ip route ") for arg in op["args"])}
        expected = {route["device"] for route in initial["plan"]["routes"]}
        assert changed == expected and changed, (changed, expected)
        runtime.checks["kubernetes_controls_same_frr_constellation"] = True
        runtime.prepare_paths(initial)
        runtime.gateway_probe("initial")
        runtime.route_n3()
        runtime.ping("cloud-controlled-initial")
        if args.focus == 'full':
            runtime.protocols.exercise()
            runtime.cloud.failover()
            runtime.cloud.policy()
        if runtime.fabric.physical_model:
            runtime.orbit_handover()
            runtime.start_onboard()
        else:
            runtime.start_onboard()
            runtime.orbit_handover()
        runtime.autonomy()
        runtime.fleet_probe("after-faults")
        runtime.gateway_probe("after-faults")
        runtime.capture_evidence()
        runtime.noc.verify()
        required = [name + "_same_gtpu_business_and_fault_recovery" for name in ("ospf", "ldp", "sr-mpls", "pcep", "srv6", "evpn")]
        required += ["bgpls_live_contact_withdrawal_and_restoration", "kubernetes_controls_same_frr_constellation",
                     "helm_deploys_real_frr_adapter", "lease_failover_preserves_real_frr_and_5g", "cilium_policy_on_same_controller",
                     "hubble_same_controller_forwarded", "hubble_same_controller_policy_dropped", "noc_receives_actual_controller_trace",
                     "all_configured_nodes_deployed", "all_configured_links_have_live_ospf_neighbors",
                     "every_configured_node_passes_real_packet_probe", "onboard_runtime_on_every_configured_satellite",
                     "configured_gateway_intents_carry_real_packets", "sr_lfib_matches_current_constellation_paths"]
        if args.focus == 'physics':
            required = ['kubernetes_controls_same_frr_constellation', 'helm_deploys_real_frr_adapter',
                        'all_configured_nodes_deployed', 'all_configured_links_have_live_ospf_neighbors',
                        'every_configured_node_passes_real_packet_probe', 'configured_gateway_intents_carry_real_packets',
                        'onboard_runtime_on_every_configured_satellite', 'gtpu_survives_ground_route_withdrawal',
                        'ground_reconnect_withdraws_fallback', 'noc_receives_actual_controller_trace',
                        'same_pdu_session_across_runtime', 'continuous_gtpu_business_across_faults',
                        'satellite_bidirectional_gtpu_capture']
        if runtime.fabric.physical_model:
            required += ['per_satellite_orbits_drive_contacts','geographic_gateway_visibility',
                         'computed_physical_parameters_in_kernel','physical_replay_on_same_frr_and_5g']
        assert all(runtime.checks.get(name) for name in required), "missing integrated acceptance checks"
        report["full_protocol_integration"] = args.focus == 'full'
        report['physical_runtime_integration'] = bool(runtime.fabric.physical_model)
        report["success"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        radio_logs = {}
        for name in ['nr_gnb', 'nr_ue']:
            result = unified.command('docker','logs','--tail','100',name,check=False)
            radio_logs[name] = result.stdout + result.stderr
        unified.save(runtime.art/'failure-radio-logs.json',radio_logs)
        import traceback
        traceback.print_exc()
    finally:
        configured = getattr(runtime, 'fabric', None)
        report['deployed_counts'] = {
            'frr_nodes': sum(runtime.router(n) in runtime.containers for n in configured.nodes) if configured else 0,
            'onboard_satellites': sum(runtime.router(n)+'-onboard' in runtime.containers for n in configured.satellites) if configured else 0,
            'kubernetes_nodes': 3 if runtime.cloud.cluster.created else 0}
        errors = []
        for cleanup in (runtime.protocols.cleanup, runtime.cleanup, runtime.noc.cleanup, runtime.cloud.cleanup, runtime.host_budget.cleanup):
            try:
                errors.extend(cleanup() or [])
            except Exception as error:
                errors.append(str(error))
        if errors:
            report.update(success=False, full_protocol_integration=False, physical_runtime_integration=False, cleanup_errors=errors)
        runtime.checks["owned_resources_cleaned"] = not errors
        final_hashes = source_hashes(runtime)
        report['source_unchanged'] = final_hashes == report['source_sha256']
        if not report['source_unchanged']:
            report.update(success=False, full_protocol_integration=False, physical_runtime_integration=False, source_sha256_after=final_hashes,
                          error='Source files changed during this run; repeat unattended acceptance')
        report.update(generated_at=unified.now(), checks=runtime.checks, traffic=runtime.traffic, timeline=runtime.timeline,
                      artifacts=str(runtime.art.relative_to(ROOT)), cloud_artifacts=str(runtime.cloud.cluster.artifacts.relative_to(ROOT)))
        unified.save(runtime.art / "platform-report.json", report)
        latest = 'physical-platform-runtime.json' if args.focus == 'physics' else 'platform-runtime.json'
        unified.save(ROOT / 'reports' / latest, report)
        if configured:
            category = 'physical-platforms' if args.focus == 'physics' else 'platforms'
            unified.save(ROOT / 'reports' / category / f'leo-{len(configured.satellites)}-{len(configured.gateways)}' / 'latest.json', report)
    print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)
    raise SystemExit(0 if report["success"] else 1)


if __name__ == "__main__":
    main()
