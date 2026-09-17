#!/usr/bin/env python3
"""Persistent, wall-clock 120-satellite FRR/5G runtime. Stop with SIGTERM.

Started by the owned 5G wrapper. Startup holds one model snapshot while routing
daemons converge. Running mode computes new states indefinitely at wall time.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'lab/platform')]
spec = importlib.util.spec_from_file_location('platform_entry', ROOT/'lab/platform/run.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
from run import command, save, now, TOOLS, GNB, UPF, wait_for
from physics_runtime import PhysicalReplay
from lab.live.model import OrbitClock, LiveFabric
from lab.live.storage import atomic, rotating, PingCounters
from lab.live.progress import Progress
from lab.live.workloads import Workloads
from tools.physical_constellation import load_config

LIVE = ROOT/'reports/live'


class LiveRuntime(entry.Platform):
    live_mode = True
    # A first commit programs every gateway and satellite in order, retaining
    # the controller's canary checks. Larger catalogs need the same bounded
    # request window as the constellation launcher.
    request_timeout = 240
    frr_operation_timeout = 20
    channel_workers = 4
    channel_command_timeout = 60
    channel_update_timeout = 300

    def vty(self, node, *commands):
        # vtysh otherwise contacts every daemon, so one unresponsive LDP
        # process can also block unrelated BGP/OSPF observations. Kill the
        # command inside the container when its deadline expires; killing
        # just docker exec leaves the remote vtysh process behind.
        daemon = None
        if len(commands) == 1:
            query = commands[0]
            for prefix, name in [('show mpls ldp ', 'ldpd'), ('show bgp ', 'bgpd'),
                                 ('show ip ospf ', 'ospfd'), ('show ipv6 ospf6 ', 'ospf6d'),
                                 ('show isis ', 'isisd'), ('show sr-te ', 'pathd'),
                                 ('show ip route', 'zebra'), ('show ipv6 route', 'zebra'),
                                 ('show mpls table', 'zebra')]:
                if query.startswith(prefix):
                    daemon = name
                    break
        args = ['vtysh', *(['-d', daemon] if daemon else [])]
        args.extend(part for item in commands for part in ('-c', item))
        return command('docker', 'exec', self.router(node), 'timeout', '-k', '2',
                       str(self.frr_operation_timeout), *args,
                       timeout=self.frr_operation_timeout + 5).stdout

    def __init__(self, constellation, physical, step, workloads_path=None):
        super().__init__(None)
        self.progress = Progress(LIVE/'progress.json', os.environ.get('SF_LIVE_SESSION', self.identifier), resume=True)
        if self.progress.value['stages'][0]['state'] == 'running':
            self.progress.finish('starting_5g')
        self.progress.context(run_id=self.identifier, cluster=self.cloud.cluster.name,
            artifacts=str(self.art.relative_to(ROOT)))
        self.cloud.cluster.observe_operation = self.progress.operation
        self.constellation_path, self.physical_path = constellation, physical
        self.step = step
        self.workloads_path = workloads_path
        self.workloads = None
        self.workload_error = None
        self.stop_event = threading.Event()
        self.ground_usable = threading.Event()
        self.phase = 'initializing'
        self.focus = 'live'
        self.event_log = rotating(self.art/'timeline.jsonl')
        self.frames = deque(maxlen=31)
        self.live_records = deque(maxlen=180)
        self.live_plans = deque(maxlen=31)
        self.ping_counters = PingCounters()
        self.ping_thread = None
        self.ping_log = rotating(self.art/'continuous-ping.log')
        self.sequence = 100000
        self.route_changes = 0
        self.started_at = now()
        self.last_error = None
        self.current_plan = None
        self.applied_at = None
        self.status_lock = threading.Lock()
        self.live_snapshot = {}
        self.skipped_intervals = 0
        self.container_extra_args = ['--log-opt', 'max-size=5m', '--log-opt', 'max-file=2']
        self.publish_status()

    def event(self, name, **values):
        value = {'run_id': self.identifier, 'at': now(), 'event': name, **values}
        self.timeline.append(value)
        self.timeline[:] = self.timeline[-120:]
        atomic(self.art/'timeline.json', self.timeline)
        self.event_log.info(json.dumps(value, ensure_ascii=False))
        print(json.dumps(value, ensure_ascii=False), flush=True)

    def publish_status(self):
        with self.status_lock:
            status = {'mode': 'live', 'run_id': self.identifier, 'pid': os.getpid(),
                'phase': self.phase, 'started_at': self.started_at, 'updated_at': now(),
                'error': self.last_error, 'endpoint': self.endpoint,
                'step_seconds': self.step, 'artifacts': str(self.art.relative_to(ROOT)),
                'sequence': self.sequence, 'route_changes': self.route_changes,
                'applied_at': self.applied_at, 'skipped_intervals': self.skipped_intervals,
                'timeline': self.timeline[-30:]}
            atomic(LIVE/'status.json', status)
            if self.live_snapshot:
                atomic(LIVE/'snapshot.json', {**self.live_snapshot, 'runtime': status})

    def stage(self, label, fn):
        if self.stop_event.is_set():
            raise InterruptedError('stop requested')
        self.phase = label
        self.progress.begin(label)
        self.event(label)
        self.publish_status()
        try:
            fn()
        except Exception as error:
            self.progress.finish(label, error=error, cancelled=self.stop_event.is_set())
            raise
        else:
            self.progress.finish(label, cancelled=self.stop_event.is_set())

    def compile_live(self):
        self.constellation_config = json.loads(self.constellation_path.read_text())
        seed_path = self.art/'seed.json'
        command(str(ROOT/'bin/sfctl'), 'constellation', 'generate', '--config', str(self.constellation_path),
                '--output', str(seed_path))
        seed = json.loads(seed_path.read_text())
        nodes = seed['topology']['nodes']
        config = load_config(self.physical_path, sorted(n['id'] for n in nodes if n['kind']=='satellite'),
                             sorted(n['id'] for n in nodes if n['kind']=='gateway'))
        config['step_seconds'] = self.step
        # A design constellation starts at the current UTC epoch. TLE/OEM inputs
        # retain their own epoch; coverage errors are reported, never replayed.
        if config['orbit']['source'] == 'walker_sgp4':
            config['epoch'] = now()
        self.orbit_clock = OrbitClock(nodes, config)
        frame = self.orbit_clock.sample(datetime.now(timezone.utc))
        seed['topology']['links'] = frame['links']
        seed['topology'].update(generated_at=now(), valid_from=now())
        seed['topology'].pop('valid_until', None)
        seed['timeline'] = []
        seed['physical_model'] = {'config': config, 'satellite_orbits': self.orbit_clock.orbits.elements,
            'reference_frame': 'TEME', 'frames': [frame], 'boundary': 'Real-time software-in-loop; modeled orbits and packet impairments; no live space telemetry.'}
        self.fabric = LiveFabric(seed, self.identifier)
        self.scenario = self.fabric.scenario
        if self.workloads_path:
            workloads = Workloads(self, json.loads(self.workloads_path.read_text()))
            if workloads.flows:
                self.workloads = workloads
                self.workloads.bind()
        self.progress.context(satellites=len(self.fabric.satellites), gateways=len(self.fabric.gateways),
                              routers=len(self.fabric.nodes), streams=len(self.workloads.flows) if self.workloads else 0)
        self.initial_frame = frame
        self.fabric.last_present = {pair: 0 for pair in self.fabric.links}
        save(self.art/'physical-model.json', self.fabric.physical_model)
        save(self.art/'scenario.json', self.scenario)
        command(str(ROOT/'bin/sfctl'), 'scenario', 'validate', '--check-paths', '--file', str(self.art/'scenario.json'))

    def start_onboard(self):
        f = self.fabric
        def create(node):
            folder = self.art/(node+'-onboard')
            folder.mkdir()
            config = json.loads((ROOT/'onboard/config.example.json').read_text())
            config.update(node_id=self.identifier+'/'+node, fallback_routes=[], fallback_targets=[
                {'prefix': UPF+'/32', 'gateway_loopback': f.loopback(f.pair[1]), 'metric': 42760},
                {'prefix': GNB+'/32', 'gateway_loopback': f.loopback(f.pair[0]), 'metric': 42760}])
            save(folder/'config.json', config)
            name = self.router(node)+'-onboard'
            command('docker', 'run', '-d', '--name', name, '--label', 'starfabric.run='+self.identifier,
                '--network', 'container:'+self.router(node), '--cpuset-cpus', self.fabric_cpus,
                '--cap-add=NET_ADMIN', *self.container_extra_args, '-e', 'TOKIO_WORKER_THREADS=1',
                '-v', f'{folder}:/data', '-v', f'{ROOT}/onboard/target/release/satellite-node-runtime:/sf-onboard:ro',
                '--entrypoint', '/sf-onboard', TOOLS, '--config', '/data/config.json', '--state-dir', '/data/state',
                '--listen', '127.0.0.1:19080', '--disconnect-hold-seconds', '25', '--heartbeat-timeout-seconds', '20')
            with self.resource_lock:
                self.containers.append(name)
            return self.onboard_request(node, '/v1/status')
        save(self.art/'onboard-inventory.json', self.parallel(f.satellites, create, 8))
        self.ground_usable.set()
        def heartbeat():
            while not self.stop_heartbeat.is_set():
                if self.ground_usable.is_set():
                    try:
                        self.request('/readyz')
                        self.generation += 1
                        save(self.art/'live-heartbeat-request.json', {'generation':self.generation,
                            'nodes':{node:self.pids[node] for node in f.satellites}})
                        result=command('docker','run','--rm','--privileged','--pid=host','--network=none',
                            '--cpuset-cpus',self.fabric_cpus,'-v',f'{self.art}:/evidence:ro',
                            '-v',f'{ROOT}/lab/live/heartbeat.py:/heartbeat.py:ro','--entrypoint','python3',TOOLS,
                            '/heartbeat.py','/evidence/live-heartbeat-request.json',timeout=20)
                        save(self.art/'live-heartbeats.json',json.loads(result.stdout))
                    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired):
                        pass
                self.stop_heartbeat.wait(3)
        self.heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        self.heartbeat_thread.start()
        wait_for('120 live onboard heartbeats', lambda: all(s['connected'] for s in
            self.parallel(f.satellites, lambda n: self.onboard_request(n, '/v1/status'),16).values()), 90)
        self.checks['onboard_runtime_on_every_configured_satellite'] = len(f.satellites)

    def route_n3(self):
        self.initial_pdu = self.pdu_identity()
        save(self.art/'pdu-identity-initial.json', self.initial_pdu)
        for endpoint, dest, via, source in [('nr_gnb',UPF,'10.230.0.1',GNB),('upf',GNB,'10.230.0.5',UPF)]:
            self.exec(endpoint, 'ip', 'route', 'add', dest+'/32', 'via', via, 'src', source)
            self.routes_added.append((endpoint,dest,via))
        # PID belongs to this run and is used for an exact, graceful stop.
        probe = 'echo $$ > /tmp/starfabric-live-ping.pid; exec ping -n -D -O -I uesimtun0 -i 1 -W 1 192.168.100.1'
        self.continuous = subprocess.Popen(['docker','exec','nr_ue','sh','-c',probe], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1)
        def read():
            for line in self.continuous.stdout:
                self.ping_log.info(line.rstrip())
                self.ping_counters.consume(line)
        self.ping_thread = threading.Thread(target=read, daemon=True)
        self.ping_thread.start()
        self.checks['gtpu_endpoints_routed_over_satellite_graph'] = True

    def provision(self, frame, tick):
        f = self.fabric
        pairs = f.register(frame, tick)
        if not pairs:
            return
        jobs = f.wiring(self.pids, pairs)
        save(self.art/'live-wiring.json', jobs)
        command('docker','run','--rm','--privileged','--pid=host','--network=none',
            '--cpuset-cpus',self.fabric_cpus, '-v',f'{self.art}:/evidence:ro',
            '-v',f'{ROOT}/lab/platform/wire.py:/wire.py:ro','--entrypoint','python3',TOOLS,
            '/wire.py','/evidence/live-wiring.json',timeout=180)
        commands = {}
        for a, b in pairs:
            for left, right in ((a,b),(b,a)):
                commands.setdefault(left, []).extend(f.interface_commands(left,right))
        def configure_contact(node):
            for attempt in range(3):
                try:
                    self.configure(node, *commands[node])
                    return
                except (RuntimeError, subprocess.TimeoutExpired) as error:
                    if attempt == 2:
                        raise
                    self.event('live_contact_configuration_retry', node=node, error=str(error)[-500:])
                    if self.stop_event.wait(5):
                        raise InterruptedError('stopped during contact configuration')
        self.parallel(list(commands), configure_contact, 8)
        self.event('live_contacts_created', pairs=[list(p) for p in sorted(pairs)])

    def retire(self, tick):
        f = self.fabric
        pairs = f.expired(tick)
        for a, b in pairs:
            interface = f.interface(a,b)
            self.exec(self.router(a),'ip','link','del',interface)
            for node in (a,b):
                self.configure(node,'mpls ldp','address-family ipv4','no interface '+interface,
                    'exit-address-family','exit','no interface '+interface)
        if pairs:
            f.forget(pairs)
            self.event('live_contacts_retired', pairs=[list(p) for p in pairs])

    def onboard_states(self):
        values = {}
        for node in self.fabric.satellites:
            try:
                values[node] = json.loads((self.art/(node+'-onboard')/'state/state.json').read_text())
            except (OSError, ValueError):
                values[node] = None
        return values

    def publish_snapshot(self, frame, record):
        self.frames.append(frame)
        self.live_records.append(record)
        self.live_plans.append(self.current_plan)
        traffic, pings = self.ping_counters.snapshot()
        self.live_snapshot = {'run_id': self.identifier, 'frames': list(self.frames),
            'records': list(self.live_records), 'plans': list(self.live_plans),
            'nodes': list(self.fabric.nodes.values()), 'intents': self.scenario['intents'],
            'physics': self.orbit_clock.config, 'orbits': self.orbit_clock.orbits.elements,
            'onboard': self.onboard_states(), 'traffic': traffic, 'pings': pings,
            'workloads': self.workloads.snapshot() if self.workloads else {'enabled': False},
            'workload_error': self.workload_error,
            'configuration': {'constellation': self.constellation_config,
                              'physical': self.orbit_clock.config},
            'checks': self.checks, 'pdu': self.initial_pdu, 'controller_status': self.last_controller_status,
            'deployed_counts': {'frr_nodes': len(self.fabric.nodes), 'onboard_satellites': len(self.fabric.satellites), 'kubernetes_nodes': 3},
            'noc_urls': {name:self.noc.url(name) for name in ('grafana','prometheus','loki','tempo')},
            'timeline': self.timeline}
        self.publish_status()

    def tick(self, frame, tick):
        started = time.monotonic()
        self.sequence += 1
        timing = {}
        stage_started = started
        def phase(name):
            nonlocal stage_started
            stamp = time.monotonic()
            if phase.previous:
                timing[phase.previous + '_seconds'] = stamp-stage_started
            phase.previous, stage_started = name, stamp
            atomic(self.art/'live-frame-progress.json', {'sequence':self.sequence,
                'model_at':frame['at'], 'phase':name, 'observed_at':now(),
                'elapsed_seconds':stamp-started, 'timings':timing})
        phase.previous = None
        phase('provision')
        self.provision(frame, tick)
        links = self.fabric.desired(frame)
        phase('channels')
        try:
            channel_timing = self.driver.channels(links, 'live-latest', readback=False)
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            # The desired interface states and queue parameters are absolute,
            # idempotent writes. Reassert the same held frame after a transient
            # failure; publish it only after all writes have completed.
            self.event('live_channel_retry', error=str(error)[-1200:])
            if self.stop_event.wait(5):
                raise InterruptedError('stopped during channel retry')
            channel_timing = self.driver.channels(links, 'live-latest', readback=False)
        self.applied_at = now()
        phase('controller')
        reprogrammed = False
        controller_error = None
        topology_version = None
        try:
            accepted, _ = self.driver.publish(links, self.sequence)
            topology_version = accepted['topology']['version']
            self.last_controller_status = self.request('/api/v1/status')
            old = self.last_controller_status.get('committed_plan')
            self.current_plan = old
            preview = self.request('/api/v1/plans/preview', {})
            planned = preview.get('plan', preview)
            if not old or planned['routes'] != old['routes']:
                committed = self.reconcile('live-commit')
                self.current_plan = committed['plan']
                self.route_changes += 1
                reprogrammed = True
            else:
                self.current_plan = old
            self.last_controller_status = self.request('/api/v1/status')
            self.ground_usable.set()
        except (RuntimeError, OSError, ValueError, KeyError) as error:
            self.ground_usable.clear()
            controller_error = str(error)[-1500:]
            self.event('live_controller_degraded', error=controller_error)
        phase('workloads')
        if self.workloads:
            try:
                self.workloads.update(self.current_plan)
                self.workload_error = None
            except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
                self.workload_error = str(error)[-1500:]
                self.event('live_workloads_degraded', error=self.workload_error)
        phase('retire')
        self.retire(tick)
        phase('complete')
        if self.continuous.poll() is not None:
            raise RuntimeError('The live UE packet probe exited; business telemetry is unavailable')
        self.phase = 'degraded' if controller_error else 'running'
        self.last_error = controller_error
        record = {'index': self.sequence, 'model_at': frame['at'], 'application_started_at': self.applied_at,
            'apply_seconds': time.monotonic()-started, 'offset_seconds': frame['offset_seconds'],
            'topology_version': topology_version,
            'routes_reprogrammed': reprogrammed, 'active_pairs': frame['active_pairs'], **timing, **channel_timing}
        self.publish_snapshot(frame, record)

    def trim_logs(self):
        # Preserve the controller log's hard link/inode used by the cloud/NOC.
        for name in ('controller.log', 'frr-agent.jsonl'):
            path = self.art/name
            if path.exists() and path.stat().st_size > 8*1024*1024:
                with path.open('r+b') as stream:
                    stream.seek(-2*1024*1024, 2)
                    tail = stream.read().split(b'\n',1)[-1]
                    stream.seek(0); stream.write(tail); stream.truncate()

    def run_forever(self, duration=0):
        self.stage('preparing_host', self.host_budget.prepare)
        self.fabric_cpus = self.host_budget.data_cpu_set
        self.stage('computing_initial_orbits', self.compile_live)
        self.stage('checking_resources', self.check_resources)
        self.stage('starting_cloud', self.cloud.prepare)
        self.stage('creating_124_routers', self.network)
        self.stage('checking_protocol_neighbors', self.protocols.setup)
        if self.workloads:
            with self.operation('workload-endpoints', '配置多业务地址', '为分布式与集中控制业务建立独立端点'):
                self.workloads.setup()
        self.stage('starting_observability', self.noc.start)
        self.host_budget.limit_containers(command('docker','ps','--filter','label=starfabric.run='+self.identifier,
            '--format','{{.Names}}').stdout.splitlines())
        self.stage('starting_controller', self.start_controller)
        def commit_initial():
            self.current_plan = self.reconcile('initial-commit')['plan']
            self.applied_at = now()
        self.stage('committing_initial_routes', commit_initial)
        self.stage('starting_5g_business', self.route_n3)
        self.stage('starting_workloads', lambda: self.workloads.start(self.current_plan) if self.workloads else None)
        self.stage('starting_120_onboard', self.start_onboard)
        self.driver = PhysicalReplay(self)
        self.last_controller_status = self.request('/api/v1/status')
        self.phase = 'running'
        if self.workloads:
            self.workloads.update(self.current_plan)
        # Expose the real held startup network before the first orbital
        # transition. Its original model/application times remain explicit.
        self.publish_snapshot(self.initial_frame, {'index':self.sequence,
            'phase':'held_startup', 'model_at':self.initial_frame['at'],
            'application_started_at':self.applied_at, 'observed_at':now(),
            'offset_seconds':self.initial_frame['offset_seconds'], 'apply_seconds':0,
            'topology_version':self.current_plan['topology_version'],
            'routes_reprogrammed':False, 'active_pairs':self.initial_frame['active_pairs']})
        self.event('continuous_runtime_started', time_scale=1, step_seconds=self.step)
        mono_base = time.monotonic()
        utc_base = datetime.now(timezone.utc)
        deadline = mono_base
        while not self.stop_event.is_set():
            if duration and time.monotonic()-mono_base >= duration:
                break
            if self.stop_event.wait(max(0, deadline-time.monotonic())):
                break
            tick = time.monotonic()-mono_base
            frame = self.orbit_clock.sample(utc_base+timedelta(seconds=tick))
            self.tick(frame, tick)
            elapsed = time.monotonic()-mono_base
            next_slot = math.floor(elapsed/self.step)+1
            scheduled_slot = math.floor((deadline-mono_base)/self.step)+1
            self.skipped_intervals += max(0,next_slot-scheduled_slot)
            deadline = mono_base+next_slot*self.step
            self.trim_logs()

    def stop_probe(self):
        if self.continuous and self.continuous.poll() is None:
            self.exec('nr_ue','sh','-c','kill -INT "$(cat /tmp/starfabric-live-ping.pid)"', check=False)
            try:
                self.continuous.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.continuous.terminate()
                self.continuous.wait(timeout=5)
        if self.ping_thread:
            self.ping_thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--constellation', type=Path, default=Path(os.environ.get('SF_LIVE_CONSTELLATION', str(ROOT/'scenarios/constellations/leo-120-global.config.json'))))
    parser.add_argument('--physical', type=Path, default=ROOT/'scenarios/constellations/physical-defaults.json')
    parser.add_argument('--step', type=float, default=float(os.environ.get('SF_LIVE_STEP','10')))
    parser.add_argument('--duration', type=float, default=float(os.environ.get('SF_LIVE_DURATION','0')), help='0 runs until stopped; positive value is an integration-test window')
    parser.add_argument('--workloads', type=Path, default=Path(os.environ.get('SF_LIVE_WORKLOADS', str(ROOT/'scenarios/constellations/live-workloads.json'))))
    parser.add_argument('--no-workloads', action='store_const', const=None, dest='workloads', help='run the existing 5G session without additional workload streams')
    args = parser.parse_args()
    if not math.isfinite(args.step) or not 1 <= args.step <= 300 or not math.isfinite(args.duration) or args.duration < 0:
        parser.error('step must be 1..300; duration must be >= 0')
    runtime = LiveRuntime(args.constellation, args.physical, args.step, args.workloads)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: runtime.stop_event.set())
    failed = False
    try:
        runtime.run_forever(args.duration)
    except InterruptedError:
        pass
    except Exception as error:
        import traceback
        traceback.print_exc()
        runtime.last_error = str(error)[-3000:]
        failed = True
    finally:
        runtime.phase = 'stopping'
        runtime.publish_status()
        errors = []
        for cleanup in ((runtime.workloads.close if runtime.workloads else lambda: None),
                        runtime.stop_probe, runtime.protocols.cleanup, runtime.cleanup,
                        runtime.noc.cleanup, runtime.cloud.cleanup, runtime.host_budget.cleanup):
            try:
                errors.extend(cleanup() or [])
            except Exception as error:
                errors.append(str(error))
        if errors:
            runtime.last_error = '; '.join(errors)[-3000:]
            failed = True
        runtime.phase = 'failed' if failed else 'stopped'
        runtime.publish_status()
        save(runtime.art/'live-session.json', {'run_id': runtime.identifier, 'mode':'live',
            'stopped_at':now(), 'error':runtime.last_error, 'cleanup_errors':errors,
            'frames_applied':runtime.sequence-100000, 'route_changes':runtime.route_changes})
    raise SystemExit(1 if failed else 0)


if __name__ == '__main__':
    main()
