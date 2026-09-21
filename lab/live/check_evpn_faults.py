#!/usr/bin/env python3
"""Withdraw/restart one owned EVPN gateway in the running full constellation.

Run after the uninterrupted stability window. Verify L2, L3 and N3 fail closed,
then recover without recreating the UE PDU. The exporting gateway's BGP helper
is restored in finally even if a check fails; unrelated containers are untouched.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from lab.live.check_evpn_system import read,pdu
from lab.live.storage import atomic


def command(*args,check=True):
    return subprocess.run(['docker',*args],capture_output=True,text=True,check=check,timeout=30)


def wait(label, predicate, seconds=60):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        value=predicate()
        if value: return value
        time.sleep(1)
    raise AssertionError(label+' did not complete within '+str(seconds)+' seconds')


def run(run_id, output):
    output.mkdir(parents=True,exist_ok=True)
    state=read(ROOT/'reports/live/status.json')
    if state.get('run_id')!=run_id or state.get('phase')!='running': raise ValueError('requested runtime is not running')
    art=ROOT/'lab/unified/artifacts'/run_id; folder=art/'workloads'
    scenario=read(art/'scenario.json'); n3=next(i for i in scenario['intents'] if i['id']=='n3-forward')
    source,target=n3['source'],n3['destination']; name=run_id+'-evpn-'+target
    info=json.loads(command('inspect',name).stdout)[0]
    if info['Config']['Labels'].get('starfabric.run')!=run_id or not info['State']['Running']:
        raise ValueError('EVPN helper ownership/running check failed')
    config=read(folder/('evpn-'+target+'.json'))
    if config.get('run_id')!=run_id: raise ValueError('EVPN config ownership mismatch')
    catalog=read(folder/'catalog.json')
    flow=next(f for f in catalog['flows'] if f['source']==source and f['destination']==target and f['carrier']=='evpn-srv6')
    ce=read(folder/'evpn-l2-catalog.json')['gateways']; left,right=ce[source][0],ce[target][0]
    def rib(): return read(folder/('evpn-'+source+'-rib.json'))
    def ping_l3(): return command('exec',run_id+'-'+source,'ping','-I',flow['source_address'],'-c','1','-W','1',flow['destination_address'],check=False)
    def ping_l2(): return command('exec',left['container'],'ping','-c','1','-W','1',right['ipv4'],check=False)
    def restore():
        args=['run','--rm','-d','--name',name,'--privileged','--pid=host','--network=none',
              '--label','starfabric.run='+run_id,'-v',str(folder)+':/work','-v',str(ROOT/'lab/live')+':/scripts:ro',
              '--entrypoint','python3']
        if info['HostConfig'].get('CpusetCpus'): args+=['--cpuset-cpus',info['HostConfig']['CpusetCpus']]
        command(*args,info['Image'],'/scripts/evpn_speaker.py','/work/evpn-'+target+'.json')
    result={'run_id':run_id,'gateway':target,'started_at':time.time(),'success':False}
    stopped=False
    try:
        result['pdu_before']=pdu()
        wait('baseline L3',lambda:ping_l3().returncode==0)
        wait('baseline L2',lambda:ping_l2().returncode==0)
        stopped=True
        command('stop','-t','3',name)
        withdrawn_at=time.time()
        wait('Type-5 withdrawal',lambda:flow['destination_address']+'/32' not in rib().get('routes',{}))
        wait('N3 unreachable',lambda:'unreachable' in command('exec',run_id+'-'+source,'ip','route','show','table','300').stdout)
        if ping_l3().returncode==0 or ping_l2().returncode==0: raise AssertionError('withdrawn L2/L3 route still forwards')
        probe=command('exec','nr_ue','ping','-I','uesimtun0','-c','2','-W','1','192.168.100.1',check=False)
        if probe.returncode==0: raise AssertionError('N3 silently fell back after EVPN withdrawal')
        result['withdrawal']={'l2_blocked':True,'l3_blocked':True,'n3_blocked':True,'seconds':time.time()-withdrawn_at}
        restore(); stopped=False; resumed=time.monotonic()
        def recovered(label,predicate):
            wait(label,predicate,seconds=max(0,60-(time.monotonic()-resumed)))
        recovered('Type-5 reannouncement',lambda:flow['destination_address']+'/32' in rib().get('routes',{}))
        recovered('L3 recovery',lambda:ping_l3().returncode==0)
        recovered('L2 recovery',lambda:ping_l2().returncode==0)
        recovered('N3 recovery',lambda:command('exec','nr_ue','ping','-I','uesimtun0','-c','1','-W','1','192.168.100.1',check=False).returncode==0)
        result['recovery_seconds']=time.monotonic()-resumed
        if result['recovery_seconds']>60: raise AssertionError('combined recovery exceeded 60 seconds')
        result['pdu_after']=pdu()
        if result['pdu_after']!=result['pdu_before']: raise AssertionError('PDU session identity changed')
        result['same_pdu_session']=True; result['success']=True
    except Exception as error:
        result.update(error=str(error),stderr=getattr(error,'stderr',None))
        raise
    finally:
        if stopped:
            try:
                existing=command('inspect',name,check=False)
                if existing.returncode: restore()
                elif not json.loads(existing.stdout)[0]['State']['Running']:
                    command('rm',name);restore()
            except Exception as error: result['restore_error']=str(error)
        result['finished_at']=time.time(); atomic(output/'result.json',result)
        print(json.dumps({'success':result['success'],'report':str(output/'result.json')}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',required=True);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();run(args.run_id,args.output)
