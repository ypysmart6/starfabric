"""Read current packet counters independently of the orbital update cadence."""
import copy
import hashlib
import json
import time


def forwarding_signature(states):
    selected = [{key:s.get(key) for key in ('nodes', 'status', 'steering', 'route_source')} for s in states]
    return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()


def observe(snapshot, folder):
    if not snapshot.get('enabled'):
        return snapshot
    result = copy.deepcopy(snapshot)
    try:
        catalog = json.loads((folder/'catalog.json').read_text())
    except (OSError, ValueError):
        catalog = {}
    valid_catalog = catalog.get('run_id') == snapshot.get('run_id') and bool(catalog.get('token'))
    def read(name):
        try:
            value = json.loads((folder/name).read_text())
            return value if valid_catalog and value.get('run_id') == snapshot['run_id'] and value.get('token') == catalog['token'] else {}
        except (OSError, ValueError):
            return {}
    now = time.time()
    sampling = result.get('sampling', {})
    nodes = {n for f in result['flows'] for n in (f['source'],f['destination'])}
    metrics = {n:read('metrics-'+n+'.json') for n in nodes}
    proof = read('capture-latest.json')
    for flow in result['flows']:
        source, destination = metrics[flow['source']], metrics[flow['destination']]
        flow.update(fresh=bool(source and destination and all(0 <= now-m.get('observed_at',0) < 3*sampling.get('sample_seconds',5)+5 for m in (source,destination))),
                    rate_scale=source.get('rate_scale'), metrics=source.get('flows',{}).get(flow['id'],{}),
                    receiver=destination.get('flows',{}).get(flow['id'],{}), evidence=proof.get('flows',{}).get(flow['id'],{}),
                    evidence_at=proof.get('finished_at'))
        flow['evidence_fresh'] = bool(proof and 0 <= now-proof.get('finished_at',0) < 2*sampling.get('capture_interval_seconds',30)+10)
        if flow['control'] != 'distributed':
            states = [flow.get('directions',{}).get(flow['id']+'-'+d,{}) for d in ('forward','reverse')]
            flow['evidence_fresh'] = flow['evidence_fresh'] and proof.get('signatures',{}).get(flow['id']) == forwarding_signature(states)
    result['observed_at'] = now
    result['rate_control'] = {'scale':read('rates.json').get('scale',1), 'minimum':.1, 'maximum':1}
    return result
