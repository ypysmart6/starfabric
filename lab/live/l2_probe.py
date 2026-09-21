"""Continuous IPv4/IPv6 CE probes; counters come only from real ping output."""
import asyncio
import json
from pathlib import Path
import re
import signal
import sys
import time
from storage import atomic


async def run(config):
    stopping=asyncio.Event(); states={}; children=[]
    for signum in (signal.SIGINT,signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(signum,stopping.set)
    async def probe(family,destination):
        state=states[str(family)]={'destination':destination,'transmitted':0,'received':0,'last_reply':None}
        child=await asyncio.create_subprocess_exec('ping','-'+str(family),'-n','-D','-O','-i','2','-W','1','-p',config['token'],destination,
              stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
        children.append(child)
        async for raw in child.stdout:
            line=raw.decode(errors='replace')
            match=re.search(r'icmp_seq=(\d+)',line)
            if match:
                state['transmitted']=max(state['transmitted'],int(match[1]))
                if 'time=' in line or 'time<' in line:
                    state['received']+=1; state['last_reply']=time.time()
            else: state['last_message']=line.strip()[-200:]
        state['error']='probe exited'; stopping.set()
    tasks=[asyncio.create_task(probe(f,config['ipv'+str(f)])) for f in (4,6)]
    try:
        while not stopping.is_set():
            atomic(Path(config['output']),{'run_id':config['run_id'],'observed_at':time.time(),'node':config['node'],
                                          'evi':config['evi'],'families':states})
            try: await asyncio.wait_for(stopping.wait(),1)
            except asyncio.TimeoutError: pass
    finally:
        for child in children:
            if child.returncode is None: child.terminate()
        await asyncio.gather(*tasks,return_exceptions=True)


if __name__=='__main__': asyncio.run(run(json.loads(Path(sys.argv[1]).read_text())))
