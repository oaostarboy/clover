"""Observation-only timing for installed-path Windows report diagnostics."""
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

import pytest

out = Path(os.environ['REPORT_TIMING_DIR']) / ('report-timing-%s.jsonl' % os.getpid())
write_lock = threading.Lock()

def record(**kw):
    kw.update(pid=os.getpid(), monotonic=time.monotonic(), thread=threading.get_ident())
    with write_lock, out.open('a', encoding='utf-8') as f:
        f.write(json.dumps(kw) + '\n')

def pytest_configure(config):
    from clover_state import SessionDB
    original = SessionDB._init_schema
    def measured(self, *args, **kwargs):
        started = time.monotonic()
        record(event='schema_start', object_id=id(self))
        stopped = threading.Event()
        def snapshot():
            if not stopped.wait(10):
                frames = {str(t): [{'file': Path(x.filename).name, 'line': x.lineno, 'function': x.name}
                                   for x in traceback.extract_stack(frame)[-15:]]
                          for t, frame in sys._current_frames().items()}
                record(event='slow_schema_threads', object_id=id(self), frames=frames)
        watcher = threading.Thread(target=snapshot, daemon=True)
        watcher.start()
        try:
            return original(self, *args, **kwargs)
        finally:
            stopped.set()
            record(event='schema_end', object_id=id(self), elapsed=time.monotonic()-started)
    SessionDB._init_schema = measured
    original_write = SessionDB._execute_write
    def measured_write(self, *args, **kwargs):
        started = time.monotonic()
        record(event='write_start', object_id=id(self))
        try:
            return original_write(self, *args, **kwargs)
        finally:
            record(event='write_end', object_id=id(self), elapsed=time.monotonic()-started)
    SessionDB._execute_write = measured_write

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    started = time.monotonic()
    record(event='setup_start', nodeid=item.nodeid)
    yield
    record(event='setup_end', nodeid=item.nodeid, elapsed=time.monotonic()-started)

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    started = time.monotonic()
    record(event='call_start', nodeid=item.nodeid)
    yield
    record(event='call_end', nodeid=item.nodeid, elapsed=time.monotonic()-started)
