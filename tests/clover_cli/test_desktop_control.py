"""Behaviour contracts for the /desk/ live desktop control API."""
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from clover_cli import desktop_control as dc

DISPLAY = {"id": 0, "name": "T", "x": 100, "y": 50, "width": 2000, "height": 1000,
           "stream_width": 1000, "stream_height": 500}
GOOD = "Bearer good"


class FakeBackend(dc.Backend):
    name = "fake"

    def __init__(self):
        super().__init__()
        self.calls = []
        self.notices = []
        self.reason = None

    def unavailable_reason(self):
        return self.reason

    def displays(self):
        return [DISPLAY]

    def capture_cmd(self, d, fps, frames):
        return ["true"]

    def move(self, x, y): self.calls.append(("move", x, y))
    def click(self, x, y, b, dbl): self.calls.append(("click", x, y, b, dbl))
    def type_text(self, t): self.calls.append(("type", t))
    def key(self, n): self.calls.append(("key", n))
    def notify(self, m): self.notices.append(m)


def make_app(settings=None, backend=None):
    settings = {**dc.read_settings({"desktop_control": {"enabled": True}}), **(settings or {})}
    ctl = dc.DesktopController(backend or FakeBackend(), settings)

    def http_auth(request):
        if request.headers.get("authorization") != GOOD:
            raise HTTPException(status_code=401, detail="Unauthorized")

    def ws_auth(ws):
        return ws.headers.get("authorization") == GOOD

    app = FastAPI()
    app.include_router(dc.build_router(http_auth=http_auth, ws_auth=ws_auth, ws_origin_ok=lambda ws: True,
                                       settings_fn=lambda: settings, controller_fn=lambda s: ctl))
    return TestClient(app), ctl


def recv_until(ws, kind, limit=40):
    for _ in range(limit):
        m = ws.receive()
        if m.get("text"):
            import json
            j = json.loads(m["text"])
            if j.get("type") == kind:
                return j
    raise AssertionError(f"no {kind}")


H = {"Authorization": GOOD}


def test_disabled_by_default_in_config():
    assert dc.read_settings({})["enabled"] is False
    from clover_cli.config_defaults import DEFAULT_CONFIG
    assert DEFAULT_CONFIG["desktop_control"]["enabled"] is False


def test_disabled_returns_403_after_auth_and_401_without():
    client, _ = make_app({"enabled": False})
    assert client.get("/desk/displays").status_code == 401
    assert client.get("/desk/displays", headers=H).status_code == 403
    with pytest.raises(Exception):
        with client.websocket_connect("/desk/stream", headers=H):
            pass


def test_auth_required_http_and_ws():
    client, _ = make_app()
    assert client.get("/desk/displays").status_code == 401
    assert client.get("/desk/snapshot").status_code == 401
    assert client.get("/desk/displays", headers=H).status_code == 200
    with pytest.raises(Exception):
        with client.websocket_connect("/desk/stream"):
            pass


def test_token_in_url_is_not_accepted():
    client, _ = make_app()
    assert client.get("/desk/displays?token=good").status_code == 401
    with pytest.raises(Exception):
        with client.websocket_connect("/desk/stream?token=good&ticket=good"):
            pass


def test_unavailable_platform_reports_reason():
    be = FakeBackend()
    be.reason = "ffmpeg not found"
    client, _ = make_app(backend=be)
    j = client.get("/desk/displays", headers=H).json()
    assert j["available"] is False and "ffmpeg" in j["reason"] and j["displays"] == []


def test_input_rejected_before_take_control_then_allowed():
    client, ctl = make_app()
    with client.websocket_connect("/desk/stream", headers=H) as ws:
        ws.send_json({"type": "move", "display": 0, "x": 10, "y": 10})
        assert "take_control" in recv_until(ws, "error")["error"]
        assert ctl.backend.calls == []
        ws.send_json({"type": "take_control"})
        assert recv_until(ws, "control")["owner"] == "user"
        ws.send_json({"type": "move", "display": 0, "x": 500, "y": 250})
        ws.send_json({"type": "release_control"})
        assert recv_until(ws, "control")["owner"] is None
        assert ctl.backend.calls == [("move", 100 + 1000, 50 + 500)]
    assert len(ctl.backend.notices) == 2  # take + release notices


def test_single_controller_and_autorelease_on_disconnect():
    client, ctl = make_app()
    with client.websocket_connect("/desk/stream", headers=H) as a:
        a.send_json({"type": "take_control"})
        recv_until(a, "control")
        with client.websocket_connect("/desk/stream", headers=H) as b:
            b.send_json({"type": "take_control"})
            assert "another client" in recv_until(b, "error")["error"]
            b.send_json({"type": "click", "display": 0, "x": 1, "y": 1})
            recv_until(b, "error")
            assert ctl.backend.calls == []
        assert ctl.owner is not None  # b leaving does not release a
    # a disconnected -> released, another client can now take it
    assert ctl.owner is None
    with client.websocket_connect("/desk/stream", headers=H) as c:
        c.send_json({"type": "take_control"})
        assert recv_until(c, "control")["owner"] == "user"


def test_view_only_and_allow_input_block_control():
    for s in ({"view_only": True}, {"allow_input": False}):
        client, ctl = make_app(s)
        with client.websocket_connect("/desk/stream", headers=H) as ws:
            ws.send_json({"type": "take_control"})
            assert "disabled" in recv_until(ws, "error")["error"]
        assert ctl.owner is None
        assert client.get("/desk/displays", headers=H).json()["input"] is False


@pytest.mark.parametrize("combo,expected", [
    ("ctrl+c", "ctrl+c"), ("Enter", "Return"), ("cmd+shift+4", "super+shift+4"), ("F5", "F5"), ("a", "a"),
])
def test_key_whitelist_accepts(combo, expected):
    assert dc.norm_key(combo) == expected


@pytest.mark.parametrize("combo", [
    "", "rm", "ctrl+rm", "a+ctrl", "ctrl+alt+shift+super+a", "XF86Launch1", "ctrl+;", "Return;ls", "$(id)", "F13",
])
def test_key_whitelist_rejects(combo):
    with pytest.raises(ValueError):
        dc.norm_key(combo)


def test_text_is_capped_and_args_not_shell():
    client, ctl = make_app()
    with client.websocket_connect("/desk/stream", headers=H) as ws:
        ws.send_json({"type": "take_control"})
        recv_until(ws, "control")
        ws.send_json({"type": "type", "text": "x" * 5000})
        ws.send_json({"type": "release_control"})
        recv_until(ws, "control")
    assert len(ctl.backend.calls[0][1]) == dc.MAX_TEXT


def test_coordinate_mapping_and_bounds():
    assert dc.map_point(DISPLAY, 0, 0) == (100, 50)
    assert dc.map_point(DISPLAY, 1000, 500) == (100 + 1999, 50 + 999)  # clamped to last pixel
    assert dc.map_point(DISPLAY, 500, 250) == (1100, 550)
    for bad in ((-1, 0), (0, 501), (1001, 0)):
        with pytest.raises(ValueError):
            dc.map_point(DISPLAY, *bad)


def test_stream_size_never_upscales_and_is_even():
    assert dc.stream_size(2560, 1440, 1280) == (1280, 720)
    assert dc.stream_size(1080, 1920, 1280) == (1080, 1920)
    w, h = dc.stream_size(3440, 1440, 1280)
    assert (w, h % 2) == (1280, 0)


def test_run_uses_argument_lists_only():
    import inspect
    src = inspect.getsource(dc)
    assert "shell=True" not in src and "os.system" not in src


def test_audit_log_never_contains_typed_text(caplog):
    import logging
    client, _ = make_app()
    caplog.set_level(logging.INFO, logger="clover.desktop_control")
    with client.websocket_connect("/desk/stream", headers=H) as ws:
        ws.send_json({"type": "take_control"})
        recv_until(ws, "control")
        ws.send_json({"type": "type", "text": "hunter2-secret"})
        ws.send_json({"type": "release_control"})
        recv_until(ws, "control")
    log = caplog.text
    assert "take_control" in log and "release" in log and "inputs=1" in log
    assert "hunter2" not in log


def test_web_server_mounts_desk_routes_with_real_auth():
    from clover_cli import web_server as ws_mod
    paths = {getattr(r, "path", "") for r in ws_mod.app.routes}
    assert {"/desk/displays", "/desk/snapshot", "/desk/stream"} <= paths
    client = TestClient(ws_mod.app)
    # no token: 401 regardless of the enabled flag (disabled state is not probeable)
    assert client.get("/desk/displays").status_code == 401
    ok = client.get("/desk/displays", headers={ws_mod._SESSION_HEADER_NAME: ws_mod._SESSION_TOKEN})
    assert ok.status_code in (403, 200)  # 403 while disabled by default


def test_capture_stops_after_idle(monkeypatch):
    import asyncio
    monkeypatch.setattr(dc, "IDLE_STOP_SECONDS", 0.05)

    async def run():
        be = FakeBackend()
        be.capture_cmd = lambda d, fps, frames: ["sleep", "30"]
        cap = dc.Capture(be, DISPLAY, 8)
        q = asyncio.Queue(maxsize=2)
        cap.add(q)
        await asyncio.sleep(0.2)
        proc = cap.proc
        assert proc is not None and proc.returncode is None
        cap.remove(q)
        await asyncio.sleep(0.4)
        assert cap.proc is None and proc.returncode is not None

    asyncio.run(run())


# ---- snapshot sizing / quality / crop ------------------------------------------------

ULTRA = {"id": 1, "name": "U", "x": 0, "y": 0, "width": 3440, "height": 1440,
         "stream_width": 1920, "stream_height": 804}


def test_snapshot_default_is_native_width_capped_at_2560():
    assert dc.snapshot_plan(ULTRA)["stream_width"] == 2560
    p = dc.snapshot_plan({**ULTRA, "width": 1920, "height": 1080})
    assert (p["stream_width"], p["stream_height"]) == (1920, 1080)


def test_snapshot_w_is_clamped_and_keeps_aspect():
    assert dc.snapshot_plan(ULTRA, w=100)["stream_width"] == 320
    assert dc.snapshot_plan(ULTRA, w=9999)["stream_width"] == 3440
    p = dc.snapshot_plan({**ULTRA, "width": 5120, "height": 2160}, w=9999)
    assert p["stream_width"] == 3840
    p = dc.snapshot_plan(ULTRA, w=1720)
    assert (p["stream_width"], p["stream_height"]) == (1720, 720)


def test_quality_maps_to_qscale_high_default():
    assert dc.quality_to_qscale(None) in (2, 3)
    assert dc.quality_to_qscale(100) == 2
    assert dc.quality_to_qscale(1) == 31
    assert dc.quality_to_qscale(0) == 31 and dc.quality_to_qscale(500) == 2
    assert dc.quality_to_qscale(80) < dc.quality_to_qscale(40)


def test_crop_validated_and_sets_filter():
    p = dc.snapshot_plan(ULTRA, crop=(100, 200, 800, 600))
    assert p["crop"] == (100, 200, 800, 600) and p["stream_width"] == 800
    for bad in ((-1, 0, 100, 100), (0, 0, 0, 100), (3000, 0, 500, 100), (0, 1400, 100, 100)):
        with pytest.raises(ValueError):
            dc.snapshot_plan(ULTRA, crop=bad)
    vf = dc.Backend()._vf(p, 0)
    assert vf.startswith("crop=800:600:100:200,scale=800:600")


def test_capture_cmds_use_planned_quality_and_stream_keeps_default():
    for be in (dc.LinuxX11Backend(), dc.MacBackend(), dc.WindowsBackend()):
        cmd = be.capture_cmd(dc.snapshot_plan(ULTRA, q=100), 0, 1)
        assert cmd[cmd.index("-q:v") + 1] == "2"
        cmd = be.capture_cmd(ULTRA, 8, None)
        assert cmd[cmd.index("-q:v") + 1] == "6"


def test_default_stream_width_is_1920():
    assert dc.read_settings({})["width"] == 1920
    from clover_cli.config_defaults import DEFAULT_CONFIG
    assert DEFAULT_CONFIG["desktop_control"]["width"] == 1920


class SnapBackend(FakeBackend):
    def displays(self):
        return [ULTRA]


def test_snapshot_endpoint_honors_params_and_validates_crop(monkeypatch):
    seen = []

    async def fake_grab(self, d):
        seen.append(d)
        return b"\xff\xd8x\xff\xd9"

    monkeypatch.setattr(dc.DesktopController, "_grab", fake_grab)
    client, _ = make_app(backend=SnapBackend())
    assert client.get("/desk/snapshot?display=1", headers=H).status_code == 200
    assert seen[-1]["stream_width"] == 2560 and seen[-1]["qscale"] == dc.SNAPSHOT_DEFAULT_QSCALE
    assert client.get("/desk/snapshot?display=1&w=1000&q=100", headers=H).status_code == 200
    assert seen[-1]["stream_width"] == 1000 and seen[-1]["qscale"] == 2
    assert client.get("/desk/snapshot?display=1&x=10&y=10&cw=400&ch=300", headers=H).status_code == 200
    assert seen[-1]["crop"] == (10, 10, 400, 300)
    n = len(seen)
    assert client.get("/desk/snapshot?display=1&x=10&y=10", headers=H).status_code == 400
    assert client.get("/desk/snapshot?display=1&x=3400&y=0&cw=400&ch=300", headers=H).status_code == 400
    assert client.get("/desk/snapshot?display=1&w=abc", headers=H).status_code == 422
    assert len(seen) == n


def test_oversize_snapshot_retries_once_at_lower_quality(monkeypatch):
    import asyncio
    sizes = iter([2_000_000, 900_000])
    qs = []

    async def fake_grab(self, d):
        qs.append(d["qscale"])
        return b"x" * next(sizes)

    monkeypatch.setattr(dc.DesktopController, "_grab", fake_grab)
    ctl = dc.DesktopController(FakeBackend(), dc.read_settings({}))
    out = asyncio.run(ctl.snapshot(dc.snapshot_plan(ULTRA)))
    assert len(out) == 900_000 and qs == [3, 9]
    sizes = iter([100])
    qs.clear()
    ctl._snap_cache.clear()
    assert len(asyncio.run(ctl.snapshot(dc.snapshot_plan(ULTRA)))) == 100 and qs == [3]


# ---- pipelined snapshots: single flight + short reuse + ETag ----------------------

def test_concurrent_snapshots_share_one_capture(monkeypatch):
    import asyncio
    ctl = dc.DesktopController(FakeBackend(), dc.read_settings({"desktop_control": {"enabled": True}}))
    grabs = []

    async def fake_grab(d):
        grabs.append(d["qscale"])
        await asyncio.sleep(0.05)
        return b"\xff\xd8jpeg\xff\xd9"

    monkeypatch.setattr(ctl, "_grab", fake_grab)
    plan = dc.snapshot_plan(DISPLAY, 640, 60)

    async def run():
        a = await asyncio.gather(*(ctl.snapshot(plan) for _ in range(4)))
        assert len(set(a)) == 1
        await ctl.snapshot(plan)            # inside SNAPSHOT_TTL: reused
        monkeypatch.setattr(dc, "SNAPSHOT_TTL", 0)
        await ctl.snapshot(plan)            # expired: a new capture

    asyncio.run(run())
    assert len(grabs) == 2


def test_snapshot_etag_returns_304_when_unchanged():
    client, ctl = make_app()

    async def fake_grab(d):
        return b"\xff\xd8same\xff\xd9"

    ctl._grab = fake_grab
    r = client.get("/desk/snapshot?w=640&q=60", headers={"authorization": GOOD})
    assert r.status_code == 200 and r.headers["etag"]
    r2 = client.get("/desk/snapshot?w=640&q=60", headers={"authorization": GOOD, "if-none-match": r.headers["etag"]})
    assert r2.status_code == 304 and r2.content == b""
