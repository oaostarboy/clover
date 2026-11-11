"""Pushed live-screen stream for phones: ``WS /desk/stream?v=1`` (protocol v1).

Replaces snapshot polling.  One persistent ffmpeg grab per (display, crop, size)
is shared by every subscriber of that region; the server pushes only the LATEST
frame, only when the region changed, and never more than ``MAX_INFLIGHT`` frames
ahead of the client's acks.

Client -> server (text JSON)::

    {"t":"start","display":0,"w":1600,"q":80,"fps":30,"crop":null}
    {"t":"update", ...any of w,q,fps,crop,display}   # crop = {x,y,cw,ch} native px
    {"t":"ack","seq":N}                               # after frame N is on screen
    {"t":"stop"}

Server -> client::

    text   {"t":"hello","v":1,"native":{w,h},"displays":[...],"max_fps":30}
    binary <u32 BE header length><UTF-8 JSON {"seq","w","h","crop","ts"}><JPEG>
    text   {"t":"ping"}          # every 5 s when no frame went out
    text   {"t":"error","error":...}
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import struct
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

_log = logging.getLogger("clover.desktop_control")

PROTOCOL_VERSION = 1
MAX_FPS = 30
MAX_INFLIGHT = 2
MAX_FRAME_BYTES = 900_000          # Cloudflare WS message limit is 1 MiB
ACK_TIMEOUT = 3.0                  # s without an ack while frames are in flight -> close
PING_INTERVAL = 5.0
TARGET_BYTES_PER_SEC = 3_000_000
RTT_SLOW = 0.250
MIN_Q, MAX_Q = 30, 95
MIN_SCALE = 0.5
MAX_CLIENT_MESSAGE = 4096
IDLE_POLL = 0.2                    # s between grabs while the screen is static
CLOSE_ACK_TIMEOUT = 4008
CLOSE_REVOKED = 4401


# ---------------------------------------------------------------- framing
def pack_frame(header: Dict[str, Any], jpeg: bytes) -> bytes:
    h = json.dumps(header, separators=(",", ":")).encode("utf-8")
    return struct.pack(">I", len(h)) + h + jpeg


def parse_frame(data: bytes) -> Tuple[Dict[str, Any], bytes]:
    if len(data) < 4:
        raise ValueError("short frame")
    (n,) = struct.unpack(">I", data[:4])
    if n <= 0 or 4 + n > len(data):
        raise ValueError("bad header length")
    return json.loads(data[4:4 + n].decode("utf-8")), data[4 + n:]


def parse_crop(value: Any) -> Optional[Tuple[int, int, int, int]]:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("crop must be an object or null")
    try:
        x, y, cw, ch = (int(value[k]) for k in ("x", "y", "cw", "ch"))
    except (KeyError, TypeError, ValueError):
        raise ValueError("crop needs integer x, y, cw, ch") from None
    return x, y, cw, ch


def _clamp(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def encode_jpeg(raw: bytes, w: int, h: int, q: int, scale: float = 1.0) -> Tuple[bytes, int, int]:
    """JPEG of an RGB24 buffer.  Pillow's libjpeg-turbo: ~3-5 ms at 1600 px."""
    from PIL import Image

    im = Image.frombuffer("RGB", (w, h), raw, "raw", "RGB", 0, 1)
    if scale < 0.999:
        w, h = max(2, int(w * scale) & ~1), max(2, int(h * scale) & ~1)
        im = im.resize((w, h), Image.BILINEAR)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=int(q), subsampling=2 if q < 70 else 0)
    return buf.getvalue(), w, h


def encode_capped(raw: bytes, w: int, h: int, q: int, scale: float) -> Tuple[bytes, int, int, int, float]:
    """Encode, lowering q (then size) until the message fits MAX_FRAME_BYTES."""
    budget = MAX_FRAME_BYTES - 512  # JSON header allowance
    while True:
        jpeg, ow, oh = encode_jpeg(raw, w, h, q, scale)
        if len(jpeg) <= budget:
            return jpeg, ow, oh, q, scale
        if q > MIN_Q:
            q = max(MIN_Q, q - 12)
        elif scale > 0.3:
            scale *= 0.8
        else:
            return jpeg, ow, oh, q, scale  # cannot happen for sane screens


# ------------------------------------------------------------------ source
class StreamSource:
    """One persistent ffmpeg raw-RGB grab, shared by all subscribers of a region.

    A reader thread keeps only the newest changed frame.  It stops pulling from the
    pipe (so ffmpeg blocks and burns no CPU) while nobody can take a frame, and
    polls slowly while the screen is static.
    """

    def __init__(self, key, cmd: List[str], env, w: int, h: int, popen_kwargs: Dict[str, Any]):
        self.key, self.cmd, self.env, self.w, self.h = key, cmd, env, w, h
        self.popen_kwargs = popen_kwargs
        self.size = w * h * 3
        self.subs: set = set()
        self.seq = 0
        self.latest: Optional[bytes] = None
        self.ts = 0
        self.error: Optional[str] = None
        self.stats = {"grabs": 0, "changed": 0}
        self._proc: Optional[subprocess.Popen] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._thread = threading.Thread(target=self._run, name="desk-grab", daemon=True)
        self._thread.start()

    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive()) and not self._stop.is_set()

    def stop(self) -> None:
        self._stop.set()
        p = self._proc
        if p is not None and p.poll() is None:
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass

    def _wanted(self) -> bool:
        return any(s.ready() for s in list(self.subs))

    def _notify(self) -> None:
        for s in list(self.subs):
            s.wake()

    def _run(self) -> None:
        try:
            self._proc = subprocess.Popen(
                self.cmd, env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0, **self.popen_kwargs)
        except Exception as exc:  # noqa: BLE001
            self.error = f"capture failed to start: {type(exc).__name__}"
            self._signal()
            return
        out = self._proc.stdout
        prev: Optional[bytes] = None
        static = 0
        try:
            while not self._stop.is_set():
                if not self._wanted():
                    time.sleep(0.01)  # pipe fills, ffmpeg blocks: no CPU
                    continue
                buf = bytearray(self.size)
                view, got = memoryview(buf), 0
                while got < self.size:
                    n = out.readinto(view[got:])
                    if not n:
                        raise EOFError
                    got += n
                self.stats["grabs"] += 1
                frame = bytes(buf)
                if frame == prev:
                    static += 1
                    if static > 2:
                        time.sleep(IDLE_POLL)
                    continue
                prev, static = frame, 0
                self.stats["changed"] += 1
                self.latest, self.ts = frame, int(time.time() * 1000)
                self.seq += 1
                self._signal()
        except EOFError:
            if not self._stop.is_set():
                self.error = "capture ended"
        except Exception as exc:  # noqa: BLE001
            if not self._stop.is_set():
                self.error = f"capture error: {type(exc).__name__}"
        finally:
            self.stop()
            self._signal()

    def _signal(self) -> None:
        loop = self._loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(self._notify)
            except RuntimeError:
                pass


class SourceHub:
    """Refcounted StreamSources, one per (display, crop, width)."""

    def __init__(self):
        self.sources: Dict[Any, StreamSource] = {}

    def acquire(self, backend, plan: Dict[str, Any], sub) -> StreamSource:
        crop = plan.get("crop")
        key = (plan["id"], crop, plan["stream_width"], plan["stream_height"])
        src = self.sources.get(key)
        if src is not None and not src.alive():
            self.sources.pop(key, None)
            src = None
        loop = asyncio.get_running_loop()
        if src is None:
            cmd = backend.raw_capture_cmd(plan)
            src = StreamSource(key, cmd, backend.capture_env(), plan["stream_width"],
                               plan["stream_height"], backend.popen_kwargs())
            self.sources[key] = src
            src.start(loop)
            _log.info("stream capture started display=%s %sx%s crop=%s", plan["id"], src.w, src.h, crop)
        src.subs.add(sub)
        return src

    def release(self, src: StreamSource, sub) -> None:
        src.subs.discard(sub)
        if src.subs:
            return
        # Stop at once: the spec is "no capture without a viewer".
        src.stop()
        if self.sources.get(src.key) is src:
            self.sources.pop(src.key, None)
        _log.info("stream capture stopped display=%s", src.key[0])


_hub = SourceHub()


def get_hub() -> SourceHub:
    return _hub


# ----------------------------------------------------------------- session
class StreamSession:
    """One phone's pushed stream: negotiation, pacing, backpressure, adaptation."""

    def __init__(self, ws, controller, *, hub: Optional[SourceHub] = None, who: str = "",
                 caps_check: Optional[Callable[[], Any]] = None, clock: Callable[[], float] = time.monotonic):
        self.ws, self.c, self.hub, self.who = ws, controller, hub or _hub, who
        self.caps_check = caps_check
        self.clock = clock
        self.loop = asyncio.get_running_loop()
        self.wake_ev = asyncio.Event()
        self.src: Optional[StreamSource] = None
        self.display_id = 0
        self.req_w, self.req_q, self.req_fps, self.req_crop = 1600, 80, MAX_FPS, None
        self.q, self.scale = 80, 1.0
        self.started = False
        self.dirty = True              # region/params changed: next frame must go out
        self.seq = 0
        self.last_seen_seq = -1
        self.inflight: Dict[int, float] = {}
        self.last_ack = self.clock()
        self.last_send = 0.0
        self.last_out = self.clock()
        self.rtt = 0.0
        self.window: List[Tuple[float, int]] = []
        self.sent = {"frames": 0, "bytes": 0}
        self.closed = False
        self.native = {"w": 0, "h": 0}

    # source callbacks
    def ready(self) -> bool:
        return self.started and not self.closed and len(self.inflight) < MAX_INFLIGHT

    def wake(self) -> None:
        self.wake_ev.set()

    # --- helpers
    async def _send_json(self, obj: Dict[str, Any]) -> None:
        await self.ws.send_text(json.dumps(obj, separators=(",", ":")))

    async def _error(self, msg: str) -> None:
        try:
            await self._send_json({"t": "error", "error": msg})
        except Exception:  # noqa: BLE001
            pass

    def _bytes_per_sec(self, now: float) -> float:
        self.window = [(t, n) for t, n in self.window if now - t <= 1.0]
        return float(sum(n for _, n in self.window))

    def _adapt(self, now: float) -> None:
        bps = self._bytes_per_sec(now)
        if self.rtt > RTT_SLOW or bps > TARGET_BYTES_PER_SEC:
            if self.q > MIN_Q + 4:
                self.q = max(MIN_Q, self.q - 8)
            elif self.scale > MIN_SCALE:
                self.scale = max(MIN_SCALE, self.scale * 0.85)
        elif self.rtt < RTT_SLOW / 2 and bps < TARGET_BYTES_PER_SEC / 2:
            if self.scale < 1.0:
                self.scale = min(1.0, self.scale / 0.9)
            elif self.q < self.req_q:
                self.q = min(self.req_q, self.q + 4)

    async def _plan(self):
        from clover_cli.desktop_control import snapshot_plan

        loop = self.loop
        d = await loop.run_in_executor(None, self.c.backend.display, self.display_id)
        if not d:
            raise ValueError("unknown display")
        plan = snapshot_plan(d, self.req_w, None, self.req_crop)
        self.native = {"w": int(d["width"]), "h": int(d["height"])}
        return plan

    async def _attach(self) -> None:
        plan = await self._plan()
        if self.src is not None:
            self.hub.release(self.src, self)
            self.src = None
        self.src = self.hub.acquire(self.c.backend, plan, self)
        self.crop_out = ({"x": plan["crop"][0], "y": plan["crop"][1], "cw": plan["crop"][2], "ch": plan["crop"][3]}
                         if plan.get("crop") else None)
        self.last_seen_seq = -1
        self.dirty = True
        self.q = min(self.req_q, self.q if self.q < self.req_q and self.rtt > RTT_SLOW else self.req_q)
        self.wake()

    def _apply(self, m: Dict[str, Any], initial: bool) -> bool:
        """Apply start/update params. Returns True if the capture region changed."""
        region = False
        if "display" in m:
            n = _clamp(m["display"], 0, 63, 0)
            region |= n != self.display_id or initial
            self.display_id = n
        if "w" in m:
            w = _clamp(m["w"], 320, 3840, 1600)
            region |= w != self.req_w
            self.req_w = w
        if "q" in m:
            self.req_q = _clamp(m["q"], MIN_Q, MAX_Q, 80)
            self.q = min(self.q, self.req_q) if not initial else self.req_q
            self.dirty = True
        if "fps" in m:
            self.req_fps = _clamp(m["fps"], 1, MAX_FPS, MAX_FPS)
        if "crop" in m:
            crop = parse_crop(m["crop"])
            region |= crop != self.req_crop
            self.req_crop = crop
        return region or initial

    # --- main
    async def run(self) -> None:
        from clover_cli.desktop_control import MAX_WS_MESSAGE  # noqa: F401 (documented limit)

        loop = self.loop
        displays = await loop.run_in_executor(None, self.c.backend.displays)
        first = displays[0] if displays else {"width": 0, "height": 0}
        await self._send_json({"t": "hello", "v": PROTOCOL_VERSION,
                               "native": {"w": int(first["width"]), "h": int(first["height"])},
                               "displays": [{k: d[k] for k in ("id", "name", "x", "y", "width", "height") if k in d}
                                            for d in displays],
                               "max_fps": MAX_FPS})
        sender = asyncio.create_task(self._pump())
        recv = asyncio.create_task(self._recv())
        try:
            await asyncio.wait({sender, recv}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            self.closed = True
            # Release the capture FIRST: this finally can itself be cancelled on disconnect.
            if self.src is not None:
                self.hub.release(self.src, self)
                self.src = None
            for t in (sender, recv):
                t.cancel()
            await asyncio.gather(sender, recv, return_exceptions=True)

    async def _recv(self) -> None:
        loop = self.loop
        last_caps = self.clock()
        while True:
            try:
                msg = await asyncio.wait_for(self.ws.receive(), timeout=5)
            except asyncio.TimeoutError:
                msg = None
            now = self.clock()
            if self.caps_check is not None and now - last_caps >= 5:
                last_caps = now
                caps = await loop.run_in_executor(None, self.caps_check)
                if not caps or "control_view" not in caps:
                    await self._error("access revoked")
                    await self.ws.close(code=CLOSE_REVOKED)
                    return
            if msg is None:
                continue
            if msg["type"] == "websocket.disconnect":
                return
            text = msg.get("text")
            if text is None:
                continue
            if len(text) > MAX_CLIENT_MESSAGE:
                await self._error("message too large")
                continue
            try:
                m = json.loads(text)
                t = m.get("t")
            except Exception:  # noqa: BLE001
                await self._error("bad json")
                continue
            if t == "ack":
                self._ack(m.get("seq"))
            elif t == "stop":
                return
            elif t in ("start", "update"):
                if t == "update" and not self.started:
                    await self._error("send start first")
                    continue
                try:
                    changed = self._apply(m, initial=(t == "start"))
                    if changed or self.src is None:
                        await self._attach()
                    self.started = True
                    self.dirty = True
                    self.wake()
                except ValueError as exc:
                    await self._error(str(exc))
            else:
                await self._error("unknown message")

    def _ack(self, seq: Any) -> None:
        if not isinstance(seq, int) or isinstance(seq, bool):
            return
        now = self.clock()
        sent_at = self.inflight.pop(seq, None)
        if sent_at is None:
            return
        # An ack for N also retires older frames the client skipped.
        for old in [s for s in self.inflight if s < seq]:
            self.inflight.pop(old, None)
        self.rtt = (now - sent_at) if self.rtt == 0 else self.rtt * 0.6 + (now - sent_at) * 0.4
        self.last_ack = now
        self._adapt(now)
        self.wake()

    async def _pump(self) -> None:
        loop = self.loop
        while True:
            now = self.clock()
            if self.inflight and now - self.last_ack > ACK_TIMEOUT:
                _log.info("stream ack timeout ip=%s", self.who)
                await self.ws.close(code=CLOSE_ACK_TIMEOUT)
                return
            src = self.src
            if src is not None and src.error and not src.alive():
                await self._error(src.error)
                await self.ws.close(code=1011)
                return
            can = self.started and src is not None and len(self.inflight) < MAX_INFLIGHT
            fresh = can and src.latest is not None and (src.seq != self.last_seen_seq or self.dirty)
            gap = 1.0 / max(1, self.req_fps)
            if fresh and now - self.last_send < gap:
                await self._wait(gap - (now - self.last_send))
                continue
            if fresh:
                raw, ts, sseq, w, h = src.latest, src.ts, src.seq, src.w, src.h
                self.last_seen_seq, self.dirty = sseq, False
                jpeg, ow, oh, q, scale = await loop.run_in_executor(
                    None, encode_capped, raw, w, h, self.q, self.scale)
                if q < self.q:
                    self.q = q
                if scale < self.scale:
                    self.scale = scale
                if self.closed:
                    return
                self.seq += 1
                header = {"seq": self.seq, "w": ow, "h": oh, "crop": getattr(self, "crop_out", None), "ts": ts}
                data = pack_frame(header, jpeg)
                t = self.clock()
                self.inflight[self.seq] = t
                if len(self.inflight) == 1:
                    self.last_ack = t  # ack clock starts when the pipe goes non-empty
                self.last_send = self.last_out = t
                self.window.append((t, len(data)))
                self.sent["frames"] += 1
                self.sent["bytes"] += len(data)
                await self.ws.send_bytes(data)
                continue
            if self.clock() - self.last_out >= PING_INTERVAL:
                self.last_out = self.clock()
                await self._send_json({"t": "ping"})
            wait = PING_INTERVAL - (self.clock() - self.last_out)
            if self.inflight:
                wait = min(wait, max(0.05, ACK_TIMEOUT - (self.clock() - self.last_ack)))
            await self._wait(max(0.01, wait))

    async def _wait(self, timeout: float) -> None:
        self.wake_ev.clear()
        try:
            await asyncio.wait_for(self.wake_ev.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
