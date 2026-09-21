#!/usr/bin/env python3
"""Read-only acceptance of EVPN L2, SRv6 N3 and the same real UE session.

Complements capacity.py: 600 seconds, all configured CE sites, IPv4 + IPv6,
satellite packet evidence, and no 60-second outage. It never changes routes,
rates or workloads and binds every observation to one owned runtime.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from lab.live.storage import atomic


def read(path):
    try: return json.loads(path.read_text())
    except (OSError,ValueError): return {}


def pdu():
    def cmd(*args): return subprocess.run(['docker',*args],capture_output=True,text=True,check=True,timeout=30)
    info=json.loads(cmd('inspect','nr_ue').stdout)[0]
    logs=cmd('logs','nr_ue')
    return {'container_id':info['Id'],'pdu_establishments':(logs.stdout+logs.stderr).count('PDU Session establishment is successful'),
            'tun_addresses':json.loads(cmd('exec','nr_ue','ip','-j','address','show','uesimtun0').stdout)[0]['addr_info']}


def packet_proofs(capture,run_id,token,frames,since,now):
    """Accept packets captured after a dynamic application in this window."""
    started=capture.get('started_at',0); finished=capture.get('finished_at',0)
    if (capture.get('run_id')!=run_id or not token or capture.get('token')!=token
            or not since<=started<=finished<=now):
        return {}
    prior=[r for r in frames.values() if r['index']>100000 and
           datetime.fromisoformat(r['application_started_at']).timestamp()<=started]
    if not prior:
        return {}
    frame=max(prior,key=lambda r:r['index'])
    return {key:{**value,'at':finished,'capture_started_at':started,'frame':frame['index']}
            for key,value in capture.get('services',{}).items()
            if value.get('packets',0)>0 and 'srh' in value.get('layers',[])
            and any(node.startswith('sat-') and count>0 for node,count in value.get('nodes',{}).items())}


def monitor(run_id,output,window=600):
    art=ROOT/'lab/unified/artifacts'/run_id; folder=art/'workloads'
    output.mkdir(parents=True,exist_ok=True)
    result={'run_id':run_id,'started_at':time.time(),'result':'measuring',
            'criteria':{'window_seconds':window,'maximum_outage_seconds':60,'minimum_reply_fraction':.95,
                        'packet_evidence':'capture starts within measurement window after a dynamic application'}}
    start=None; baseline={}; last={}; max_gap={}; proofs={}; frames={}; failures=[]; last_print=0
    warmup_proofs={}
    probes={}; required={'n3:172.22.0.8','n3:172.22.0.23'}
    def n3_replies():
        try: lines=(art/'continuous-ping.log').read_text().splitlines()
        except OSError: return []
        return [(float(m[1]),int(m[2])) for line in lines
                if (m:=re.search(r'\[([\d.]+)\].*icmp_seq=(\d+).*time[=<]',line)) and 'DUP!' not in line]
    def n3_transmitted():
        # ping -O also emits the sequence of unanswered packets. Counting
        # only the last reply underestimates a trailing outage.
        try: raw=(art/'continuous-ping.log').read_text()
        except OSError: return 0
        return max((int(m) for m in re.findall(r'icmp_seq=(\d+)',raw)),default=0)
    with (output/'observations.jsonl').open('w') as log:
        while time.time()-result['started_at']<3000:
            now=time.time(); state=read(ROOT/'reports/live/status.json')
            if state.get('run_id') != run_id: failures.append('run identity changed'); break
            if state.get('phase') in ('stopped','failed','interrupted'): failures.append('runtime stopped before acceptance'); break
            l2_catalog=read(folder/'evpn-l2-catalog.json')
            catalog=l2_catalog.get('gateways',{})
            workloads=read(folder/'catalog.json')
            expected_nodes={f[endpoint] for f in workloads.get('flows',[])
                            for endpoint in ('source','destination')}
            if catalog and l2_catalog.get('run_id')!=run_id:
                failures.append('L2 catalog run identity changed'); break
            if workloads and workloads.get('run_id')!=run_id:
                failures.append('workload catalog run identity changed'); break
            if catalog and expected_nodes and set(catalog)!=expected_nodes:
                failures.append('L2 catalog does not cover all workload gateways'); break
            if catalog and not all(catalog.values()):
                failures.append('L2 catalog has a gateway without CE services'); break
            probes={}
            for node,services in catalog.items():
                for service in services:
                    record=read(folder/f"l2-metrics-{service['evi']}-{node}.json")
                    for family in ('4','6'):
                        value=record.get('families',{}).get(family,{})
                        if record.get('run_id')==run_id:
                            probes[f"{node}:{service['evi']}:{family}"]={**value,'fresh':now-record.get('observed_at',0)<10}
            replies=n3_replies()
            if replies: probes['n3']={'received':len(replies),'transmitted':n3_transmitted(),'last_reply':replies[-1][0],'fresh':True}
            proof=read(folder/'capture-latest.json')
            snapshot=read(ROOT/'reports/live/snapshot.json')
            if snapshot.get('run_id')==run_id:
                frames.update({r['index']:r for r in snapshot.get('records',[]) if r['index']>100000})
            observed_proofs=packet_proofs(proof,run_id,workloads.get('token'),frames,start or 0,now)
            if start is None:
                warmup_proofs.update(observed_proofs)
            else:
                proofs.update(observed_proofs)
            required={'n3:172.22.0.8','n3:172.22.0.23'}
            nodes=sorted(catalog)
            for index,node in enumerate(nodes):
                for svc in catalog[node]:
                    remote=next(s for s in catalog[nodes[(index+1)%len(nodes)]] if s['evi']==svc['evi'])
                    for family in ('ipv4','ipv6'):
                        required.update(('l2:'+svc[family]+':'+remote[family], 'l2:'+remote[family]+':'+svc[family]))
            count=sum(len(v) for v in catalog.values())*2+1
            ready=(bool(expected_nodes) and set(catalog)==expected_nodes and len(probes)==count and all(v.get('fresh') and now-(v.get('last_reply') or 0)<10 for v in probes.values())
                   and required <= set(warmup_proofs) and bool(frames))
            if start is None and ready and state.get('phase')=='running':
                start=now; baseline={k:dict(v) for k,v in probes.items()}; last={k:v['last_reply'] for k,v in probes.items()}
                max_gap={k:0 for k in probes}; result['pdu_initial']=read(art/'pdu-identity-initial.json')
                result['measurement_started_at']=start
            if start:
                if state.get('phase')=='degraded': failures.append('controller degraded during service measurement')
                for key in baseline:
                    value=probes.get(key,{})
                    if value.get('fresh'): last[key]=max(last[key],value.get('last_reply') or 0)
                    gap=now-last[key]; max_gap[key]=max(max_gap[key],gap)
                    if gap>60: failures.append(f'{key}: no reply for {gap:.1f}s')
            elapsed=now-start if start else 0
            entry={'at':now,'phase':state.get('phase'),'probes':probes,'measured_seconds':elapsed,'proof_services':len(proofs)}
            log.write(json.dumps(entry)+'\n'); log.flush()
            result.update(observed_at=now,measured_seconds=elapsed,gateways=len(catalog),probe_count=len(probes),
                          maximum_outages=max_gap,packet_proofs=proofs,dynamic_frames=len(frames),failures=failures)
            atomic(output/'result.json',result)
            if now-last_print>30:
                print(json.dumps({k:result[k] for k in ('run_id','measured_seconds','gateways','probe_count','dynamic_frames')}),flush=True);last_print=now
            if failures or elapsed>=window: break
            time.sleep(3)
        if not start or time.time()-start<window: failures.append('full measurement window not completed')
        result['delivery']={}
        for key,base in baseline.items():
            value=probes.get(key,{})
            sent=value.get('transmitted',0)-base.get('transmitted',0)
            received=value.get('received',0)-base.get('received',0)
            ratio=received/max(1,sent)
            result['delivery'][key]={'sent':sent,'received':received,'reply_fraction':ratio}
            if ratio<.95: failures.append(key+': reply fraction below 95%')
        measured_frames=[r for r in frames.values() if start and datetime.fromisoformat(r['model_at']).timestamp()>=start]
        if len(measured_frames)<3: failures.append('fewer than 3 dynamic frames during measurement')
        result['measured_dynamic_frames']=len(measured_frames)
        result['missing_packet_proofs']=sorted(required-set(proofs))
        if required-set(proofs): failures.append('not all CE directions have satellite packet evidence within the measurement window')
        try:
            result['pdu_final']=pdu()
            if result.get('pdu_initial') != result['pdu_final']: failures.append('UE PDU session identity changed or missing')
        except (OSError,ValueError,subprocess.SubprocessError) as error: failures.append('PDU identity unavailable: '+str(error))
        result.update(result='failed' if failures else 'passed',failures=failures,finished_at=time.time())
        atomic(output/'result.json',result)
        print(json.dumps({'result':result['result'],'failures':failures,'report':str(output/'result.json')}),flush=True)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',required=True); parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    return 0 if monitor(args.run_id,args.output)['result']=='passed' else 1


if __name__=='__main__':
    raise SystemExit(main())
