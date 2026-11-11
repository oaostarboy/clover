"""Behaviour contracts for the pushed /desk/stream protocol v1."""
import json
import os
import struct
import sys
import time

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from clover_cli import desk_stream as ds
from clover_cli import desktop_control as dc

DISPLAY = {"id": 0, "name": "T", "x": 0, "y": 0, "width": 640, "height": 360,
           "stream_width": 640, "stream_height": 360}
GOOD = "Bearer good"
H = {"Authorization": GOOD}

PRODUCER = r"""
import sys, os, time
w, h, mode = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
i = 0
while True:
    shade = (i * 7) % 256 if mode == "changing" else 40
    sys.stdout.buffer.write(bytes([shade, (shade * 3) % 256, 90]) * (w * h))
    sys.stdout.buffer.flush()
    i += 1
    time.sleep(0.02)
"""


class FakeBackend(dc.Backend):
    name = "fake"
    mode = "changing"

    def unavailable_reason(self):
        return None

    def displays(self):
        return [DISPLAY]

    def capture_cmd(self, d, fps, frames):
        return ["true"]

    def raw_capture_cmd(self, d, fps=30):
        return [sys.executable, "-c", PRODUCER, str(d["stream_width"]), str(d["stream_height"]), self.mode]


@pytest.fixture(autouse=True)
def fresh_hub(monkeypatch):
    hub = ds.SourceHub()
    monkeypatch.setattr(ds, "_hub", hub)
    yield hub
    for src in list(hub.sources.values()):
        src.stop()


def make_app(mode="changing"):
    settings = dc.read_settings({"desktop_control": {"enabled": True}})
    backend = FakeBackend()
    backend.mode = mode
    ctl = dc.DesktopController(backend, settings)

    def http_auth(request):
        if request.headers.get("authorization") != GOOD:
            raise HTTPException(status_code=401, detail="Unauthorized")

    app = FastAPI()
    app.include_router(dc.build_router(http_auth=http_auth, ws_auth=lambda ws: ws.headers.get("authorization") == GOOD,
                                       ws_origin_ok=lambda ws: True, settings_fn=lambda: settings,
                                       controller_fn=lambda s: ctl))
    return TestClient(app)


def recv(ws):
    m = ws.receive()
    if m["type"] == "websocket.close":
        raise WebSocketDisconnect(m.get("code", 1000))
    return m


def frames_for(ws, seconds, ack=True):
    """Collect (header, jpeg) pairs for ``seconds``; text messages go to .texts."""
    out, texts, end = [], [], time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            m = recv(ws)
        except WebSocketDisconnect:
            break
        if m.get("bytes"):
            hdr, jpeg = ds.parse_frame(m["bytes"])
            out.append((hdr, jpeg))
            if ack:
                ws.send_text(json.dumps({"t": "ack", "seq": hdr["seq"]}))
        elif m.get("text"):
            texts.append(json.loads(m["text"]))
    return out, texts


START = {"t": "start", "display": 0, "w": 640, "q": 70, "fps": 30, "crop": None}


# ---- framing ---------------------------------------------------------------
def test_frame_roundtrip_and_bad_frames():
    hdr = {"seq": 3, "w": 640, "h": 360, "crop": None, "ts": 12345}
    data = ds.pack_frame(hdr, b"\xff\xd8jpeg\xff\xd9")
    assert struct.unpack(">I", data[:4])[0] == len(json.dumps(hdr, separators=(",", ":")))
    got, jpeg = ds.parse_frame(data)
    assert got == hdr and jpeg == b"\xff\xd8jpeg\xff\xd9"
    for bad in (b"", b"\x00\x00", struct.pack(">I", 99) + b"{}"):
        with pytest.raises(ValueError):
            ds.parse_frame(bad)


def test_parse_crop():
    assert ds.parse_crop(None) is None
    assert ds.parse_crop({"x": 1, "y": 2, "cw": 30, "ch": 40}) == (1, 2, 30, 40)
    for bad in ({"x": 1}, "x", {"x": "a", "y": 0, "cw": 1, "ch": 1}):
        with pytest.raises(ValueError):
            ds.parse_crop(bad)


def test_size_cap_lowers_quality_until_it_fits():
    import os as _os
    w, h = 1600, 900
    noise = _os.urandom(w * h * 3)  # worst case for JPEG
    jpeg, ow, oh, q, scale = ds.encode_capped(noise, w, h, 95, 1.0)
    assert len(jpeg) + 512 <= ds.MAX_FRAME_BYTES
    assert q < 95 or scale < 1.0


# ---- protocol --------------------------------------------------------------
def test_requires_credentials():
    client = make_app()
    with pytest.raises(Exception):
        with client.websocket_connect("/desk/stream?v=1"):
            pass
    r = client.get("/desk/displays", headers=H)
    assert r.json()["stream"] == {"v": 1}
    assert client.get("/desk/displays").status_code == 401


def test_hello_then_frames_with_header_and_acks():
    client = make_app()
    with client.websocket_connect("/desk/stream?v=1", headers=H) as ws:
        hello = json.loads(recv(ws)["text"])
        assert hello["t"] == "hello" and hello["v"] == 1 and hello["max_fps"] == 30
        assert hello["native"] == {"w": 640, "h": 360} and hello["displays"][0]["id"] == 0
        ws.send_text(json.dumps(START))
        frames, _ = frames_for(ws, 1.5)
    assert len(frames) >= 5
    seqs = [h["seq"] for h, _ in frames]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    h0, jpeg0 = frames[0]
    assert (h0["w"], h0["h"], h0["crop"]) == (640, 360, None) and jpeg0[:2] == b"\xff\xd8"
    assert all(len(ds.pack_frame(h, j)) <= ds.MAX_FRAME_BYTES for h, j in frames)


def test_static_screen_sends_one_frame_then_pings(monkeypatch):
    monkeypatch.setattr(ds, "PING_INTERVAL", 0.4)
    client = make_app("static")
    with client.websocket_connect("/desk/stream?v=1", headers=H) as ws:
        recv(ws)
        ws.send_text(json.dumps(START))
        frames, texts = frames_for(ws, 1.5)
    assert len(frames) == 1  # change detection: nothing else is pushed
    assert any(t.get("t") == "ping" for t in texts)


def test_backpressure_two_in_flight_then_closes_without_acks(monkeypatch):
    monkeypatch.setattr(ds, "ACK_TIMEOUT", 0.8)
    client = make_app()
    with client.websocket_connect("/desk/stream?v=1", headers=H) as ws:
        recv(ws)
        ws.send_text(json.dumps(START))
        got, code = 0, None
        t0 = time.monotonic()
        while time.monotonic() - t0 < 6:
            m = ws.receive()
            if m["type"] == "websocket.close":
                code = m["code"]
                break
            if m.get("bytes"):
                got += 1
        assert got == ds.MAX_INFLIGHT  # never more than 2 unacked
        assert code == ds.CLOSE_ACK_TIMEOUT


def test_crop_update_changes_header_and_bad_crop_errors():
    client = make_app()
    with client.websocket_connect("/desk/stream?v=1", headers=H) as ws:
        recv(ws)
        ws.send_text(json.dumps(START))
        frames_for(ws, 0.4)
        ws.send_text(json.dumps({"t": "update", "crop": {"x": 100, "y": 50, "cw": 320, "ch": 180}, "w": 640}))
        frames, _ = frames_for(ws, 1.2)
        crops = [h["crop"] for h, _ in frames if h["crop"]]
        assert crops and crops[0] == {"x": 100, "y": 50, "cw": 320, "ch": 180}
        assert (frames[-1][0]["w"], frames[-1][0]["h"]) == (320, 180)  # never upscaled past native pixels
        ws.send_text(json.dumps({"t": "update", "crop": {"x": 600, "y": 300, "cw": 320, "ch": 180}}))
        _, texts = frames_for(ws, 0.6)
        assert any(t.get("t") == "error" for t in texts)


def test_quality_adapts_down_on_slow_ack_rtt():
    client = make_app()
    with client.websocket_connect("/desk/stream?v=1", headers=H) as ws:
        recv(ws)
        ws.send_text(json.dumps({**START, "q": 90}))
        out, qs = [], []
        end = time.monotonic() + 3
        while time.monotonic() < end and len(out) < 6:
            m = recv(ws)
            if m.get("bytes"):
                hdr, jpeg = ds.parse_frame(m["bytes"])
                out.append(len(jpeg))
                time.sleep(0.3)  # slow client: RTT > 250 ms
                ws.send_text(json.dumps({"t": "ack", "seq": hdr["seq"]}))
        assert len(out) >= 4
    # smaller frames later (gradient content, so quality really changes size)
    assert out[-1] < out[0]


def test_disconnect_and_stop_stop_the_capture(fresh_hub):
    client = make_app()
    with client.websocket_connect("/desk/stream?v=1", headers=H) as ws:
        recv(ws)
        ws.send_text(json.dumps(START))
        frames_for(ws, 0.4)
        assert fresh_hub.sources
        src = next(iter(fresh_hub.sources.values()))
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline and src.alive():
        time.sleep(0.1)
    assert not src.alive() and not fresh_hub.sources
    assert src._proc.poll() is not None  # ffmpeg stand-in really exited


def test_two_phones_share_one_capture(fresh_hub):
    client = make_app()
    with client.websocket_connect("/desk/stream?v=1", headers=H) as a, \
            client.websocket_connect("/desk/stream?v=1", headers=H) as b:
        for ws in (a, b):
            recv(ws)
            ws.send_text(json.dumps(START))
        fa, _ = frames_for(a, 0.6)
        fb, _ = frames_for(b, 0.6)
        assert fa and fb and len(fresh_hub.sources) == 1


def test_device_without_control_view_is_refused():
    caps = {"v": frozenset({"control_view"})}
    settings = dc.read_settings({"desktop_control": {"enabled": True}})
    ctl = dc.DesktopController(FakeBackend(), settings)

    class Principal:  # marker only
        pass

    def ws_auth(ws):
        if ws.headers.get("authorization") != GOOD:
            return False
        ws._clover_device_principal = Principal()
        return True

    app = FastAPI()
    app.include_router(dc.build_router(http_auth=lambda r: None, ws_auth=ws_auth, ws_origin_ok=lambda w: True,
                                       device_caps_fn=lambda ws: caps["v"], settings_fn=lambda: settings,
                                       controller_fn=lambda s: ctl))
    client = TestClient(app)
    with client.websocket_connect("/desk/stream?v=1", headers=H) as ws:
        recv(ws)
        ws.send_text(json.dumps(START))
        frames, _ = frames_for(ws, 0.3)
        assert frames
        caps["v"] = frozenset()  # revoked: re-read within 5 s
        with pytest.raises(WebSocketDisconnect):
            for _ in range(200):
                m = recv(ws)
                if m.get("bytes"):
                    h, _j = ds.parse_frame(m["bytes"])
                    ws.send_text(json.dumps({"t": "ack", "seq": h["seq"]}))
