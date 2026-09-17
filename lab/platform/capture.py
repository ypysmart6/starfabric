"""Capture owned satellite namespaces with one supervised, temporary container."""
import ctypes,json,os,signal,subprocess,sys,time
from pathlib import Path
stop=False
children=[]
def stopping(*_):
    global stop
    stop=True
signal.signal(signal.SIGINT,stopping)
signal.signal(signal.SIGTERM,stopping)
parent_pid=os.getpid()
def parent_lifetime():
    ctypes.CDLL(None).prctl(1,signal.SIGKILL)
    if os.getppid()!=parent_pid:os.kill(os.getpid(),signal.SIGKILL)
config=json.loads(Path(sys.argv[1]).read_text()); directory=Path('/evidence'); profile=sys.argv[2]
try:
    for node,pid in config.items():
        log=(directory/f'{profile}-{node}-tcpdump.log').open('w')
        child=subprocess.Popen(['nsenter','-t',str(pid),'-n','--','tcpdump','-U','-n','-i','any','-s','0','-w',str(directory/f'{profile}-{node}.pcap')],stdout=log,stderr=log,preexec_fn=parent_lifetime)
        children.append(child);log.close()
    time.sleep(1)
    assert all(p.poll() is None for p in children),'capture subprocess exited'
    (directory/f'{profile}-capture-ready.json').write_text(json.dumps({'satellites':list(config),'ready':True}))
    while not stop:
        assert all(p.poll() is None for p in children),'capture subprocess exited'
        time.sleep(.2)
finally:
    for child in children:
        if child.poll() is None:child.send_signal(signal.SIGINT)
    for child in children:
        try:child.wait(timeout=10)
        except subprocess.TimeoutExpired:child.kill();child.wait()
