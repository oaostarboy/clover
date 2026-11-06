"""Exercise main's real recovery callback and receipt gate without a hosted run."""
import json
import os
from pathlib import Path
import sys
from unittest.mock import Mock, patch

import pytest
import interrupted_controller as controller
import interrupted_identity
import interrupted_source
import interrupted_utility_binding as utilities


def test_main_runs_chat_before_accounting_and_does_not_write_success_on_refusal(tmp_path):
    runner = tmp_path / 'runner'; runner.mkdir()
    home = tmp_path / 'home'; home.mkdir()
    target = home / 'clover-c1'; target.mkdir()
    evidence = runner / 'evidence'; evidence.mkdir()
    stage = runner / 'trusted-stage'
    runtime = runner / 'trusted-runtime/bin/python'
    (evidence / 'before-state.json').write_text(json.dumps({'pid':42,'start_time':123}))
    old = Mock()
    old.cmdline.return_value = ['python', 'run']
    old.environ.return_value = {'CLOVER_HOME': str(home)}
    old.exe.return_value = str((target / 'venv/bin/python').resolve())
    old.uids.return_value.real = os.getuid()
    old.status.return_value = 'running'
    args = ['controller']
    for name, value in {'stage': stage, 'runtime':runtime, 'home':home, 'target':target,
                        'evidence':evidence, 'old':'old', 'commit':'commit', 'tree':'tree',
                        'dependencies':tmp_path / 'deps', 'python':sys.executable,
                        'recovery-commit':'new'}.items():
        args += ['--' + name, str(value)]
    events = []
    def observed(binary, argv, env, home, cwd, prefix, fds, inject, recover):
        return recover(object(), {'watchers': {'rollback': {'pid': 43}}})
    def chat(*args):
        events.append('chat')
        return {'chat': True}
    def accounting(*args, **kwargs):
        events.append('accounting')
        raise controller.Refusal('duplicate launch')
    with patch.object(sys, 'argv', args), patch.dict(os.environ, {'RUNNER_TEMP': str(runner)}), \
         patch.object(controller, 'hosted_preflight'), patch('psutil.Process', return_value=old), \
         patch.object(interrupted_identity, 'birth', return_value=123), \
         patch.object(interrupted_source, 'pinned_hash', return_value='hash'), \
         patch('shutil.which', return_value='/fixture/strace'), \
         patch.object(utilities, 'preflight', return_value={}), \
         patch.object(utilities, 'pin_helpers', return_value={}), \
         patch.object(utilities, 'check_helpers'), patch.object(utilities, 'bind_rules', return_value=[]), \
         patch.object(controller, 'verify_recovery', side_effect=chat), \
         patch.object(controller, 'finish_accounting', side_effect=accounting), \
         patch.object(controller, 'run_observed_recovery', side_effect=observed), \
         patch.object(controller, 'bind_original_pidfd', side_effect=lambda *args: os.open('/dev/null', os.O_RDONLY)):
        with pytest.raises(controller.Refusal, match='duplicate launch'):
            controller.main()
    assert events == ['chat', 'accounting']
    assert not (evidence / 'interrupted-observed.json').exists()
    assert json.loads((evidence / 'interrupted-fault-failure.json').read_text())['detail'] == 'duplicate launch'
