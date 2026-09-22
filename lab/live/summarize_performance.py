#!/usr/bin/env python3
"""Summarize retained run-scoped performance evidence without contacting devices."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
import math
from pathlib import Path
import statistics


def records(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def distribution(values):
    values = sorted(values)
    if not values:
        return {'count':0}
    return {'count':len(values), 'mean':statistics.mean(values), 'median':statistics.median(values),
            'p95_nearest_rank':values[math.ceil(.95*len(values))-1], 'max':max(values)}


def summarize(artifacts, report=None):
    frames = records(artifacts/'performance-frames.jsonl')
    scope = 'all completed dynamic frames retained in artifacts (includes warmup)'
    if report is not None:
        start, end = report.get('measurement_started_at'), report.get('observed_at')
        # Freeze membership at the monitor's last observation. The runtime can
        # finish another frame while shutting down after measurement ends.
        frames = report.get('frames', frames)
        frames = [f for f in frames if start is not None and end is not None and
                  start <= datetime.fromisoformat(f['model_at']).timestamp() <= end and
                  'scheduled_at' in f and 'scheduled_to_completion_seconds' in f and
                  datetime.fromisoformat(f['scheduled_at']).timestamp()+f['scheduled_to_completion_seconds'] <= end]
        scope = 'frames observed complete by report observed_at, with model_at inside measurement_started_at..observed_at'
    frame_ids = {str(f['index']) for f in frames}
    logs = records(artifacts/'controller.log')
    profiles = [r for r in logs if r.get('message') == 'reconciliation profile' and
                r.get('correlation',{}).get('frame_id') in frame_ids]
    request_ids = {r['correlation']['request_id'] for r in profiles}
    operations = [r for path in artifacts.glob('frr-agent.jsonl*') for r in records(path)
                  if r.get('correlation',{}).get('request_id') in request_ids]
    phases, work, counts = defaultdict(list), defaultdict(list), Counter()
    cpu = defaultdict(list)
    for row in profiles:
        for name, value in row['profile']['phases'].items():
            phases[name].append(value['total_seconds'])
        for name, value in row['profile']['work'].items():
            work[name].append(value['total_seconds'])
        counts.update(row['profile']['counts'])
        before, after = row['cpu_before']['cgroups'], row['cpu_after']['cgroups']
        for path in before.keys() & after.keys():
            def stat(group):
                return {key:int(value) for key,value in
                        (line.split() for line in group.get('cpu.stat','').splitlines())}
            initial, final = stat(before[path]), stat(after[path])
            for key in initial.keys() & final.keys():
                delta = final[key]-initial[key]
                if delta >= 0:
                    cpu[path+':'+key].append(delta)
    command_counts = Counter()
    for row in operations:
        if row['program'] == 'vtysh':
            for command in row['args'][1::2]:
                if command.startswith('show '): command_counts[command] += 1
        else:
            command_counts[row['program']] += 1
    client = [r for r in records(artifacts/'controller-client.jsonl') if r.get('frame_id') in frame_ids]
    result = {
        'artifacts':str(artifacts), 'scope':scope, 'frame_count':len(frames),
        'frame_indices':[f['index'] for f in frames],
        'frame_seconds':{key:distribution(f[key] for f in frames if key in f) for key in (
            'apply_seconds','iteration_seconds','sample_seconds','snapshot_seconds','controller_seconds',
            'provision_seconds','channels_seconds','workloads_seconds','retire_seconds',
            'scheduled_to_forwarding_seconds','scheduled_to_completion_seconds')},
        'profile_count':len(profiles), 'profile_outcomes':dict(Counter(r['outcome'] for r in profiles)),
        'transaction_seconds':distribution(r['duration_seconds'] for r in profiles),
        'phase_wall_seconds':{k:distribution(v) for k,v in phases.items()},
        'device_work_seconds':{k:distribution(v) for k,v in work.items()},
        'transaction_counts':dict(counts), 'cpu_counter_deltas':{k:distribution(v) for k,v in cpu.items()},
        'max_history_entries_per_device':max((r['profile']['maxima'].get('history_entries_per_device',0) for r in profiles),default=0),
        'frr_logged_operations':len(operations), 'frr_exit_codes':dict(Counter(str(r.get('exit_code')) for r in operations)),
        'frr_commands':dict(command_counts),
        'executor_per_command_seconds':{key:distribution((r.get('executor_timing') or {})[key] for r in operations
            if key in (r.get('executor_timing') or {})) for key in ('slot_wait_seconds','execution_seconds','executor_seconds')},
        'client_requests':len(client),
        'limitations':['Device work times overlap; do not add them to phase wall times.',
                      'Logs retain bounded history; compare operation counts with transaction counts for coverage.',
                      'CPU counter deltas include concurrent work in the same cgroup, not only this transaction.']}
    if report is not None:
        result['acceptance'] = {k:report.get(k) for k in ('functional_passed','realtime_passed','overall_passed',
            'measured_seconds','source_unchanged','realtime_assessment','maximum_ack_receive_gaps','failures')}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts',type=Path,required=True)
    parser.add_argument('--report',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text()) if args.report else None
    result = summarize(args.artifacts,report)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'output':str(args.output),'frames':result['frame_count'],'profiles':result['profile_count']}))


if __name__ == '__main__':
    main()
