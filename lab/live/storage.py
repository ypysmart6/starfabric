"""Bounded live records and observed ICMP counters."""
import json
import logging
import re
import threading
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':'))+'\n')
    temporary.replace(path)


def rotating(path, size=2*1024*1024, backups=3):
    logger = logging.Logger(str(path))
    handler = RotatingFileHandler(path, maxBytes=size, backupCount=backups)
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)
    return logger


class PingCounters:
    def __init__(self):
        self.lock = threading.Lock()
        self.samples = deque(maxlen=1800)
        self.highest = self.received = 0

    def consume(self, line):
        match = re.search(r'icmp_seq=(\d+)', line)
        if not match:
            return
        with self.lock:
            seq = int(match[1])
            extended = (self.highest // 65536)*65536 + seq
            if extended < self.highest - 32768:
                extended += 65536
            elif extended > self.highest + 32768:
                extended -= 65536
            self.highest = max(self.highest, extended)
            reply = re.search(r'\[([\d.]+)\].*time[=<]([\d.]+)', line)
            if reply and 'DUP!' not in line:
                self.received += 1
                self.samples.append([float(reply[1]), extended, float(reply[2])])

    def snapshot(self):
        with self.lock:
            return {'phase': 'continuous_across_all_faults', 'transmitted': self.highest,
                    'received': self.received,
                    'loss_percent': max(0, (self.highest-self.received)*100/max(1,self.highest)),
                    'counter_scope': 'observed ICMP sequences; current in-flight packet may be pending'}, list(self.samples)
