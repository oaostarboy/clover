"""Staged code must identify processes by the selected installation runtime."""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from clover_cli import _migration_target as binding
from clover_cli import update_cmd


def _repo(path):
    path.mkdir()
    subprocess.run(['git', 'init', '-q', str(path)], check=True)
    subprocess.run(['git', '-C', str(path), 'config', 'user.email', 'test@example.invalid'], check=True)
    subprocess.run(['git', '-C', str(path), 'config', 'user.name', 'Test'], check=True)
    (path / 'pyproject.toml').write_text('[project]\nname="clover-c1"\nversion="0"\n')
    (path / 'clover_cli').mkdir()
    (path / 'clover_cli' / '__init__.py').write_text('')
    subprocess.run(['git', '-C', str(path), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(path), 'commit', '-qm', 'fixture'], check=True)
    (path / '.git' / 'info' / 'exclude').write_text('venv/\n')
    return subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX venv and proc fixture')
def test_staged_ownership_uses_target_not_controller(tmp_path, monkeypatch):
    stage, target, other = (tmp_path / n for n in ('stage', 'target', 'other'))
    stage_id, target_id = _repo(stage), _repo(target)
    for env in (stage / 'venv', target / 'venv', other):
        subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(env)], check=True)
    stage_python = stage / 'venv' / 'bin' / 'python'
    target_python = target / 'venv' / 'bin' / 'python'
    other_python = other / 'bin' / 'python'
    cmdline = tmp_path / 'cmdline'
    monkeypatch.setattr(update_cmd, '_gateway_pid_cmdline_path', lambda pid: cmdline)
    monkeypatch.setattr(sys, 'executable', str(stage_python))
    with binding.bind_target(stage, target, stage_id, target_id):
        for python, expected in ((target_python, 'own'), (target_python.parent / 'python3', 'own'), (stage_python, 'other'), (other_python, 'other')):
            cmdline.write_bytes(os.fsencode(python) + b'\0-m\0clover_cli.main\0gateway\0run\0')
            assert update_cmd._gateway_pid_install_verdict(123) == expected


@pytest.mark.skipif(os.name == 'nt', reason='POSIX venv fixture')
def test_staged_gateway_resolver_ignores_controller_environment(tmp_path, monkeypatch):
    from clover_cli import gateway
    stage, target = tmp_path / 'stage', tmp_path / 'target'
    stage_id, target_id = _repo(stage), _repo(target)
    for env in (stage / 'venv', target / 'venv'):
        subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(env)], check=True)
    monkeypatch.setattr(sys, 'prefix', str(stage / 'venv'))
    monkeypatch.setenv('VIRTUAL_ENV', str(stage / 'venv'))
    with binding.bind_target(stage, target, stage_id, target_id):
        assert gateway.get_python_path() == str(target / 'venv' / 'bin' / 'python')


@pytest.mark.skipif(os.name == 'nt', reason='POSIX systemd and venv fixture')
def test_bound_systemd_uses_target_without_merging_shared_base(tmp_path, monkeypatch):
    stage, target = tmp_path / 'stage', tmp_path / 'target'
    stage_id, target_id = _repo(stage), _repo(target)
    for env in (stage / 'venv', target / 'venv'):
        subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(env)], check=True)
    monkeypatch.setattr(sys, 'executable', str(stage / 'venv/bin/python'))
    with binding.bind_target(stage, target, stage_id, target_id):
        monkeypatch.setattr(update_cmd, 'get_clover_home', lambda: Path('/fixture/home'))
        def probe(python):
            venv = python.parent.parent
            text = (f'Id=clover-gateway.service\nWorkingDirectory=/fixture/home\n'
                    f'ExecStart={{ path={python} ; argv[]={python} -m clover_cli.main gateway run ; }}\n'
                    f'Environment=CLOVER_HOME=/fixture/home VIRTUAL_ENV={venv}\n')
            monkeypatch.setattr(update_cmd.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=text))
            return update_cmd._systemd_unit_owned_by_install(['systemctl', '--user'], 'clover-gateway.service')
        assert probe(target / 'venv/bin/python')
        assert not probe(stage / 'venv/bin/python')


def test_bound_runtime_refuses_missing_or_ambiguous_venv(tmp_path):
    stage, target = tmp_path / 'stage', tmp_path / 'target'
    stage_id, target_id = _repo(stage), _repo(target)
    with binding.bind_target(stage, target, stage_id, target_id):
        with pytest.raises(ValueError, match='exactly one'):
            binding.target_python()
        (target / 'venv').mkdir()
        (target / '.venv').mkdir()
        with pytest.raises(ValueError, match='exactly one'):
            binding.target_python()


def test_recovery_uses_real_subprocess_input_contract(monkeypatch):
    real_run = subprocess.run
    monkeypatch.setattr(update_cmd, "_gateway_recovery_partition", lambda *a, **k: ({"demo": "systemd"}, []))
    def run_echo(command, **kwargs):
        return real_run([sys.executable, "-c", "import json,sys; d=json.load(sys.stdin); print(json.dumps(dict(verified=[], relaunch_attempted=d.get('profiles'), failed=[])))"], **kwargs)
    monkeypatch.setattr(update_cmd.subprocess, "run", run_echo)
    result = update_cmd._recover_gateway_restart_after_abort(None, gateway_mode=False)
    assert result["relaunch_attempted"] == ["demo"]


def test_bound_recovery_uses_target_cwd_and_scrubs_python_environment(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setattr(binding, "target_root", lambda: target)
    monkeypatch.setattr(binding, "target_python", lambda: Path(sys.executable))
    monkeypatch.setattr(binding, "validate_recovery_source", lambda: None, raising=False)
    monkeypatch.setattr(update_cmd, "_gateway_recovery_partition", lambda *a, **k: ({"demo": "systemd"}, []))
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        monkeypatch.setenv(name, "contaminated")
    captured = {}
    def fake_run(command, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout='{"verified": [], "relaunch_attempted": ["demo"], "failed": []}')
    monkeypatch.setattr(update_cmd.subprocess, "run", fake_run)
    update_cmd._recover_gateway_restart_after_abort(None, gateway_mode=False)
    assert captured["cwd"] == target
    assert all(name not in captured["env"] for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX venv fixture and proc layout")
def test_bound_recovery_runs_target_module_in_real_child(tmp_path, monkeypatch):
    target, stage = tmp_path / "target", tmp_path / "stage"
    _repo(target)
    stage.mkdir()
    (target / "clover_cli/update_restart_recovery.py").write_text(
        "import json,os,sys\n"
        "d=json.load(sys.stdin)\n"
        "assert os.path.realpath(sys.prefix) == os.path.realpath(os.path.join(os.getcwd(), 'venv'))\n"
        "assert all(k not in os.environ for k in ('PYTHONPATH','PYTHONHOME','VIRTUAL_ENV'))\n"
        "print(json.dumps(dict(verified=[],relaunch_attempted=d['profiles'],failed=[])))\n")
    subprocess.run(['git', '-C', str(target), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(target), 'commit', '-qm', 'recovery'], check=True)
    commit = subprocess.check_output(['git', '-C', str(target), 'rev-parse', 'HEAD'], text=True).strip()
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(target / 'venv')], check=True)
    monkeypatch.setattr(binding, '_target', target)
    monkeypatch.setattr(binding, '_identities', (stage, target, commit, commit))
    monkeypatch.setattr(binding, '_prepared_commit', commit)
    monkeypatch.setattr(update_cmd, '_gateway_recovery_partition', lambda *a, **k: ({'demo': 'systemd'}, []))
    monkeypatch.chdir(stage)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'):
        monkeypatch.setenv(key, str(stage))
    result = update_cmd._recover_gateway_restart_after_abort(None, gateway_mode=False)
    assert result['relaunch_attempted'] == ['demo']
    assert result['verified'] == []
    (target / 'clover_cli/update_restart_recovery.py').write_text('raise AssertionError()')
    result = update_cmd._recover_gateway_restart_after_abort(None, gateway_mode=False)
    assert result['failed'] == ['demo']


@pytest.mark.skipif(os.name == 'nt', reason='symlink fixture')
def test_bound_runtime_refuses_external_symlink_and_bad_metadata(tmp_path, monkeypatch):
    target = tmp_path / 'target'
    target.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (target / 'venv').symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(binding, '_target', target)
    with pytest.raises(ValueError, match='physical'):
        binding.target_python()
    (target / 'venv').unlink()
    (target / 'venv').mkdir()
    with pytest.raises(ValueError, match='metadata'):
        binding.target_python()


def test_windows_runtime_resolver_fixture(tmp_path, monkeypatch):
    target = tmp_path / 'target'
    scripts = target / 'venv' / 'Scripts'
    scripts.mkdir(parents=True)
    (target / 'venv' / 'pyvenv.cfg').write_text('home = fixture')
    (scripts / 'python.exe').write_text('fixture')
    monkeypatch.setattr(binding, '_target', target)
    monkeypatch.setattr(binding, 'os', SimpleNamespace(name='nt'))
    assert binding.target_python() == scripts / 'python.exe'


def test_unbound_runtime_is_none(monkeypatch):
    monkeypatch.setattr(binding, '_target', None)
    assert binding.target_python() is None


def test_purge_retains_binding_and_reloads_gateway_for_target(tmp_path):
    script = """import sys
from pathlib import Path
from clover_cli import _migration_target as binding, update_cmd
import clover_cli.gateway
binding._target = Path(sys.argv[1])
update_cmd._purge_stale_clover_modules()
import importlib
assert importlib.import_module('clover_cli._migration_target') is binding
gateway = importlib.import_module('clover_cli.gateway')
assert gateway.PROJECT_ROOT == binding.target_root()
"""
    subprocess.run([sys.executable, '-c', script, str(tmp_path)], check=True)


def test_bound_desktop_build_uses_target_runtime_and_clean_environment(tmp_path, monkeypatch):
    desktop = tmp_path / 'apps/desktop'
    desktop.mkdir(parents=True)
    (desktop / 'package.json').write_text('{}')
    monkeypatch.setattr(binding, 'target_root', lambda: tmp_path)
    monkeypatch.setattr(binding, 'target_python', lambda: tmp_path / 'venv/bin/python')
    monkeypatch.setattr(update_cmd, '_should_auto_build_desktop_on_update', lambda: True)
    monkeypatch.setattr(update_cmd, '_desktop_app_present', lambda _: False)
    calls = []
    class Main:
        PROJECT_ROOT = tmp_path
        _resolve_node_runtime_npm = staticmethod(lambda: 'npm')
        _desktop_build_needed = staticmethod(lambda *a, **k: True)
        _register_desktop_launchers = staticmethod(lambda *_: None)
        _run_logged_subprocess = staticmethod(lambda cmd, **kw: calls.append((cmd, kw)) or SimpleNamespace(returncode=0, stdout=''))
    monkeypatch.setattr(update_cmd, '_m', lambda: Main)
    monkeypatch.setattr('clover_constants.with_clover_node_path', lambda: {'PYTHONPATH':'stage', 'PYTHONHOME':'stage', 'VIRTUAL_ENV':'stage', 'PATH':'ok'})
    assert update_cmd._rebuild_desktop_after_update(desktop, had_desktop_app_before_update=False)
    cmd, kwargs = calls[0]
    assert cmd[0] == str(tmp_path / 'venv/bin/python')
    assert kwargs['cwd'] == tmp_path
    assert kwargs['env'] == {'PATH':'ok'}


def test_gateway_interpreter_failure_is_fail_closed_only_when_bound(monkeypatch, tmp_path):
    from clover_cli import gateway
    def fail():
        raise ValueError('bad target')
    monkeypatch.setattr(gateway, 'get_python_path', fail)
    monkeypatch.setattr(binding, 'target_root', lambda: tmp_path)
    argv = ['python', '-m', 'clover_cli.main']
    with pytest.raises(ValueError, match='bad target'):
        update_cmd._with_current_gateway_interpreter(argv)
    monkeypatch.setattr(binding, 'target_root', lambda: None)
    assert update_cmd._with_current_gateway_interpreter(argv) == argv
