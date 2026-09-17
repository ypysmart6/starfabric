#!/usr/bin/env python3
"""One helper enters owned router namespaces to deliver local Rust heartbeats."""
import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path


def main():
    request=json.loads(Path(sys.argv[1]).read_text())
    generation=request['generation']
    body=json.dumps({'connected':True,'generation':generation})
    def send(node):
        name,pid=node
        try:
            result=subprocess.run(['nsenter','-t',str(pid),'-n','--','curl','-sf','--max-time','2',
                '-H','Content-Type: application/json','--data-binary',body,
                'http://127.0.0.1:19080/v1/connectivity'],capture_output=True,text=True,timeout=4)
        except (subprocess.TimeoutExpired, OSError) as error:
            return name, {'ok':False,'error':str(error)[-200:]}
        return name, {'ok':result.returncode==0,'error':result.stderr[-200:] if result.returncode else None}
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        records=dict(pool.map(send,request['nodes'].items()))
    print(json.dumps({'generation':generation,'nodes':records}))


if __name__=='__main__':main()
