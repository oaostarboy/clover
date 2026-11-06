"""Pin the only approved direct rollback-watcher utility executions."""
import ast
import hashlib
import os
from pathlib import Path
import shutil

from interrupted_identity import Refusal
from interrupted_source import pinned_hash


def _pin(path):
    path = Path(path)
    if not path.is_absolute():
        raise Refusal('utility executable path is not absolute')
    try:
        physical = path.resolve(strict=True)
        before = physical.stat()
        data = physical.read_bytes()
        after = physical.stat()
    except OSError as exc:
        raise Refusal('utility executable unavailable') from exc
    fields = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if fields(before) != fields(after) or not physical.is_file() or not os.access(physical, os.X_OK):
        raise Refusal('utility executable changed or is not executable')
    return {'path': str(path), 'resolved': str(physical), 'sha256': hashlib.sha256(data).hexdigest(),
            'identity': fields(after)}


def pin_helpers(paths, tracer):
    helpers = {}
    for path in paths:
        path = Path(path).resolve(strict=True)
        if not path.is_file():
            raise Refusal('helper is not a regular file')
        raw = path.read_bytes()
        if raw != path.read_bytes():
            raise Refusal('helper changed during pinning')
        helpers[str(path)] = hashlib.sha256(raw).hexdigest()
    return {'helpers_sha256': helpers, 'tracer': _pin(tracer)}


def check_helpers(pins):
    for name, digest in pins['helpers_sha256'].items():
        try:
            raw = Path(name).read_bytes()
        except OSError as exc:
            raise Refusal('pinned helper unavailable') from exc
        if hashlib.sha256(raw).hexdigest() != digest:
            raise Refusal('pinned helper changed')
    if _pin(pins['tracer']['path']) != pins['tracer']:
        raise Refusal('pinned tracer changed')


def _validate_source(raw):
    try:
        tree = ast.parse(raw)
        functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        expected = {
            '_current_head': "['git', 'rev-parse', 'HEAD']",
            'watch': "['git', 'rev-parse', 'HEAD']",
            '_core_imports_healthy': "[str(python), '-c', 'import clover_cli.main, gateway.run']",
            '_gateway_running': "['ps', '-eo', 'args']",
        }
        for name, expression in expected.items():
            want = ast.dump(ast.parse(expression, mode='eval').body, include_attributes=False)
            calls = [n for n in ast.walk(functions[name]) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
                     and n.func.value.id == 'subprocess' and n.func.attr == 'run' and n.args]
            if not any(ast.dump(n.args[0], include_attributes=False) == want for n in calls):
                raise Refusal('pinned watcher utility source shape changed')
    except (SyntaxError, KeyError, UnicodeError) as exc:
        raise Refusal('invalid pinned watcher source') from exc


def preflight(stage, commit, tree, python, env):
    relative = 'clover_cli/update_restart_watcher.py'
    try:
        digest = pinned_hash(stage, commit, tree, relative)
    except Exception as exc:
        raise Refusal('watcher source pin failed') from exc
    source = Path(stage) / relative
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise Refusal('watcher source changed during preflight')
    _validate_source(raw)
    tools = {}
    for name in ('git', 'ps'):
        path = shutil.which(name, path=env.get('PATH', ''))
        if not path:
            raise Refusal('required utility not found: ' + name)
        tools[name] = _pin(path)
    tools['python'] = _pin(python)
    return {'source_path': str(source), 'source_sha256': digest, 'tools': tools,
            'commit': commit, 'tree': tree}


def bind_rules(pins, frozen, *, rollback_pid):
    try:
        raw = Path(frozen).read_bytes()
        current = Path(pins['source_path']).read_bytes()
    except OSError as exc:
        raise Refusal('watcher source unavailable at binding') from exc
    digest = pins['source_sha256']
    if (raw != current or hashlib.sha256(raw).hexdigest() != digest
            or hashlib.sha256(current).hexdigest() != digest):
        raise Refusal('frozen watcher differs from pinned source')
    _validate_source(raw)
    for pin in pins['tools'].values():
        if _pin(pin['path']) != pin:
            raise Refusal('utility tool changed after preflight')
    if not isinstance(rollback_pid, int) or rollback_pid <= 1:
        raise Refusal('invalid rollback watcher identity')
    tools = pins['tools']
    return [
        {'parent_pid': rollback_pid, 'path': tools['git']['path'],
         'argv': ['git', 'rev-parse', 'HEAD'], 'max_count': 128},
        {'parent_pid': rollback_pid, 'path': tools['python']['path'],
         'argv': [tools['python']['path'], '-c', 'import clover_cli.main, gateway.run'], 'max_count': 128},
        {'parent_pid': rollback_pid, 'path': tools['ps']['path'],
         'argv': ['ps', '-eo', 'args'], 'max_count': 256},
    ]
