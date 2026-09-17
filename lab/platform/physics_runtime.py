"""Replay computed fleet contacts into the real packet network on a 1:1 clock."""
from __future__ import annotations
import copy
import json
import time
from datetime import datetime, timezone

from channel import qdisc_command
from run import ROOT, TOOLS, command, save, now


def snapshot_links(frame, previous):
    current = {link['id']: copy.deepcopy(link) for link in frame['links']}
    unknown = set(current) - {link['id'] for link in previous}
    if unknown:
        raise ValueError(f'physical frame has links outside provisioned horizon: {sorted(unknown)}')
    return [current.get(link['id'], dict(link, operational_up=False, acquisition_state='unavailable')) for link in previous]


class PhysicalReplay:
    def __init__(self, runtime):
        self.r = runtime
        self.f = runtime.fabric
        self.model = self.f.physical_model
        self.records = []
        self.changed_pairs = set()
        self.channel_updates = 0
        self.last_sequence = 99999

    def channels(self, links, index, readback=False):
        started = time.monotonic()
        r, f = self.r, self.f
        before = f.directed
        desired = {(l['source'], l['target']): l for l in links}
        jobs = {n: {'name': n, 'pid': r.pids[n], 'commands': [],
                    'timeout_seconds':getattr(r,'channel_command_timeout',15)} for n in f.nodes}
        # Withdraw first, then update delay/rate, then enable new contacts. There
        # is no brief unshaped forwarding window when a physical contact appears.
        for ends, link in desired.items():
            a, b = ends; interface = f.interface(a, b); old = before[ends]
            was_up, is_up = f.active(a, b), link['operational_up']
            if was_up != is_up:
                self.changed_pairs.add(tuple(sorted(ends)))
            if not is_up and was_up:
                jobs[a]['commands'].append(['ip', 'link', 'set', interface, 'down'])
        for ends, link in desired.items():
            a, b = ends; interface = f.interface(a, b); old = before[ends]
            changed = any(link.get(k, 0) != old.get(k, 0) for k in ('latency_us', 'capacity_bps', 'loss_ppm'))
            if changed or index == 0:
                jobs[a]['commands'].append(qdisc_command(interface, link, f.queue_limit, 'change'))
                self.channel_updates += 1
            if link['operational_up'] and not f.active(a, b):
                jobs[a]['commands'].append(['ip', 'link', 'set', interface, 'up'])
            if readback:
                jobs[a]['commands'].append(['tc', '-j', '-s', 'qdisc', 'show', 'dev', interface])
                jobs[a]['commands'].append(['ip', '-j', 'link', 'show', 'dev', interface])
                jobs[a]['commands'].append(['sysctl', '-n', f'net.ipv4.conf.{interface}.rp_filter'])
        job_path = r.art / f'physical-jobs-{index}.json'
        result_path = r.art / f'physical-channels-{index}.json'
        save(job_path, [job for job in jobs.values() if job['commands']])
        command('docker', 'run', '--rm', '--privileged', '--pid=host', '--network=none',
                '--cpuset-cpus', r.fabric_cpus,
                '-v', f'{r.art}:/evidence', '-v', f'{ROOT}/lab/platform/physics_jobs.py:/jobs.py:ro',
                '--entrypoint', 'python3', TOOLS, '/jobs.py', '/evidence/' + job_path.name,
                '/evidence/' + result_path.name, str(getattr(r,'channel_workers',16)),
                timeout=getattr(r,'channel_update_timeout',180))
        f.set_links(links)
        records = json.loads(result_path.read_text())
        if readback:
            self.verify_readback(records)
        return {'channel_seconds': time.monotonic()-started}

    def verify_readback(self, records):
        checked = 0
        for node in self.f.nodes:
            by_interface = {v['interface']: v for v in records[node]}
            for peer in self.f.adjacency[node]:
                link = self.f.directed[node, peer]
                observed = by_interface[self.f.interface(node, peer)]
                if observed['rp_filter'] != 0:
                    raise AssertionError(('physical interface still filters asymmetric service sources',node,peer,observed))
                flags = observed['link']['flags']
                if ('UP' in flags) != link['operational_up'] or (link['operational_up'] and 'LOWER_UP' not in flags):
                    raise AssertionError(('kernel interface differs from physical contact', node, peer, flags, link))
                qdisc = next(q for q in observed['qdisc'] if q['kind'] == 'netem')
                options = qdisc['options']
                # iproute2 JSON represents netem delay in seconds and rate in bytes/s.
                if abs(options['delay']['delay'] * 1e6 - link['latency_us']) > 2:
                    raise AssertionError(('kernel delay differs from physical model', node, peer, options, link))
                rate = options['rate'].get('rate64', options['rate']['rate'])
                if abs(rate * 8 - link['capacity_bps']) > 8:
                    raise AssertionError(('kernel rate differs from physical model', node, peer, options, link))
                actual_loss=options.get('loss-random',{}).get('loss',0)
                if abs(actual_loss-link.get('loss_ppm',0)/1e6)>1e-8:
                    raise AssertionError(('kernel loss differs from physical model',node,peer,options,link))
                checked += 1
        if checked != len(self.f.directed):
            raise AssertionError('missing physical link readbacks')

    def publish(self, links, sequence):
        at = now()
        events = [{'event_id': f'{self.r.identifier}-physical-{sequence}-{l["id"]}', 'type': 'link_update',
            'subject': l['id'], 'sequence': sequence, 'observed_at': at, 'effective_at': at, 'link': l} for l in links]
        accepted = self.r.request('/api/v1/topology/events/batch', {'events': events})
        self.last_sequence = sequence
        return accepted, at

    def refresh_held_snapshot(self):
        """Reobserve the held final network before ground recovery replans.

        Orbital replay has explicitly stopped. Freshness comes from reading the
        actual interfaces and queues, while orbital time remains at the last frame.
        Never relabel an old observation as fresh without checking the network.
        """
        sequence = self.last_sequence + 1
        links = self.f.scenario['topology']['links']
        self.channels(links, f'held-{sequence}', readback=True)
        accepted, at = self.publish(links, sequence)
        record = {'mode': 'held final physical snapshot', 'model_at': self.model['frames'][-1]['at'],
            'observed_at': at, 'topology_version': accepted['topology']['version'],
            'directed_interfaces_verified': len(links), 'sequence': sequence}
        save(self.r.art / 'physical-held-snapshot.json', record)
        self.r.event('physical_held_snapshot_reobserved', **record)

    def run(self):
        r, f = self.r, self.f
        frames = self.model['frames']
        r.event('physical_replay_started', epoch=self.model['config']['epoch'], time_scale=1,
                step_seconds=self.model['config']['step_seconds'], frames=len(frames),
                initial_protocol_phase='held epoch snapshot')
        started = time.monotonic()
        baseline = copy.deepcopy(f.scenario['topology']['links'])
        for index, frame in enumerate(frames):
            deadline = started + frame['offset_seconds']
            while time.monotonic() < deadline:
                time.sleep(max(0,min(0.25, deadline - time.monotonic())))
            lag = time.monotonic() - deadline
            # Do not silently compress orbital time when a host cannot apply a
            # complete sampling instant before the next one is due.
            if lag > self.model['config']['step_seconds']:
                raise RuntimeError(f'physical replay cannot keep 1:1 time: frame {index}, lag {lag:.3f}s')
            links = snapshot_links(frame, f.scenario['topology']['links'])
            timing = self.channels(links, index, readback=index in (0, len(frames)-1))
            accepted, at = self.publish(links, 100000 + index)
            preview = r.request('/api/v1/plans/preview', {})
            plan = preview.get('plan', preview)
            committed = r.request('/api/v1/status')['committed_plan']
            reprogrammed = plan['routes'] != committed['routes']
            if reprogrammed:
                r.reconcile('physical-' + str(index))
            record = {'index': index, 'model_at': frame['at'], 'offset_seconds': frame['offset_seconds'],
                'application_started_at': at, 'start_lag_seconds': lag, 'apply_seconds': time.monotonic()-deadline-lag,
                'topology_version': accepted['topology']['version'], 'active_pairs': len(f.active_pairs()),
                'routes_reprogrammed': reprogrammed, 'route_count': len(plan['routes']), **timing}
            self.records.append(record)
            save(r.art / 'physical-replay.json', {'success': False, 'frames': self.records})
            r.event('physical_frame_applied', **record)
        final = f.scenario['topology']['links']
        changed_parameters = sum(any(l.get(k) != b.get(k) for k in ('latency_us','capacity_bps','operational_up')) for l,b in zip(final,baseline))
        if not changed_parameters:
            raise AssertionError('physical scenario did not change any link parameter')
        r.ping('physical-final',20)
        report = {'success': True, 'time_scale': 1, 'frames': self.records,
            'satellites_propagated': len(self.model['satellite_orbits']), 'geographic_gateways': len(self.model['config']['ground_stations']),
            'changed_contact_pairs': sorted(self.changed_pairs), 'channel_updates': self.channel_updates,
            'final_parameter_changes': changed_parameters, 'packet_probe': 'physical-final',
            'boundary': self.model['boundary']}
        save(r.art / 'physical-replay.json', report)
        r.checks.update(per_satellite_orbits_drive_contacts=True, geographic_gateway_visibility=True,
            computed_physical_parameters_in_kernel=True, physical_replay_on_same_frr_and_5g=True)
