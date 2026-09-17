#!/usr/bin/env python3
"""Real packet checks in two container-owned namespaces using compiled physics.

Run through make test-physical-packets. This is a channel contract test, not a
claim that every node in a constellation has been deployed. Replay validation
on the whole FRR/5G platform is recorded separately by PhysicalReplay.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'lab/platform')]
from channel import qdisc_command
from tools.physical_constellation import capacity


def run(*args, check=True, timeout=30):
    return subprocess.run(args,check=check,capture_output=True,text=True,timeout=timeout)


def net(name,*args,**kwargs):
    return run('ip','netns','exec',name,*args,**kwargs)


def ping():
    result=net('sf-physical-a','ping','-n','-c','12','-i','0.05','-W','1','10.254.0.2',check=False)
    samples=[float(v) for v in re.findall(r'time[=<]([\d.]+)',result.stdout)]
    return {'exit_code':result.returncode,'output':result.stdout,'median_ms':statistics.median(samples) if samples else None,'received':len(samples)}


def transfer():
    server='''import socket
s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(("10.254.0.2",19099));s.listen(1)
c,_=s.accept();total=0
while True:
 data=c.recv(65536)
 if not data:break
 total+=len(data)
c.sendall(str(total).encode());c.close();s.close()
'''
    proc=subprocess.Popen(['ip','netns','exec','sf-physical-b','python3','-c',server],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        time.sleep(.15)
        client='''import json,socket,time
s=socket.create_connection(("10.254.0.2",19099),10);s.settimeout(30)
data=b"s"*(1024*1024);started=time.monotonic();s.sendall(data);s.shutdown(socket.SHUT_WR)
received=s.recv(100);elapsed=time.monotonic()-started;s.close()
print(json.dumps({"bytes":int(received),"elapsed_seconds":elapsed,"bps":len(data)*8/elapsed}))
'''
        result=json.loads(net('sf-physical-a','python3','-c',client).stdout)
        if result['bytes']!=1024*1024:raise AssertionError('incomplete transfer')
        proc.wait(timeout=5)
        if proc.returncode:raise RuntimeError(proc.stderr.read())
        return result
    finally:
        if proc.poll() is None:proc.kill();proc.wait()


def apply(link):
    for ns,iface in [('sf-physical-a','phy0'),('sf-physical-b','phy1')]:
        net(ns,*qdisc_command(iface,link,10000))
        net(ns,'ip','link','set',iface,'up' if link['operational_up'] else 'down')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); args.output.parent.mkdir(parents=True,exist_ok=True)
    model=json.loads(args.scenario.read_text())['physical_model']
    frames=model['frames']; sample=next(l for l in frames[0]['links'] if l['link_type']=='feeder' and l['operational_up'])
    occurrences=[(f,l) for f in frames for l in f['links'] if l['id']==sample['id'] and l['operational_up']]
    far_frame,far=max(occurrences,key=lambda v:v[1]['range_km'])
    near_frame,near=min(occurrences,key=lambda v:v[1]['range_km'])
    report={'success':False,'scope':'real packet channel test using per-satellite physical frame values and explicit low-rate budget variants',
            'scenario':str(args.scenario),'input_sha256':hashlib.sha256(args.scenario.read_bytes()).hexdigest(),
            'source_link':sample['id'],'checks':{},'phases':[]}
    capture=None
    try:
        for name in ['sf-physical-a','sf-physical-b']:
            run('ip','netns','add',name);net(name,'ip','link','set','lo','up')
        run('ip','link','add','phy0','type','veth','peer','name','phy1')
        for name,iface,address in [('sf-physical-a','phy0','10.254.0.1/30'),('sf-physical-b','phy1','10.254.0.2/30')]:
            run('ip','link','set',iface,'netns',name)
            net(name,'ip','address','add',address,'dev',iface)
            net(name,'ip','link','set',iface,'up')
            net(name,'ethtool','-K',iface,'tso','off','gso','off','gro','off')
        pcap=args.output.with_suffix('.pcap')
        capture=subprocess.Popen(['ip','netns','exec','sf-physical-a','tcpdump','-U','-n','-i','phy0','-w',str(pcap)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        time.sleep(.2)
        if capture.poll() is not None:raise RuntimeError(capture.stderr.read().decode())
        baseline=ping();assert baseline['received']==12,baseline
        report['baseline']=baseline
        for name,frame,link,limit in [('near-low-rate',near_frame,near,2000000),('far-high-rate',far_frame,far,8000000)]:
            budget=dict(model['config']['links']['feeder'],max_capacity_bps=limit)
            actual=dict(link,capacity_bps=capacity(link['range_km'],budget),loss_ppm=0)
            apply(actual);observed=ping();throughput=transfer()
            expected=2*actual['latency_us']/1000
            assert observed['received']==12 and abs(observed['median_ms']-expected)<max(2,expected*.25),(observed,expected)
            assert actual['capacity_bps']*.55<throughput['bps']<actual['capacity_bps']*1.25,(actual,throughput)
            qdisc=json.loads(net('sf-physical-a','tc','-j','-s','qdisc','show','dev','phy0').stdout)
            report['phases'].append({'name':name,'physical_at':frame['at'],'budget_override':budget,'model_link':actual,
                                     'expected_rtt_ms':expected,'ping':observed,'transfer':throughput,'qdisc':qdisc})
        assert report['phases'][1]['transfer']['bps']>report['phases'][0]['transfer']['bps']*1.8
        # Use a real computed withdrawal/acquisition interval from the fleet.
        transition=None
        for previous,current in zip(frames,frames[1:]):
            active={l['id']:l for l in previous['links'] if l['operational_up']}
            next_active={l['id'] for l in current['links'] if l['operational_up']}
            lost=set(active)-next_active
            if lost:
                identifier=sorted(lost)[0];transition=(previous,current,active[identifier]);break
        if transition is None:raise AssertionError('scenario has no physical contact withdrawal to test')
        previous,current,link=transition
        apply(dict(link,operational_up=False))
        failed=ping();assert failed['received']==0,failed
        report['withdrawal']={'link':link['id'],'before_physical_at':previous['at'],'after_physical_at':current['at'],'ping':failed}
        # Packet loss is an explicit configured impairment, not inferred from Doppler.
        apply(dict(sample,loss_ppm=1000000));lost=ping();assert lost['received']==0,lost
        apply(sample);recovered=ping();assert recovered['received']==12,recovered
        report['checks']={'propagation_delay_visible_in_rtt':True,'computed_capacity_limits_transfer':True,
                          'physical_contact_withdrawal_blocks_packets':True,'configured_packet_loss_blocks_packets':True,
                          'restored_channel_forwards_packets':True}
        report['success']=True
    except Exception as error:
        report['error']=f'{type(error).__name__}: {error}'
        raise
    finally:
        if capture is not None:
            capture.send_signal(signal.SIGINT);capture.wait(timeout=5)
        errors=[]
        for name in ['sf-physical-a','sf-physical-b']:
            result=run('ip','netns','del',name,check=False)
            if result.returncode and 'No such file' not in result.stderr:errors.append(result.stderr)
        report['cleanup_errors']=errors
        if errors:report['success']=False
        args.output.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({k:v for k,v in report.items() if k not in ('phases','baseline','withdrawal')}),flush=True)


if __name__=='__main__':main()
