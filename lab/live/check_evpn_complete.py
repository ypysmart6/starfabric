#!/usr/bin/env python3
"""Complete acceptance of an owned 120/24 live EVPN/SRv6 run, then cleanup.

Observe L3 and L2/N3 concurrently. Only after both uninterrupted 600-second
windows pass, withdraw/restart one EVPN gateway, interrupt the radio link,
and verify recovery of the same UE PDU. Bind every action and result to the
supplied run ID.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import subprocess
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from lab.live.capacity import monitor as capacity
from lab.live.check_evpn_system import monitor as services
from lab.live.check_evpn_faults import run as faults
from lab.live.service import Service,read
from lab.live.storage import atomic


def run(run_id,output,session_id=None):
    service=Service(); state=service.status()
    config=state.get('launch_config') or {}
    if state.get('run_id')!=run_id or not state.get('active'):
        raise ValueError('requested run is not active')
    if session_id is not None and state.get('session')!=session_id:
        raise ValueError('requested launch session is not active')
    if (config.get('satellites'),config.get('gateways'),config.get('carriers')) != (120,24,['evpn-srv6']):
        raise ValueError('this gate requires the 120/24 EVPN/SRv6 profile')
    output.mkdir(parents=True,exist_ok=True)
    session_id=state.get('session')
    result={'run_id':run_id,'session':session_id,'started_at':time.time(),'success':False,'phase':'observing',
            'configuration':config,'failures':[]}
    atomic(output/'result.json',result)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            l3=pool.submit(capacity,run_id,24,600,600,60,output/'full-scale')
            l2=pool.submit(services,run_id,output/'services')
            traffic,systems=l3.result(),l2.result()
        result['stability_passed']=traffic['result']=='passed' and systems['result']=='passed'
        result['realtime_passed']=traffic.get('realtime_passed',False)
        if not result['stability_passed']:
            raise RuntimeError('stability acceptance failed; see full-scale and services reports')
        result['phase']='faults';atomic(output/'result.json',result)
        faults(run_id,output/'faults')
        result['faults_passed']=read(output/'faults/result.json').get('success',False)
        result['phase']='radio-faults';atomic(output/'result.json',result)
        for direction in ('uplink','downlink'):
            subprocess.run([sys.executable,str(ROOT/'ntn/single-pc/check_radio_recovery.py'),
                            '--run-id',run_id,'--direction',direction,
                            '--output',str(output/'radio-faults'/direction)],check=True,timeout=180)
        result['radio_faults_passed']=all(read(output/'radio-faults'/d/'result.json').get('success',False)
                                          for d in ('uplink','downlink'))
        if not result['realtime_passed']:
            result['failures'].append('10-second dynamic update criterion failed')
    except Exception as error:
        result['failures'].append(str(error))
    finally:
        result['phase']='cleaning';atomic(output/'result.json',result)
        try:
            state=service.status()
            if state.get('run_id')==run_id and state.get('session')==session_id and state.get('active'):
                # Runtime may have failed while its wrapper is already in the
                # EXIT cleanup trap. A second SIGTERM can interrupt 5G teardown.
                if state.get('phase') not in ('stopping','stopped','failed'):
                    service.stop()
                deadline=time.monotonic()+300
                while (state:=service.status()).get('session')==session_id and state.get('active') and time.monotonic()<deadline:
                    time.sleep(3)
            state=service.status()
            if state.get('run_id')==run_id and state.get('active'):
                raise RuntimeError('owned runtime cleanup timeout')
            session=read(ROOT/'lab/unified/artifacts'/run_id/'live-session.json')
            result['cleanup']=session
            if not session or session.get('cleanup_errors'):
                raise RuntimeError('runtime cleanup report missing or contains errors')
            filters=['label=starfabric.run='+run_id]
            if state.get('run_id')==run_id and state.get('session')==session_id:
                filters.append('label=com.docker.compose.project=starfabric-5g')
            for selector in filters:
                left=subprocess.run(['docker','ps','-a','-q','--filter',selector],check=True,capture_output=True,text=True).stdout.split()
                if left: raise RuntimeError('owned containers remain after cleanup: '+selector)
            result['cleanup_passed']=True
        except Exception as error:
            result['failures'].append(str(error))
        result.update(phase='finished',finished_at=time.time())
        result['success']=bool(result.get('stability_passed') and result.get('faults_passed') and
                               result.get('radio_faults_passed') and result.get('cleanup_passed') and not result['failures'])
        atomic(output/'result.json',result)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    return result['success']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--session',help='require the matching launch session before acceptance')
    args=parser.parse_args()
    raise SystemExit(0 if run(args.run_id,args.output,args.session) else 1)
