"""Fixture-only live process module-origin observer; not shipped in product."""
import json
import os
import sys
import threading
import time
from pathlib import Path


def observe():
    output = os.environ.get('STAGE_ORIGIN_EVIDENCE')
    if not output:
        return
    for _ in range(1200):
        names = ('__main__', 'gateway.run', 'gateway.status')
        modules = {name: sys.modules.get(name) for name in names}
        if modules['__main__'] and modules['gateway.run']:
            values = {name: getattr(module, '__file__', None) if module else None
                      for name, module in modules.items()}
            path = Path(output) / ('live-modules-%s.json' % os.getpid())
            tmp = path.with_suffix('.tmp')
            try:
                tmp.write_text(json.dumps({'pid': os.getpid(), 'modules': values}))
                tmp.replace(path)
            except OSError:
                pass
            return
        time.sleep(.1)


if os.environ.get('STAGE_ORIGIN_EVIDENCE'):
    threading.Thread(target=observe, daemon=True).start()
