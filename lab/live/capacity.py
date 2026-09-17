#!/usr/bin/env python3
"""Measure one owned live capacity trial without changing its forwarding.

Cold-start convergence is reported separately. A stable trial needs a full
measurement window after dynamic forwarding (not the held startup frame)
has been verified. The monitor never restarts daemons or changes rates.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from lab.live.service import Service
from lab.live.storage import atomic
from lab.live.workload_snapshot import observe


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def seconds(stamp):
    return datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp()


def verified(flow, since=0):
    evidence, directions = flow.get('evidence', {}), flow.get('directions', {})
    return bool(flow.get('fresh') and flow.get('evidence_fresh')
        and flow.get('metrics', {}).get('goodput_bps', 0) > 0
        and (flow.get('evidence_at') or 0) >= since
        and evidence.get('forward', 0) > 0 and evidence.get('reverse', 0) > 0
        and len(directions) == 2 and all(d.get('status') == 'ready' for d in directions.values()))


def monitor(run_id, streams, window, warmup, outage, output, stop_after=False):
    service = Service()
    artifact = ROOT/'lab/unified/artifacts'/run_id
    folder = artifact/'workloads'
    output.mkdir(parents=True, exist_ok=True)
    started = time.time()
    observed_frames, initial_proofs, dynamic_proofs = {}, {}, {}
    phase_names, failures = [], []
    last_print, last_phase = 0, None
    dynamic_started = measured_started = None
    baseline, last_counts, last_ack, max_gap = {}, {}, {}, {}
    current = {}
    memory = [l for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemTotal:')][0]
    report = {'run_id':run_id, 'streams':streams, 'started_at':started,
        'scope':'same 120-satellite / 24-gateway live FRR, Kubernetes, onboard and 5G system',
        'criterion':{'window_seconds':window, 'warmup_limit_seconds':warmup,
            'maximum_per_flow_outage_seconds':outage,
            'minimum_per_flow_ack_fraction':.95, 'minimum_per_flow_target_goodput_fraction':.8,
            'required_dynamic_frames':3, 'all_flows_dynamic_packet_evidence':True,
            'realtime_target_seconds':10, 'realtime_passing_fraction':.95},
        'host':{'memory':memory, 'load_at_start':Path('/proc/loadavg').read_text().strip()},
        'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [*ROOT.joinpath('lab/live').glob('*.py'), *ROOT.joinpath('lab/platform').glob('*.py')]},
        'result':'measuring'}
    atomic(output/'result.json', report)
    with (output/'observations.jsonl').open('a') as log:
        while time.time()-started < 3000:
            now = time.time()
            state = service.status()
            if state.get('run_id') != run_id:
                failures.append('owned run identity changed')
                break
            if state.get('phase') in ('failed', 'stopped', 'interrupted') or not state.get('active'):
                failures.append('runtime ended: '+str(state.get('error') or state.get('phase')))
                break
            phase = state.get('phase')
            if phase != last_phase:
                phase_names.append({'at':now,'phase':phase})
                last_phase = phase
            progress = read(artifact/'live-frame-progress.json')
            if progress and dynamic_started is None:
                dynamic_started = seconds(progress['model_at'])
            snapshot = read(ROOT/'reports/live/snapshot.json')
            if snapshot.get('run_id') == run_id:
                for record in snapshot.get('records', []):
                    if record['index'] > 100000:
                        observed_frames[record['index']] = record
            base = read(folder/'snapshot.json')
            if base.get('run_id') == run_id:
                current = observe(base, folder)
            flows = current.get('flows', [])
            newest = max(observed_frames.values(), key=lambda r:r['index'], default={})
            since = seconds(newest['application_started_at']) if newest else math.inf
            good = [f for f in flows if verified(f)]
            dynamic_good = [f for f in flows if verified(f, since)] if newest else []
            for flow in good:
                proof = {k:flow.get(k) for k in ('source','destination','carrier','evidence_at','evidence','directions')}
                initial_proofs[flow['id']] = proof
            for flow in dynamic_good:
                dynamic_proofs[flow['id']] = {**initial_proofs[flow['id']], 'frame':newest['index']}
            if flows and len(flows) != streams:
                failures.append(f'catalog changed: {len(flows)} != {streams}')
                break
            if flows and current.get('summary',{}).get('gateways') != 24:
                failures.append('workloads do not exercise all 24 gateways')
                break
            if any(f.get('fresh') and f.get('rate_scale') != .25 for f in flows):
                failures.append('sending rate changed during the fixed-rate trial')
                break
            if measured_started is None and len(dynamic_good) == streams and phase == 'running':
                measured_started = now
                baseline = {f['id']:dict(f['metrics']) for f in flows}
                last_counts = {f['id']:f['metrics'].get('acked_packets',0) for f in flows}
                last_ack = {f['id']:now for f in flows}
                max_gap = {f['id']:0 for f in flows}
                atomic(output/'measurement-start.json', {'at':now,'snapshot':snapshot,'workloads':current})
            if measured_started is not None:
                for flow in flows:
                    key, metrics = flow['id'], flow.get('metrics', {})
                    count = metrics.get('acked_packets',0)
                    if flow.get('fresh') and count > last_counts[key]:
                        last_ack[key] = now
                    last_counts[key] = count
                    gap = now-last_ack[key]
                    max_gap[key] = max(max_gap[key],gap)
                    if gap > outage:
                        failures.append(f'{key}: no fresh acknowledged traffic for {gap:.1f}s')
                if phase == 'degraded':
                    failures.append('controller degraded in measurement: '+str(state.get('error'))[-400:])
            elapsed = now-measured_started if measured_started else 0
            entry = {'at':now,'phase':phase,'sequence':state.get('sequence'),
                'frame_progress':progress,'measurement_seconds':elapsed,
                'verified_now':len(good),'dynamic_verified_now':len(dynamic_good),
                'load':Path('/proc/loadavg').read_text().strip(),
                'flows':{f['id']:{'fresh':f.get('fresh'),'rate_scale':f.get('rate_scale'),
                    'metrics':f.get('metrics'), 'evidence_at':f.get('evidence_at'),
                    'evidence_fresh':f.get('evidence_fresh'),
                    'forward':f.get('evidence',{}).get('forward',0),
                    'reverse':f.get('evidence',{}).get('reverse',0),
                    'states':{k:{x:v.get(x) for x in ('status','error','nodes','observed_at')}
                              for k,v in f.get('directions',{}).items()}} for f in flows}}
            log.write(json.dumps(entry,ensure_ascii=False,separators=(',',':'))+'\n');log.flush()
            if now-last_print >= 30:
                print(json.dumps({'at':now,'phase':phase,'sequence':state.get('sequence'),
                    'verified':len(good),'dynamic_verified':len(dynamic_good),
                    'measured_seconds':round(elapsed),'progress':progress.get('phase')},ensure_ascii=False),flush=True)
                last_print = now
            report.update(observed_at=now, phase=phase, dynamic_started_at=dynamic_started,
                measurement_started_at=measured_started, measured_seconds=elapsed,
                initial_or_later_verified=len(initial_proofs), dynamic_verified=len(dynamic_proofs),
                currently_verified=len(good), frames=list(observed_frames.values()), failures=failures,
                maximum_flow_outages=max_gap, phases=phase_names)
            atomic(output/'result.json',report)
            if failures or elapsed >= window:
                break
            if dynamic_started and not measured_started and now-dynamic_started > warmup:
                failures.append(f'all {streams} flows did not become verified on a dynamic frame within {warmup}s')
                break
            time.sleep(3)
        else:
            failures.append('trial wall-clock timeout before a complete measurement window')
    finished = time.time()
    measured_seconds = finished-measured_started if measured_started else 0
    results = {}
    if measured_started:
        for flow in current.get('flows',[]):
            key, metrics = flow['id'], flow.get('metrics',{})
            tx = metrics.get('tx_packets',0)-baseline[key].get('tx_packets',0)
            ack = metrics.get('acked_packets',0)-baseline[key].get('acked_packets',0)
            ack_bytes = metrics.get('acked_bytes',0)-baseline[key].get('acked_bytes',0)
            target = flow['rate_bps'] * .25
            results[key]={'tx_packets':tx, 'acked_packets':ack, 'ack_fraction':ack/max(1,tx),
                'average_goodput_bps':ack_bytes*8/max(1,measured_seconds),
                'target_bps':target,'target_fraction':ack_bytes*8/max(1,measured_seconds)/target,
                'maximum_outage_seconds':max_gap[key]}
            if results[key]['ack_fraction'] < .95 or results[key]['target_fraction'] < .8:
                failures.append(key+': delivery/throughput threshold not met')
    measured_frames = [r for r in observed_frames.values()
        if measured_started and seconds(r['model_at']) >= measured_started]
    if measured_seconds < window:
        failures.append('full measurement window not completed')
    if len(measured_frames) < 3:
        failures.append('fewer than three dynamic frames during measurement')
    if len(dynamic_proofs) != streams:
        failures.append('not every flow has post-transition bidirectional packet evidence')
    realtime = sum(r['apply_seconds']<=10 for r in measured_frames)/max(1,len(measured_frames))
    report.update(finished_at=finished, result='failed' if failures else 'passed', failures=failures,
        measured_seconds=measured_seconds, flow_results=results, dynamic_proofs=dynamic_proofs,
        frames=list(observed_frames.values()), realtime_passing_fraction=realtime,
        realtime_passed=bool(not failures and realtime>=.95), phases=phase_names)
    atomic(output/'result.json',report)
    atomic(output/'last-workloads.json',current)
    if stop_after and service.status().get('run_id') == run_id:
        service.stop()
    print(json.dumps({'result':report['result'],'run_id':run_id,'streams':streams,
        'failures':failures[:8], 'report':str(output/'result.json')},ensure_ascii=False),flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--streams',type=int,required=True)
    parser.add_argument('--window',type=int,default=600)
    parser.add_argument('--warmup',type=int,default=600)
    parser.add_argument('--outage',type=int,default=60)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--stop-after',action='store_true')
    args = parser.parse_args()
    if args.streams < 1 or args.window < 60 or args.warmup < 60 or args.outage < 5:
        parser.error('invalid trial bounds')
    monitor(args.run_id,args.streams,args.window,args.warmup,args.outage,args.output,args.stop_after)


if __name__ == '__main__':
    main()
