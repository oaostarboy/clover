"""Plugin-defined ``external_process`` providers: registration, status, credentials, client, auth hook.

Each case runs in a fresh interpreter against a temp CLOVER_HOME with a flat-installed
``kind: model-provider`` plugin and a fake process command, because provider discovery and the
auth registry are populated once per process.
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

PLUGIN_INIT = '''
from providers import register_provider
from providers.base import ProviderProfile


class FakeClient:
    def __init__(self, **kw):
        self.kw = kw


class FakeProfile(ProviderProfile):
    def create_client(self, **kw):
        return FakeClient(**kw)


def _handler(action, args):
    if action == "status":
        print("fake-handler-status")
        return True
    return False


register_provider(FakeProfile(
    name="fake-proc",
    aliases=("fp",),
    display_name="Fake Proc",
    base_url="fake://local",
    auth_type="external_process",
    process_command="fake-agent-bin",
    process_args=("--json",),
    process_command_env_vars=("FAKE_PROC_COMMAND",),
    process_args_env_var="FAKE_PROC_ARGS",
    auth_handler=_handler,
    classify_api_error=lambda e, **kw: {"reason": "context_overflow", "retryable": True, "should_compress": True}
    if "boom-overflow" in str(e) else None,
))
'''

MANIFEST = "name: fake-proc\nversion: 0.0.1\nkind: model-provider\nmanifest_version: 2\n"


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    plug = h / "plugins" / "fake-proc"
    plug.mkdir(parents=True)
    (plug / "__init__.py").write_text(PLUGIN_INIT)
    (plug / "plugin.yaml").write_text(MANIFEST)
    (h / "config.yaml").write_text("plugins:\n  enabled:\n    - fake-proc\n  disabled: []\n")
    binp = tmp_path / "bin"
    binp.mkdir()
    fake = binp / "fake-agent-bin"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    return h, binp


def _run(home, code, *, path_bin=True, extra_env=None):
    h, binp = home
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FAKE_PROC", "CLOVER_"))}
    env.update(CLOVER_HOME=str(h), PYTHONPATH=str(ROOT), HOME=str(h.parent))
    env["PATH"] = (str(binp) + os.pathsep if path_bin else "") + "/usr/bin:/bin"
    env.update(extra_env or {})
    res = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], env=env,
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr[-2000:]
    return res.stdout.strip().splitlines()[-1]


def test_profile_accepts_process_fields_and_registers(home):
    out = _run(home, '''
        import json
        from providers import get_provider_profile
        from clover_cli.auth import PROVIDER_REGISTRY, resolve_provider
        p = get_provider_profile("fake-proc")
        print(json.dumps([p.process_command, list(p.process_args), PROVIDER_REGISTRY["fake-proc"].auth_type,
                          resolve_provider("fp")]))
    ''')
    assert json.loads(out) == ["fake-agent-bin", ["--json"], "external_process", "fake-proc"]


def test_disabled_plugin_does_not_register(home):
    h, _ = home
    (h / "config.yaml").write_text("plugins:\n  enabled: []\n  disabled: []\n")
    out = _run(home, '''
        from providers import get_provider_profile
        print(get_provider_profile("fake-proc") is None)
    ''')
    assert out == "True"


def test_status_and_credentials_use_profile_launch_fields(home):
    out = _run(home, '''
        import json
        from clover_cli.auth import get_auth_status, resolve_external_process_provider_credentials as r
        s = get_auth_status("fake-proc")
        c = r("fake-proc")
        print(json.dumps([s["logged_in"], s["command"], s["args"], c["command"].endswith("fake-agent-bin"),
                          c["args"], c["base_url"]]))
    ''')
    assert json.loads(out) == [True, "fake-agent-bin", ["--json"], True, ["--json"], "fake://local"]


def test_env_overrides_command_and_args(home):
    h, binp = home
    alt = binp / "alt-bin"
    alt.write_text("#!/bin/sh\nexit 0\n")
    alt.chmod(0o755)
    out = _run(home, '''
        import json
        from clover_cli.auth import resolve_external_process_provider_credentials as r
        c = r("fake-proc")
        print(json.dumps([c["command"].endswith("alt-bin"), c["args"]]))
    ''', extra_env={"FAKE_PROC_COMMAND": "alt-bin", "FAKE_PROC_ARGS": "--a 'b c'"})
    assert json.loads(out) == [True, ["--a", "b c"]]


def test_missing_command_is_a_clear_auth_error_not_unknown_provider(home):
    out = _run(home, '''
        from clover_cli.auth import resolve_external_process_provider_credentials as r, AuthError
        try:
            r("fake-proc")
        except AuthError as e:
            print(e.code, "fake-agent-bin" in str(e))
    ''', path_bin=False)
    assert out == "missing_process_command True"


def test_runtime_resolution_and_plugin_client(home):
    out = _run(home, '''
        import json
        from clover_cli.runtime_provider import resolve_runtime_provider
        rt = resolve_runtime_provider(requested="fake-proc")
        from providers import get_provider_profile
        cl = get_provider_profile("fake-proc").create_client(api_key=rt["api_key"], base_url=rt["base_url"],
                                                             command=rt["command"], args=rt["args"])
        print(json.dumps([rt["provider"], rt["api_mode"], rt["args"], type(cl).__name__, cl.kw["command"].endswith("fake-agent-bin")]))
    ''')
    assert json.loads(out) == ["fake-proc", "chat_completions", ["--json"], "FakeClient", True]


def test_agent_client_factory_picks_plugin_client(home):
    out = _run(home, '''
        from types import SimpleNamespace
        from agent.agent_runtime_helpers import create_openai_client
        agent = SimpleNamespace(provider="fake-proc", _client_log_context=lambda: "")
        cl = create_openai_client(agent, {"api_key": "x", "base_url": "fake://local", "command": "c", "args": []},
                                  reason="t", shared=True)
        print(type(cl).__name__)
    ''')
    assert out == "FakeClient"


def test_auth_handler_hook_and_error_classifier(home):
    out = _run(home, '''
        from types import SimpleNamespace
        from clover_cli.auth_commands import auth_status_command
        auth_status_command(SimpleNamespace(provider="fake-proc"))
        from agent.error_classifier import classify_api_error
        c = classify_api_error(RuntimeError("boom-overflow"), provider="fake-proc", model="m")
        print(c.reason.name, c.should_compress)
    ''')
    assert out == "context_overflow True"


def test_copilot_acp_still_external_process_and_unchanged(home):
    out = _run(home, '''
        from clover_cli.auth import PROVIDER_REGISTRY, get_external_process_provider_status
        s = get_external_process_provider_status("copilot-acp")
        print(PROVIDER_REGISTRY["copilot-acp"].auth_type, s["args"])
    ''')
    assert out == "external_process ['--acp', '--stdio']"
