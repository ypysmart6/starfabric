#!/usr/bin/env python3
"""Cold-start and fully accept one owned 120/24 EVPN/SRv6 run, then clean up."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from lab.live.launch import DEFAULT, validate
from lab.live.service import Service, read
from lab.live.storage import atomic

CONFIGURATION={**DEFAULT,'copies_per_profile':3}


def cleanup_launch(service,session_id,run_id,timeout=300):
    """Stop only the launch we started; never interrupt an existing EXIT trap."""
    state=service.status()
    if state.get('session')!=session_id:
        raise RuntimeError('launch session changed; cleanup will not touch the replacement')
    if state.get('active') and state.get('phase') not in ('stopping','stopped','failed'):
        service.stop()
    deadline=time.monotonic()+timeout
    while state.get('active') and time.monotonic()<deadline:
        time.sleep(3)
        state=service.status()
        if state.get('session')!=session_id:
            raise RuntimeError('launch session changed during cleanup')
    if state.get('active'):
        raise RuntimeError('owned launch cleanup timeout')
    run_id=run_id or state.get('run_id')
    selectors=['label=com.docker.compose.project=starfabric-5g']
    if run_id:
        selectors.append('label=starfabric.run='+run_id)
    for selector in selectors:
        left=subprocess.run(['docker','ps','-a','-q','--filter',selector],check=True,
                            capture_output=True,text=True,timeout=30).stdout.split()
        if left:
            raise RuntimeError('owned containers remain after cleanup: '+selector)
    report=read(ROOT/'lab/unified/artifacts'/run_id/'live-session.json') if run_id else {}
    if run_id and (not report or report.get('cleanup_errors')):
        raise RuntimeError('runtime cleanup report missing or contains errors')
    return {'session':session_id,'run_id':run_id,'success':True,'runtime':report,
            'wrapper_exit_code':state.get('wrapper_exit_code')}


def accept(run_id,session_id,output,timeout):
    process=subprocess.Popen([sys.executable,str(ROOT/'lab/live/check_evpn_complete.py'),
        '--run-id',run_id,'--session',session_id,'--output',str(output)],start_new_session=True)
    try:
        return process.wait(timeout=timeout)
    except BaseException:
        # Include the radio-check child if acceptance times out or is cancelled.
        if process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL)
                process.wait(timeout=30)
        raise


def run(configuration,output,startup_timeout=1200,acceptance_timeout=4200):
    configuration=validate(configuration)
    required={'satellites':120,'gateways':24,'carriers':['evpn-srv6'],
              'copies_per_profile':3,'step_seconds':10}
    if any(configuration[key]!=value for key,value in required.items()):
        raise ValueError('full acceptance requires 120 satellites, 24 gateways, EVPN/SRv6, 3 copies and 10-second steps')
    output=Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('use a new empty output directory to preserve previous evidence')
    output.mkdir(parents=True,exist_ok=True)
    atomic(output/'launch.json',configuration)
    result={'started_at':time.time(),'configuration':configuration,'phase':'starting',
            'success':False,'failures':[]}
    session_id=run_id=None
    service=Service()
    atomic(output/'launch-result.json',result)
    try:
        if service.status().get('active'):
            raise RuntimeError('a live service is already active; no new launch was started')
        state=service.start(configuration)
        session_id=state.get('session')
        if not session_id:
            raise RuntimeError('launcher did not return an owned session')
        result['session']=session_id
        atomic(output/'start.json',state)
        deadline=time.monotonic()+startup_timeout
        last_print=0
        while True:
            if state.get('session')!=session_id:
                raise RuntimeError('launch session changed while waiting for startup')
            if state.get('run_id'):
                run_id=state['run_id']; result['run_id']=run_id
            if not state.get('active') or state.get('phase') in ('failed','stopped','interrupted','stopping'):
                raise RuntimeError('owned startup failed: '+str(state.get('error') or state.get('phase')))
            if run_id and state.get('phase')=='running':
                break
            if time.monotonic()>=deadline:
                raise TimeoutError(f'owned startup did not reach running within {startup_timeout}s')
            result['runtime_phase']=state.get('phase')
            atomic(output/'launch-result.json',result)
            if time.monotonic()-last_print>=30:
                print(json.dumps({'session':session_id,'run_id':run_id,'phase':state.get('phase')}),flush=True)
                last_print=time.monotonic()
            time.sleep(3)
            state=service.status()
        (output/'run-id.txt').write_text(run_id+'\n')
        result['phase']='accepting'; atomic(output/'launch-result.json',result)
        exit_code=accept(run_id,session_id,output,acceptance_timeout)
        acceptance=read(output/'result.json')
        result['acceptance_exit_code']=exit_code
        result['acceptance_passed']=bool(exit_code==0 and acceptance.get('run_id')==run_id
            and acceptance.get('session')==session_id and acceptance.get('success'))
        if not result['acceptance_passed']:
            result['failures'].extend(acceptance.get('failures') or ['complete acceptance failed or report missing'])
    except (Exception,KeyboardInterrupt) as error:
        result['failures'].append(str(error) or 'acceptance interrupted')
    finally:
        result['phase']='cleaning'; atomic(output/'launch-result.json',result)
        if session_id is not None:
            try:
                result['cleanup']=cleanup_launch(service,session_id,run_id)
            except Exception as error:
                result['failures'].append(str(error))
        result.update(phase='finished',finished_at=time.time())
        result['success']=bool(result.get('acceptance_passed') and
                               result.get('cleanup',{}).get('success') and not result['failures'])
        atomic(output/'launch-result.json',result)
        if not (output/'result.json').exists():
            atomic(output/'result.json',result)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,help='optional 120/24 acceptance launch configuration')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/evpn-srv6-complete'/
                        datetime.now(timezone.utc).strftime('run-%Y%m%dT%H%M%S.%fZ'))
    parser.add_argument('--startup-timeout',type=int,default=1200)
    parser.add_argument('--acceptance-timeout',type=int,default=4200)
    args=parser.parse_args(argv)
    if args.startup_timeout<1 or args.acceptance_timeout<1:
        parser.error('timeouts must be positive')
    configuration=json.loads(args.config.read_text()) if args.config else CONFIGURATION
    return 0 if run(configuration,args.output,args.startup_timeout,args.acceptance_timeout)['success'] else 1


if __name__=='__main__':
    def interrupted(*_):
        raise KeyboardInterrupt('acceptance interrupted by SIGTERM')
    signal.signal(signal.SIGTERM,interrupted)
    raise SystemExit(main())
