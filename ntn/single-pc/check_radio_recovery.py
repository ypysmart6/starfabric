#!/usr/bin/env python3
"""Interrupt only an owned UERANSIM radio link; require the same PDU to recover."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from lab.live.check_evpn_system import pdu, read
from lab.live.storage import atomic


def cmd(*args,check=True):
    return subprocess.run(['docker',*args],capture_output=True,text=True,check=check,timeout=30)


def run(output,run_id=None,direction='uplink'):
    output.mkdir(parents=True,exist_ok=True)
    if run_id:
        state=read(ROOT/'reports/live/status.json')
        if state.get('run_id')!=run_id or state.get('phase')!='running':
            raise ValueError('requested satellite runtime is not running')
    owned={name:json.loads(cmd('inspect',name).stdout)[0] for name in ('nr_ue','nr_gnb')}
    if any(v['Config']['Labels'].get('com.docker.compose.project')!='starfabric-5g' for v in owned.values()):
        raise ValueError('radio ownership check failed')
    before_logs=cmd('logs','nr_ue');before_text=before_logs.stdout+before_logs.stderr
    result={'success':False,'started_at':time.time(),'pdu_before':pdu(),
            'gnb_image':owned['nr_gnb']['Image'],'run_id':run_id,'direction':direction,
            'scope':'satellite N3 radio recovery' if run_id else 'radio recovery only, independent of satellite acceptance'}
    before_gnb=cmd('logs','nr_gnb');before_gnb_text=before_gnb.stdout+before_gnb.stderr
    if direction=='uplink':
        fault_node='nr_gnb';rule=['INPUT','-s','172.22.0.24','-p','udp','--dport','4997']
    elif direction=='downlink':
        # Drop at the receiving UE. OUTPUT DROP returns EPERM to the gNB's
        # sendto(), testing a process exception instead of radio packet loss.
        fault_node='nr_ue';rule=['INPUT','-s','172.22.0.23','-p','udp','--sport','4997']
    else: raise ValueError('invalid radio fault direction')
    result['fault_container']=fault_node
    rule+=['-m','comment','--comment','sf-radio-check-'+uuid.uuid4().hex[:10],'-j','DROP']
    installed=False
    def ping(): return cmd('exec','nr_ue','ping','-n','-I','uesimtun0','-c','1','-W','1','192.168.100.1',check=False)
    try:
        if ping().returncode: raise AssertionError('baseline UE ping failed')
        cmd('exec',fault_node,'iptables','-I',*rule);installed=True
        time.sleep(8)
        blocked=ping();result['blocked_ping']=blocked.stdout
        if blocked.returncode==0: raise AssertionError('radio fault did not interrupt UE traffic')
        cmd('exec',fault_node,'iptables','-D',*rule);installed=False
        restored=time.monotonic();deadline=restored+60
        while ping().returncode:
            if time.monotonic()>deadline: raise AssertionError('radio did not recover within 60 seconds')
            time.sleep(1)
        result['recovery_seconds']=time.monotonic()-restored
        result['pdu_after']=pdu()
        log=cmd('logs','nr_ue');text=log.stdout+log.stderr
        if text.count('Radio link failure detected')<=before_text.count('Radio link failure detected'):
            raise AssertionError('test did not trigger an actual radio link failure')
        if direction=='downlink':
            gnb=cmd('logs','nr_gnb');gnb_text=gnb.stdout+gnb.stderr
            marker='Releasing stale access context before RRC Setup'
            if gnb_text.count(marker)<=before_gnb_text.count(marker):
                raise AssertionError('test did not exercise UE-only loss with a stale gNB context')
        if result['pdu_before']!=result['pdu_after']: raise AssertionError('PDU was recreated')
        result['same_pdu_session']=True;result['success']=True
    except Exception as error:
        result['error']=str(error)
    finally:
        if installed:
            current=json.loads(cmd('inspect',fault_node).stdout)[0]
            if current['Id']==owned[fault_node]['Id']: cmd('exec',fault_node,'iptables','-D',*rule)
        for node in ('nr_ue','nr_gnb','amf','smf'):
            log=cmd('logs',node,check=False);(output/(node+'.log')).write_text(log.stdout+log.stderr)
        result['finished_at']=time.time();atomic(output/'result.json',result)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    return result['success']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--run-id')
    parser.add_argument('--direction',choices=('uplink','downlink'),default='uplink')
    args=parser.parse_args();raise SystemExit(0 if run(args.output,args.run_id,args.direction) else 1)
