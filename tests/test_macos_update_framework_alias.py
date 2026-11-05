"""Framework argv aliases require live launchd and process attestation."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import os
import plistlib

import pytest
import psutil
from clover_cli import macos_update_launchd as m
from clover_cli.macos_update_lifecycle import LifecycleRefused
from tests.test_macos_update_launchd import rig, generated


@pytest.mark.parametrize('mutation', ['ok', 'foreign_venv', 'loaded_args', 'program', 'pid',
    'nested_only', 'nested_pid', 'duplicate', 'uid', 'exe', 'birth', 'birth_after_print', 'child_parent', 'profile',
    'cwd', 'plist', 'loaded_plist', 'non_darwin', 'virtual_env'])
def test_framework_alias_of_generated_wrapper_requires_attestation(generated, tmp_path, monkeypatch, mutation):
    ops, service, state, _ = generated
    python = service.installation_root / '.venv/bin/python'
    python.unlink()
    framework = tmp_path / 'Python.framework/Versions/3.12'
    base = framework / 'bin/python3.12'
    alias = framework / 'Resources/Python.app/Contents/MacOS/Python'
    base.parent.mkdir(parents=True)
    alias.parent.mkdir(parents=True)
    base.write_text('python')
    alias.hardlink_to(base)
    python.symlink_to(base)
    (python.parent.parent / 'pyvenv.cfg').write_text('home = test\n')
    wrapper, inner = m._commands(service)
    for pid in (120, 121):
        p = state['processes'][pid]
        state['processes'][pid] = replace(p, argv=(str(alias), *p.argv[1:]))
    if mutation == 'foreign_venv':
        foreign = tmp_path / 'foreign/venv/bin/python'
        foreign.parent.mkdir(parents=True)
        foreign.symlink_to(base)
        p = state['processes'][121]
        state['processes'][121] = replace(p, argv=(str(foreign), *p.argv[1:]))
    if mutation == 'child_parent':
        state['processes'][121] = replace(state['processes'][121], ppid=1)
    if mutation == 'profile':
        state['processes'][121] = replace(state['processes'][121], env={'CLOVER_HOME': '/wrong'})
    if mutation == 'cwd':
        state['processes'][121] = replace(state['processes'][121], cwd=tmp_path)
    if mutation == 'virtual_env':
        state['processes'][121].env['VIRTUAL_ENV'] = '/wrong'
    if mutation == 'plist':
        service.plist.write_bytes(service.plist_bytes + b' ')
    monkeypatch.setattr(m.sys, 'platform', 'linux' if mutation == 'non_darwin' else 'darwin')
    monkeypatch.setattr(m.os, 'getuid', lambda: 501)
    monkeypatch.setattr(m.os, 'geteuid', lambda: 501)
    import gateway.status
    monkeypatch.setattr(gateway.status, 'get_process_start_time', lambda pid: state['processes'][pid].birth + (1 if mutation == 'birth' or (mutation == 'birth_after_print' and state.get('prints', 0) >= 2) else 0))
    class Live:
        def __init__(self, pid): self.pid = pid
        def uids(self): return SimpleNamespace(real=501, effective=501, saved=502 if mutation == 'uid' else 501)
        def exe(self): return str(base if mutation == 'exe' else alias) if mutation != 'exe' else '/usr/bin/python3'
        def ppid(self): return state['processes'][self.pid].ppid
    monkeypatch.setattr(psutil, 'Process', Live)
    def launch(*args):
        state['calls'].append(args)
        if args[0] == 'print':
            state['prints'] = state.get('prints', 0) + 1
            lines = [f'gui/501/{service.label} = {{', f'\tpath = {service.plist}', f'\tprogram = {wrapper[0]}', '\targuments = {']
            lines.extend('\t\t' + a for a in wrapper)
            lines.extend(['\t}', '\tpid = 120', '}'])
            text = '\n'.join(lines)
            if mutation == 'loaded_args': text = text.replace('\t\tgateway', '\t\tforeign')
            if mutation == 'program': text = text.replace(f'program = {wrapper[0]}', 'program = /wrong')
            if mutation == 'pid': text = text.replace('pid = 120', 'pid = 999')
            if mutation == 'duplicate': text = text.replace('\tpid = 120', '\tprogram = /wrong\n\tpid = 120')
            if mutation == 'nested_only': text = text.replace(f'\tprogram = {wrapper[0]}', f'\tother = {{\n\t\tprogram = {wrapper[0]}\n\t}}')
            if mutation == 'nested_pid': text = text.replace('\tpid = 120', '\tother = {\n\t\tpid = 120\n\t}')
            if mutation == 'loaded_plist': text = text.replace(str(service.plist), '/tmp/other.plist')
            return m.CommandResult(0, text, '')
        return m.CommandResult(0, '', '')
    monkeypatch.setattr(m, '_launchctl', launch)
    service = replace(service, domain='gui/501')
    if mutation == 'ok':
        ops.unload(service)
        assert state['calls'][-1][0] == 'bootout'
    else:
        with pytest.raises(LifecycleRefused):
            ops.unload(service)
        assert not any(call[0] == 'bootout' for call in state['calls'])
