#!/usr/bin/env python3
"""Reproduce source filtering on a service return path in owned namespaces.

Run inside an isolated privileged test container, never in the host namespace.
The receiving node can reach the packet destination but has no source-prefix
route, as can happen on an independently selected service return path.
"""
import argparse
import json
import subprocess
import time
from pathlib import Path


def run(*args,check=True):
    return subprocess.run(args,check=check,capture_output=True,text=True,timeout=10)


def net(name,*args,**kwargs):return run('ip','netns','exec',name,*args,**kwargs)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    report={'success':False,'scope':'kernel packet regression for interface reverse-path filtering; no constellation deployment claim','phases':[]}
    created=[]
    try:
        for name in ['sf-rpf-a','sf-rpf-b']:
            run('ip','netns','add',name);created.append(name)
            net(name,'ip','link','set','lo','up')
            net(name,'sysctl','-qw','net.ipv4.conf.all.rp_filter=0')
        run('ip','link','add','rpf0','type','veth','peer','name','rpf1')
        for name,iface,address in [('sf-rpf-a','rpf0','10.254.0.1/30'),('sf-rpf-b','rpf1','10.254.0.2/30')]:
            run('ip','link','set',iface,'netns',name)
            net(name,'ip','address','add',address,'dev',iface)
            net(name,'ip','link','set',iface,'up')
        net('sf-rpf-a','ip','address','add','198.51.100.1/32','dev','lo')
        missing=net('sf-rpf-b','ip','route','get','198.51.100.1',check=False)
        assert missing.returncode!=0,missing.stdout
        report['source_route_lookup']=missing.stderr
        receiver='''import socket,json
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(("10.254.0.2",19099));s.settimeout(1)
try: result=s.recv(100).decode()
except socket.timeout: result=None
print(json.dumps({"received":result}));s.close()
'''
        sender='''import socket
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(("198.51.100.1",0));s.sendto(b"physical-service-return",("10.254.0.2",19099));s.close()
'''
        for value in [2,0]:
            net('sf-rpf-b','sysctl','-qw',f'net.ipv4.conf.rpf1.rp_filter={value}')
            child=subprocess.Popen(['ip','netns','exec','sf-rpf-b','python3','-c',receiver],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                time.sleep(.15);net('sf-rpf-a','python3','-c',sender)
                stdout,stderr=child.communicate(timeout=3)
                assert child.returncode==0,stderr
                result=json.loads(stdout)
                assert result['received']==(None if value else 'physical-service-return'),result
                report['phases'].append({'all_rp_filter':0,'interface_rp_filter':value,**result})
            finally:
                if child.poll() is None:child.kill();child.wait()
        report['success']=True
    finally:
        for name in reversed(created):run('ip','netns','delete',name)
        report['remaining_namespaces']=run('ip','netns','list').stdout.splitlines()
        if report['remaining_namespaces']:report['success']=False
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
    if not report['success']:raise SystemExit(1)


if __name__=='__main__':main()
