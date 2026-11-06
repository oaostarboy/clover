"""Exact Git completion-tail contracts; not full updater/native acceptance."""
import ast
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('staged', [False, True])
@pytest.mark.parametrize('failure', ['none', 'npm', 'desktop', 'fleet'])
def test_git_completion_retains_ordinary_contract(monkeypatch, staged, failure):
    from clover_cli import update_receipt
    outcomes = []
    monkeypatch.setattr(update_receipt, 'finalize_update_receipt',
                        lambda outcome, **kwargs: outcomes.append(outcome))
    source = Path(__file__).resolve().parents[2] / 'clover_cli/update_cmd.py'
    module = ast.parse(source.read_text())
    function = next(n for n in module.body if isinstance(n, ast.FunctionDef)
                    and n.name == '_cmd_update_impl')
    # Locate by the real receipt call, not line numbers or a copy of the logic.
    body = next(n for n in function.body if isinstance(n, ast.Try)
                and any(isinstance(s, ast.Try) and any(
                    isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                    and c.func.id == 'finalize_update_receipt' for c in ast.walk(s))
                    for s in n.body))
    start = next(i for i, n in enumerate(body.body) if isinstance(n, ast.Try)
                 and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                         and c.func.id == 'finalize_update_receipt' for c in ast.walk(n)))
    tail = ast.Module(body=body.body[start:], type_ignores=[])
    env = dict(sys=sys, logger=SimpleNamespace(info=lambda *a: None, debug=lambda *a: None),
               gateway_fleet_restart_incomplete=failure == 'fleet',
               node_failures=['web'] if failure == 'npm' else [],
               desktop_build_ok=failure != 'desktop', _fleet_snapshot=[],
               _staged_fetch_commit='a' * 40 if staged else None,
               _clear_fleet_restart_pending_marker=lambda: None)
    exit_code = 0
    try:
        exec(compile(ast.fix_missing_locations(tail), str(source), 'exec'), env)
    except SystemExit as exc:
        exit_code = exc.code
    must_fail = failure == 'fleet' or (staged and failure in ('npm', 'desktop'))
    assert exit_code == (1 if must_fail else 0)
    assert outcomes == ['partial' if must_fail else 'success']
