"""Exercise the ZIP updater's final result expression without network mutation."""
import ast
from pathlib import Path

import pytest


@pytest.mark.parametrize('desktop_ok,node_failures,expected', [
    (True, [], True),
    (False, [], False),
    (True, ['web'], False),
    (False, ['desktop'], False),
])
def test_zip_result_does_not_claim_success_for_partial_refresh(desktop_ok, node_failures, expected):
    source = Path(__file__).resolve().parents[1] / 'clover_cli/update_cmd.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    updater = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_update_via_zip')
    result = updater.body[-1]
    assert isinstance(result, ast.Return)
    expression = ast.Expression(body=result.value)
    actual = eval(compile(ast.fix_missing_locations(expression), str(source), 'eval'),
                  {'desktop_build_ok': desktop_ok, 'node_failures': node_failures})
    assert actual is expected
