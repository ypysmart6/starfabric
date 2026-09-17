#!/usr/bin/env python3
import json
import time
from datetime import datetime
from pathlib import Path
import sys


timing = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
end = datetime.fromisoformat(timing["wall_clock_contact_end"].replace("Z", "+00:00")).timestamp()
delay = end - time.time()
if delay > 0:
    time.sleep(delay)
