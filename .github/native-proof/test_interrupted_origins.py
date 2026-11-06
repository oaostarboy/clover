import json
import os
from pathlib import Path
import runpy
import sys
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).parent / 'stage_independence_verify.py'


def test_final_successor_module_origins_are_checked(tmp_path):
    target = tmp_path / 'target'; target.mkdir()
    evidence = tmp_path / 'evidence'; evidence.mkdir()
    proc = tmp_path / 'proc/321'; proc.mkdir(parents=True)
    (proc / 'cmdline').write_bytes(str(target / 'venv/bin/python').encode() + b'\0run\0')
    state = tmp_path / 'after.json'; state.write_text(json.dumps({'pid':321}))
    record = {'pid':321, 'modules':{'__main__':str(target / 'clover_cli/main.py'), 'gateway.run':str(target / 'gateway/run.py')}}
    path = evidence / 'live-modules-321.json'
    path.write_text(json.dumps(record))
    real_path = Path
    def mapped(value):
        return tmp_path / 'proc' if value == '/proc' else real_path(value)
    def readlink(value):
        return str(target if str(value).endswith('/cwd') else target / 'venv/bin/python')
    argv = ['verify', 'identity', str(tmp_path), str(target), str(tmp_path / 'stage'), str(evidence), str(state)]
    with patch.object(sys, 'argv', argv), patch('pathlib.Path', side_effect=mapped), patch.object(os, 'readlink', side_effect=readlink):
        runpy.run_path(str(SCRIPT), run_name='__main__')
        record['modules']['gateway.run'] = str(tmp_path / 'foreign/run.py')
        path.write_text(json.dumps(record))
        with pytest.raises(AssertionError):
            runpy.run_path(str(SCRIPT), run_name='__main__')
