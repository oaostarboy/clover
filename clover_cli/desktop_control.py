"""Live desktop screen stream + remote control (the ``/desk/`` API).

Served by Clover's own web server so any user running ``clover serve`` / the
gateway can drive the iPhone Control tab without an extra service.  The wire
protocol is the "Clover Desk" API (``/desk/displays``, ``/desk/snapshot``,
``WS /desk/stream``) and is identical to the standalone clover-desk service.

Safety model
------------
* Opt-in: ``desktop_control.enabled`` in ``config.yaml`` (default ``false``).
  Disabled -> authenticated callers get 403; unauthenticated callers get 401.
* Auth is injected by the web server (same session/bearer auth as /api/*).
  Tokens are never read from the URL.
* View-only until ``take_control``.  One controller at a time; a disconnect
  auto-releases.  Take/release raise an on-screen notice and are audit-logged
  (counts only - typed text is never logged).
* Key names are whitelisted, text is length-capped, subprocesses always get
  argument lists (never a shell string).
* Capture runs only while a client is connected, at low priority, and stops a
  few seconds after the last client leaves.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

_log = logging.getLogger("clover.desktop_control")

MAX_TEXT = 500
MAX_WS_MESSAGE = 65536
IDLE_STOP_SECONDS = 3.0
MAX_COMBO_PARTS = 4

DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "view_only": False,
    "allow_input": True,
    "fps": 8,
    "width": 1280,
}


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def _clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default if value is None else bool(value)


def read_settings(config: Optional[dict] = None) -> Dict[str, Any]:
    """Return the effective ``desktop_control`` settings (defaults filled in)."""
    if config is None:
        try:
            from clover_cli.config import load_config

            config = load_config()
        except Exception:  # noqa: BLE001 - unreadable config == disabled
            config = {}
    raw = (config or {}).get("desktop_control")
    if not isinstance(raw, dict):
        raw = {}
    return {
        "enabled": _as_bool(raw.get("enabled"), False),
        "view_only": _as_bool(raw.get("view_only"), False),
        "allow_input": _as_bool(raw.get("allow_input"), True),
        "fps": _clamp_int(raw.get("fps"), 1, 15, DEFAULTS["fps"]),
        "width": _clamp_int(raw.get("width"), 320, 1920, DEFAULTS["width"]),
    }


def input_allowed(settings: Dict[str, Any]) -> bool:
    return bool(settings["allow_input"]) and not settings["view_only"]


# --------------------------------------------------------------------------
# Pure helpers: keys, coordinates, JPEG size
# --------------------------------------------------------------------------

_KEYS = {
    "Return", "Escape", "Tab", "space", "BackSpace", "Delete", "Home", "End",
    "Page_Up", "Page_Down", "Up", "Down", "Left", "Right", "Insert", "Print",
    "Menu", "XF86AudioPlay", "XF86AudioNext", "XF86AudioPrev",
    "XF86AudioRaiseVolume", "XF86AudioLowerVolume", "XF86AudioMute",
}
_KEYS |= {f"F{i}" for i in range(1, 13)}
_MODS = {
    "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift",
    "super": "super", "meta": "super", "cmd": "super",
}
_ALIAS = {
    "enter": "Return", "esc": "Escape", "backspace": "BackSpace",
    "del": "Delete", "pageup": "Page_Up", "pagedown": "Page_Down",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "tab": "Tab", "space": "space", "home": "Home", "end": "End",
}


def norm_key(combo: Any) -> str:
    """Validate a key combo against the whitelist; return X11-style ``a+b``.

    Modifiers (ctrl/alt/shift/super) may precede one final key; a non-modifier
    anywhere but last is rejected.  Raises ``ValueError`` for anything else.
    """
    parts = [p for p in str(combo).split("+") if p]
    if not parts or len(parts) > MAX_COMBO_PARTS:
        raise ValueError("bad combo")
    out: List[str] = []
    for i, part in enumerate(parts):
        low = part.lower()
        if low in _MODS:
            out.append(_MODS[low])
            continue
        key = _ALIAS.get(low, part)
        if key in _KEYS or re.fullmatch(r"[A-Za-z0-9]", key):
            if i != len(parts) - 1:
                raise ValueError("non-modifier must be last")
            out.append(key)
        else:
            raise ValueError("key not allowed")
    return "+".join(out)


def split_combo(norm: str) -> Tuple[List[str], str]:
    """Split a normalised combo into (modifiers, final_key_or_empty)."""
    parts = norm.split("+")
    if parts[-1] in _MODS.values():
        return parts, ""
    return parts[:-1], parts[-1]


def stream_size(width: int, height: int, max_width: int) -> Tuple[int, int]:
    """Streamed image size: never upscaled, capped at ``max_width``, even height."""
    scale = min(1.0, max_width / float(width))
    sw = max(2, round(width * scale))
    sh = max(2, round(height * scale))
    return sw, sh + (sh % 2)


def map_point(display: Dict[str, Any], x: Any, y: Any) -> Tuple[int, int]:
    """Map stream-image coordinates to absolute desktop coordinates."""
    sw, sh = display["stream_width"], display["stream_height"]
    fx, fy = float(x), float(y)
    if not (0 <= fx <= sw and 0 <= fy <= sh):
        raise ValueError("out of range")
    ax = display["x"] + min(display["width"] - 1, round(fx * display["width"] / sw))
    ay = display["y"] + min(display["height"] - 1, round(fy * display["height"] / sh))
    return int(ax), int(ay)


def jpeg_size(data: bytes) -> Tuple[int, int]:
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return 0, 0


def _run(args: List[str], *, env: Optional[dict] = None, timeout: float = 10) -> subprocess.CompletedProcess:
    """Run an argument list (never a shell string)."""
    return subprocess.run(
        [str(a) for a in args], env=env, timeout=timeout, check=True,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL,
    )


# --------------------------------------------------------------------------
# Platform backends
# --------------------------------------------------------------------------

class Backend:
    """Capture + input for one platform.  Methods are blocking; call off-loop."""

    name = "none"

    def __init__(self, width: int = 1280, fps: int = 8):
        self.width = width
        self.fps = fps
        self._cache: Tuple[float, List[dict]] = (0.0, [])

    # -- availability -----------------------------------------------------
    def unavailable_reason(self) -> Optional[str]:
        return "unsupported platform"

    def input_unavailable_reason(self) -> Optional[str]:
        return self.unavailable_reason()

    # -- geometry ---------------------------------------------------------
    def _raw_displays(self) -> List[dict]:
        return []

    def displays(self) -> List[dict]:
        now = time.monotonic()
        if now - self._cache[0] < 2.0 and self._cache[1]:
            return self._cache[1]
        out = []
        for d in self._raw_displays():
            sw, sh = stream_size(d["width"], d["height"], self.width)
            out.append({**d, "stream_width": sw, "stream_height": sh})
        self._cache = (now, out)
        return out

    def display(self, n: int) -> Optional[dict]:
        for d in self.displays():
            if d["id"] == n:
                return d
        return None

    # -- capture ----------------------------------------------------------
    def capture_cmd(self, d: dict, fps: int, frames: Optional[int]) -> List[str]:
        raise NotImplementedError

    def capture_env(self) -> Optional[dict]:
        return None

    def popen_kwargs(self) -> Dict[str, Any]:
        return {}

    def _vf(self, d: dict, fps: int) -> str:
        return (f"fps={fps}," if fps else "") + f"scale={d['stream_width']}:{d['stream_height']}"

    # -- input (all take absolute coords already mapped) --------------------
    def move(self, x: int, y: int) -> None: raise NotImplementedError
    def click(self, x: int, y: int, button: str, double: bool) -> None: raise NotImplementedError
    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None: raise NotImplementedError
    def scroll(self, dx: int, dy: int) -> None: raise NotImplementedError
    def type_text(self, text: str) -> None: raise NotImplementedError
    def key(self, norm: str) -> None: raise NotImplementedError

    # -- on-screen notice -------------------------------------------------
    def notify(self, message: str) -> None:
        _log.info("notice: %s", message)


_BTN = {"left": 1, "middle": 2, "right": 3}


class LinuxX11Backend(Backend):
    name = "linux-x11"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.display_name = os.environ.get("DISPLAY") or ":0"
        self.env = {**os.environ, "DISPLAY": self.display_name}

    def unavailable_reason(self) -> Optional[str]:
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            return "Wayland session: screen capture needs X11 (x11grab); not supported on Wayland"
        for tool in ("ffmpeg", "xrandr"):
            if not shutil.which(tool):
                return f"{tool} not found on PATH"
        try:
            _run(["xrandr", "--listmonitors"], env=self.env, timeout=5)
        except Exception as exc:  # noqa: BLE001
            return f"cannot reach X display {self.display_name}: {type(exc).__name__}"
        return None

    def input_unavailable_reason(self) -> Optional[str]:
        return self.unavailable_reason() or (None if shutil.which("xdotool") else "xdotool not found on PATH (input disabled)")

    def _raw_displays(self) -> List[dict]:
        out = _run(["xrandr", "--listmonitors"], env=self.env, timeout=5).stdout
        res = []
        for line in out.splitlines()[1:]:
            m = re.search(r"(\d+):\s+\+?\*?(\S+)\s+(\d+)/\d+x(\d+)/\d+\+(\d+)\+(\d+)", line)
            if m:
                i, name, w, h, x, y = m.groups()
                res.append({"id": int(i), "name": name, "x": int(x), "y": int(y),
                            "width": int(w), "height": int(h)})
        return res

    def capture_cmd(self, d, fps, frames):
        cmd = ["nice", "-n", "10", "ffmpeg", "-loglevel", "error", "-f", "x11grab",
               "-draw_mouse", "1", "-video_size", f"{d['width']}x{d['height']}",
               # A one-frame snapshot must not wait a whole frame interval at 1 fps.
               "-framerate", str(30 if frames == 1 else max(fps, 1)), "-i", f"{self.display_name}+{d['x']},{d['y']}",
               "-vf", self._vf(d, fps), "-sws_flags", "fast_bilinear", "-threads", "1",
               "-c:v", "mjpeg", "-q:v", "6"]
        if frames:
            cmd += ["-frames:v", str(frames)]
        return cmd + ["-f", "image2pipe", "-"]

    def capture_env(self):
        return self.env

    def _xdo(self, *args):
        _run(["xdotool", *args], env=self.env, timeout=10)

    def move(self, x, y): self._xdo("mousemove", x, y)

    def click(self, x, y, button, double):
        rep = ["--repeat", "2", "--delay", "60"] if double else []
        self._xdo("mousemove", x, y, "click", *rep, _BTN[button])

    def drag(self, x1, y1, x2, y2):
        self._xdo("mousemove", x1, y1, "mousedown", 1, "mousemove", x2, y2, "mouseup", 1)

    def scroll(self, dx, dy):
        if dy:
            self._xdo("click", "--repeat", abs(dy), "--delay", 10, 5 if dy > 0 else 4)
        if dx:
            self._xdo("click", "--repeat", abs(dx), "--delay", 10, 7 if dx > 0 else 6)

    def type_text(self, text): self._xdo("type", "--delay", 12, "--", text)

    def key(self, norm): self._xdo("key", "--", norm)

    def notify(self, message):
        if not shutil.which("notify-send"):
            return _log.info("notice: %s", message)
        subprocess.Popen(["notify-send", "-a", "Clover Desktop Control", "-t", "4000",
                          "Clover Desktop Control", message], env=self.env,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


_MAC_KEYS = {
    "Return": "return", "Escape": "esc", "Tab": "tab", "space": "space",
    "BackSpace": "delete", "Delete": "fwd-delete", "Home": "home", "End": "end",
    "Page_Up": "page-up", "Page_Down": "page-down", "Up": "arrow-up",
    "Down": "arrow-down", "Left": "arrow-left", "Right": "arrow-right",
    "XF86AudioPlay": "play-pause", "XF86AudioNext": "play-next",
    "XF86AudioPrev": "play-previous", "XF86AudioRaiseVolume": "volume-up",
    "XF86AudioLowerVolume": "volume-down", "XF86AudioMute": "mute",
}
_MAC_KEYS.update({f"F{i}": f"f{i}" for i in range(1, 13)})
_MAC_MODS = {"ctrl": "ctrl", "alt": "alt", "shift": "shift", "super": "cmd"}


class MacBackend(Backend):
    """macOS: ffmpeg avfoundation capture of the main display; cliclick input.

    Geometry comes from the Finder desktop bounds (points).  Only the main
    display is exposed.  Scroll is not supported (cliclick cannot scroll).
    """

    name = "macos"

    def unavailable_reason(self):
        if not shutil.which("ffmpeg"):
            return "ffmpeg not found (brew install ffmpeg)"
        if not shutil.which("osascript"):
            return "osascript not found"
        return None

    def input_unavailable_reason(self):
        return self.unavailable_reason() or (None if shutil.which("cliclick") else "cliclick not found (brew install cliclick); view-only")

    def _raw_displays(self):
        out = _run(["osascript", "-e", 'tell application "Finder" to get bounds of window of desktop'], timeout=5).stdout
        nums = [int(n) for n in re.findall(r"-?\d+", out)]
        if len(nums) != 4:
            return []
        x0, y0, x1, y1 = nums
        return [{"id": 0, "name": "Main", "x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}]

    def capture_cmd(self, d, fps, frames):
        cmd = ["nice", "-n", "10", "ffmpeg", "-loglevel", "error", "-f", "avfoundation",
               "-capture_cursor", "1", "-framerate", str(max(fps, 1)), "-pixel_format", "uyvy422",
               "-i", "Capture screen 0", "-vf", self._vf(d, fps), "-threads", "1",
               "-c:v", "mjpeg", "-q:v", "6"]
        if frames:
            cmd += ["-frames:v", str(frames)]
        return cmd + ["-f", "image2pipe", "-"]

    def _cc(self, *cmds):
        _run(["cliclick", *cmds], timeout=10)

    def move(self, x, y): self._cc(f"m:{x},{y}")

    def click(self, x, y, button, double):
        if button != "left":
            raise ValueError("only left click supported on macOS")
        self._cc(f"{'dc' if double else 'c'}:{x},{y}")

    def drag(self, x1, y1, x2, y2): self._cc(f"dd:{x1},{y1}", f"du:{x2},{y2}")

    def scroll(self, dx, dy): raise ValueError("scroll not supported on macOS")

    def type_text(self, text): self._cc(f"t:{text}")

    def key(self, norm):
        mods, key = split_combo(norm)
        cc_mods = ",".join(_MAC_MODS[m] for m in mods)
        steps = []
        if cc_mods:
            steps.append(f"kd:{cc_mods}")
        if key in _MAC_KEYS:
            steps.append(f"kp:{_MAC_KEYS[key]}")
        elif key:
            steps.append(f"t:{key.lower()}")
        if cc_mods:
            steps.append(f"ku:{cc_mods}")
        self._cc(*steps)

    def notify(self, message):
        if not shutil.which("osascript"):
            return _log.info("notice: %s", message)
        subprocess.Popen(["osascript", "-e", "on run argv", "-e",
                          'display notification (item 1 of argv) with title "Clover Desktop Control"',
                          "-e", "end run", message],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class WindowsBackend(Backend):
    """Windows: ffmpeg gdigrab + ctypes SendInput.  Untested on real hardware."""

    name = "windows"

    def unavailable_reason(self):
        return None if shutil.which("ffmpeg") else "ffmpeg not found on PATH"

    def _raw_displays(self):
        import ctypes
        from ctypes import wintypes

        mons: List[dict] = []
        proc_t = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                                    ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

        def cb(_h, _dc, rect, _lp):
            r = rect.contents
            mons.append({"id": len(mons), "name": f"Display{len(mons)}", "x": r.left, "y": r.top,
                         "width": r.right - r.left, "height": r.bottom - r.top})
            return 1

        ctypes.windll.user32.EnumDisplayMonitors(None, None, proc_t(cb), 0)
        return mons

    def capture_cmd(self, d, fps, frames):
        cmd = ["ffmpeg", "-loglevel", "error", "-f", "gdigrab", "-draw_mouse", "1",
               "-framerate", str(max(fps, 1)), "-offset_x", str(d["x"]), "-offset_y", str(d["y"]),
               "-video_size", f"{d['width']}x{d['height']}", "-i", "desktop",
               "-vf", self._vf(d, fps), "-threads", "1", "-c:v", "mjpeg", "-q:v", "6"]
        if frames:
            cmd += ["-frames:v", str(frames)]
        return cmd + ["-f", "image2pipe", "-"]

    def popen_kwargs(self):
        flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flags}

    # SendInput helpers (ctypes) -------------------------------------------
    _VK = {"Return": 0x0D, "Escape": 0x1B, "Tab": 0x09, "space": 0x20, "BackSpace": 0x08,
           "Delete": 0x2E, "Home": 0x24, "End": 0x23, "Page_Up": 0x21, "Page_Down": 0x22,
           "Up": 0x26, "Down": 0x28, "Left": 0x25, "Right": 0x27, "Insert": 0x2D, "Print": 0x2C,
           "Menu": 0x5D, "XF86AudioPlay": 0xB3, "XF86AudioNext": 0xB0, "XF86AudioPrev": 0xB1,
           "XF86AudioRaiseVolume": 0xAF, "XF86AudioLowerVolume": 0xAE, "XF86AudioMute": 0xAD}
    _MODVK = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "super": 0x5B}

    def _u(self):
        import ctypes
        return ctypes.windll.user32

    def _mouse(self, flags, data=0):
        self._u().mouse_event(flags, 0, 0, data, 0)

    def move(self, x, y): self._u().SetCursorPos(int(x), int(y))

    def click(self, x, y, button, double):
        down, up = {"left": (0x2, 0x4), "right": (0x8, 0x10), "middle": (0x20, 0x40)}[button]
        self.move(x, y)
        for _ in range(2 if double else 1):
            self._mouse(down); self._mouse(up); time.sleep(0.05)

    def drag(self, x1, y1, x2, y2):
        self.move(x1, y1); self._mouse(0x2); time.sleep(0.05)
        self.move(x2, y2); time.sleep(0.05); self._mouse(0x4)

    def scroll(self, dx, dy):
        if dy: self._mouse(0x800, -dy * 120)
        if dx: self._mouse(0x1000, dx * 120)

    def type_text(self, text):
        import ctypes
        from ctypes import wintypes

        class KI(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class MI(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class IN(ctypes.Structure):
            class _U(ctypes.Union):
                _fields_ = [("ki", KI), ("mi", MI)]
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        for ch in text:
            for unit in struct.unpack(f"<{len(ch.encode('utf-16-le')) // 2}H", ch.encode("utf-16-le")):
                for flags in (0x4, 0x4 | 0x2):  # UNICODE down, UNICODE up
                    inp = IN(type=1, ki=KI(0, unit, flags, 0, 0))
                    self._u().SendInput(1, ctypes.byref(inp), ctypes.sizeof(IN))
            time.sleep(0.012)

    def key(self, norm):
        mods, key = split_combo(norm)
        vks = [self._MODVK[m] for m in mods]
        if key:
            vks.append(self._VK.get(key) or (int(key[1:]) + 0x6F if re.fullmatch(r"F\d+", key) else ord(key.upper())))
        for vk in vks: self._u().keybd_event(vk, 0, 0, 0)
        for vk in reversed(vks): self._u().keybd_event(vk, 0, 0x2, 0)


def default_backend(width: int = 1280, fps: int = 8) -> Backend:
    if sys.platform.startswith("linux"):
        return LinuxX11Backend(width, fps)
    if sys.platform == "darwin":
        return MacBackend(width, fps)
    if sys.platform == "win32":
        return WindowsBackend(width, fps)
    return Backend(width, fps)


# --------------------------------------------------------------------------
# Capture (one ffmpeg per display, shared, stops when idle)
# --------------------------------------------------------------------------

class Capture:
    def __init__(self, backend: Backend, d: dict, fps: int):
        self.backend, self.d, self.fps = backend, d, fps
        self.subs: set = set()
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.task: Optional[asyncio.Task] = None
        self.stop_task: Optional[asyncio.Task] = None
        self.seq = 0

    def add(self, q: asyncio.Queue) -> None:
        self.subs.add(q)
        if self.stop_task:
            self.stop_task.cancel()
            self.stop_task = None
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self._run())

    def remove(self, q: asyncio.Queue) -> None:
        self.subs.discard(q)
        if not self.subs and not self.stop_task:
            self.stop_task = asyncio.create_task(self._later())

    async def _later(self) -> None:
        try:
            await asyncio.sleep(IDLE_STOP_SECONDS)
        except asyncio.CancelledError:
            return
        self.stop_task = None
        if not self.subs:
            await self.stop()

    async def stop(self) -> None:
        proc, task = self.proc, self.task
        self.proc = self.task = None
        if proc and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
        if task and not task.done():
            task.cancel()
        _log.info("capture stopped display=%s", self.d["id"])

    def _publish(self, item: Tuple[str, Any]) -> None:
        for q in list(self.subs):
            if q.full():
                try:
                    q.get_nowait()
                except Exception:  # noqa: BLE001
                    pass
            q.put_nowait(item)

    async def _run(self) -> None:
        _log.info("capture started display=%s", self.d["id"])
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *self.backend.capture_cmd(self.d, self.fps, None),
                env=self.backend.capture_env(),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                **self.backend.popen_kwargs(),
            )
        except Exception as exc:  # noqa: BLE001
            self._publish(("error", f"capture failed to start: {type(exc).__name__}"))
            self.task = None
            return
        buf, last, got = b"", None, False
        try:
            while True:
                chunk = await self.proc.stdout.read(65536)
                if not chunk:
                    break
                buf += chunk
                while True:
                    s = buf.find(b"\xff\xd8")
                    e = buf.find(b"\xff\xd9", s + 2) if s >= 0 else -1
                    if s < 0 or e < 0:
                        break
                    frame, buf = buf[s:e + 2], buf[e + 2:]
                    digest = hashlib.blake2s(frame).digest()
                    got = True
                    if digest == last:  # unchanged frame: skip
                        continue
                    last = digest
                    self.seq += 1
                    w, h = jpeg_size(frame)
                    meta = {"display": self.d["id"], "w": w, "h": h,
                            "scale": self.d["width"] / w if w else 1, "seq": self.seq}
                    self._publish(("frame", (meta, frame)))
            if not got and self.subs:
                self._publish(("error", "capture ended without producing frames"))
        except asyncio.CancelledError:
            pass
        finally:
            self.task = None


# --------------------------------------------------------------------------
# Control (ownership + input dispatch + audit)
# --------------------------------------------------------------------------

INPUT_TYPES = ("click", "move", "drag", "scroll", "type", "key")


class DesktopController:
    """Process-wide state: one controller at a time, capture sharing."""

    def __init__(self, backend: Backend, settings: Dict[str, Any]):
        self.backend = backend
        self.settings = settings
        self.owner: Any = None
        self.since = 0.0
        self.inputs = 0
        self.captures: Dict[int, Capture] = {}

    # -- ownership ------------------------------------------------------------
    def take(self, client: Any, who: str = "") -> Tuple[bool, str]:
        if not input_allowed(self.settings):
            return False, "input disabled by configuration (view only)"
        reason = self.backend.input_unavailable_reason()
        if reason:
            return False, f"input unavailable: {reason}"
        if self.owner is not None and self.owner is not client:
            return False, "controlled by another client"
        if self.owner is not client:
            self.owner, self.since, self.inputs = client, time.time(), 0
            _log.info("AUDIT take_control client=%s", who)
            self.backend.notify("A remote client is controlling this computer")
        return True, ""

    def release(self, client: Any, why: str) -> bool:
        if self.owner is not client:
            return False
        _log.info("AUDIT release why=%s inputs=%d duration=%ds", why, self.inputs, time.time() - self.since)
        self.owner = None
        self.backend.notify("Remote control of this computer ended")
        return True

    # -- input -----------------------------------------------------------------
    def do_input(self, m: Dict[str, Any]) -> None:
        """Blocking.  Validates and performs one input message."""
        b, t = self.backend, m.get("type")

        def pt(xk="x", yk="y"):
            d = b.display(int(m.get("display", 0)))
            if not d:
                raise ValueError("unknown display")
            return map_point(d, m[xk], m[yk])

        if t == "move":
            b.move(*pt())
        elif t == "click":
            btn = m.get("button", "left")
            if btn not in _BTN:
                raise ValueError("bad button")
            b.click(*pt(), btn, bool(m.get("double")))
        elif t == "drag":
            b.drag(*pt("x1", "y1"), *pt("x2", "y2"))
        elif t == "scroll":
            dx = max(-20, min(20, int(m.get("dx", 0))))
            dy = max(-20, min(20, int(m.get("dy", 0))))
            b.scroll(dx, dy)
        elif t == "type":
            s = str(m.get("text", ""))[:MAX_TEXT].replace("\x00", "")
            if s:
                b.type_text(s)
        elif t == "key":
            b.key(norm_key(m["key"] if "key" in m else m["combo"]))
        else:
            raise ValueError("unknown input type")

    async def handle_input(self, client: Any, m: Dict[str, Any]) -> Optional[str]:
        """Return an error string, or None on success."""
        if self.owner is not client:
            return "not controlling; send take_control first"
        try:
            await asyncio.get_running_loop().run_in_executor(None, self.do_input, m)
        except Exception as exc:  # noqa: BLE001
            return f"input rejected: {type(exc).__name__}"
        self.inputs += 1
        return None

    # -- capture ---------------------------------------------------------------
    def capture_for(self, d: dict) -> Capture:
        cap = self.captures.get(d["id"])
        if cap is None:
            cap = self.captures[d["id"]] = Capture(self.backend, d, self.settings["fps"])
        cap.d = d
        return cap

    async def snapshot(self, d: dict) -> bytes:
        proc = await asyncio.create_subprocess_exec(
            *self.backend.capture_cmd(d, 0, 1), env=self.backend.capture_env(),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            **self.backend.popen_kwargs(),
        )
        try:
            data, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        except asyncio.TimeoutError:
            proc.kill()
            return b""
        return data


_controller: Optional[DesktopController] = None


def get_controller(settings: Dict[str, Any]) -> DesktopController:
    """Singleton; settings refreshed on every call so config edits apply live."""
    global _controller
    if _controller is None:
        _controller = DesktopController(default_backend(settings["width"], settings["fps"]), settings)
    else:
        _controller.settings = settings
        _controller.backend.width = settings["width"]
        _controller.backend.fps = settings["fps"]
    return _controller


def reset_controller_for_tests(controller: Optional[DesktopController] = None) -> None:
    global _controller
    _controller = controller


# --------------------------------------------------------------------------
# FastAPI wiring
# --------------------------------------------------------------------------

def _setup_audit_log() -> None:
    """Send AUDIT/info lines to ``<CLOVER_HOME>/logs/desktop-control.log``."""
    if any(getattr(h, "_clover_desk", False) for h in _log.handlers):
        return
    try:
        from logging.handlers import RotatingFileHandler

        from clover_constants import get_clover_home

        path = get_clover_home() / "logs"
        path.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(path / "desktop-control.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        h._clover_desk = True  # type: ignore[attr-defined]
        _log.addHandler(h)
        _log.setLevel(logging.INFO)
    except Exception:  # noqa: BLE001 - audit is best-effort, never fatal
        pass


def build_router(
    *,
    http_auth: Callable[[Any], None],
    ws_auth: Callable[[Any], bool],
    ws_origin_ok: Callable[[Any], bool],
    device_caps_fn: Optional[Callable[[Any], Optional[frozenset]]] = None,
    settings_fn: Callable[[], Dict[str, Any]] = read_settings,
    controller_fn: Callable[[Dict[str, Any]], DesktopController] = get_controller,
):
    """Return an APIRouter exposing /desk/*.  Auth callables are injected.

    ``http_auth(request)`` raises HTTPException(401) when unauthenticated.
    ``ws_auth(ws)`` is True when the upgrade carries a valid credential
    (Authorization header, or the single-use ticket subprotocol) - never the URL.
    """
    from fastapi import APIRouter, HTTPException, Request, WebSocket
    from fastapi.responses import JSONResponse, Response
    from starlette.websockets import WebSocketDisconnect, WebSocketState

    router = APIRouter()
    _setup_audit_log()

    def gate(request: Request):
        http_auth(request)  # 401 first, so disabled state is not probeable unauthenticated
        s = settings_fn()
        if not s["enabled"]:
            raise HTTPException(status_code=403, detail="desktop control is disabled; set desktop_control.enabled: true in config.yaml")
        c = controller_fn(s)
        return s, c

    def unavailable(c: DesktopController) -> Optional[str]:
        return c.backend.unavailable_reason()

    @router.get("/desk/displays")
    async def desk_displays(request: Request):
        s, c = gate(request)
        loop = asyncio.get_running_loop()
        reason = await loop.run_in_executor(None, unavailable, c)
        if reason:
            return {"displays": [], "available": False, "reason": reason}
        try:
            displays = await loop.run_in_executor(None, c.backend.displays)
        except Exception as exc:  # noqa: BLE001
            return {"displays": [], "available": False, "reason": f"display query failed: {type(exc).__name__}"}
        in_reason = None if input_allowed(s) else "input disabled by configuration"
        in_reason = in_reason or await loop.run_in_executor(None, c.backend.input_unavailable_reason)
        return {"displays": displays, "available": True, "backend": c.backend.name,
                "input": in_reason is None, **({"input_reason": in_reason} if in_reason else {})}

    @router.get("/desk/snapshot")
    async def desk_snapshot(request: Request, display: int = 0):
        s, c = gate(request)
        loop = asyncio.get_running_loop()
        reason = await loop.run_in_executor(None, unavailable, c)
        if reason:
            return JSONResponse({"error": reason}, status_code=503)
        d = await loop.run_in_executor(None, c.backend.display, display)
        if not d:
            return JSONResponse({"error": "unknown display"}, status_code=404)
        data = await c.snapshot(d)
        if not data:
            return JSONResponse({"error": "capture failed"}, status_code=500)
        return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    async def _deny(ws: WebSocket, status: int, msg: str) -> None:
        try:
            await ws.send_denial_response(JSONResponse({"error": msg}, status_code=status))
        except Exception:  # noqa: BLE001
            await ws.close(code=4401 if status == 401 else 4403)

    @router.websocket("/desk/stream")
    async def desk_stream(ws: WebSocket):
        if not ws_origin_ok(ws):
            return await _deny(ws, 403, "forbidden")
        if not ws_auth(ws):
            return await _deny(ws, 401, "unauthorized")
        s = settings_fn()
        if not s["enabled"]:
            return await _deny(ws, 403, "desktop control disabled")
        c = controller_fn(s)
        loop = asyncio.get_running_loop()
        # Echo the stable subprotocol (never a ticket-bearing one) if the client offered it.
        offered = [p.strip() for p in str(ws.headers.get("sec-websocket-protocol", "")).split(",") if p.strip()]
        await ws.accept(subprotocol=getattr(ws, "_clover_ws_subprotocol", None) if offered else None)

        reason = await loop.run_in_executor(None, unavailable, c)
        if reason:
            await ws.send_json({"type": "error", "error": f"desktop unavailable: {reason}"})
            await ws.close(code=1011)
            return

        client = object()
        cur: Dict[str, Any] = {"id": None, "q": asyncio.Queue(maxsize=2), "cap": None}
        who = ws.client.host if ws.client else ""

        async def switch(n: int) -> None:
            d = await loop.run_in_executor(None, c.backend.display, n)
            if not d:
                await ws.send_json({"type": "error", "error": "unknown display"})
                return
            if cur["cap"] is not None:
                cur["cap"].remove(cur["q"])
            cur["id"], cur["cap"] = n, c.capture_for(d)
            cur["cap"].add(cur["q"])
            await ws.send_json({"type": "display", **d})

        async def pump() -> None:
            while True:
                kind, payload = await cur["q"].get()
                if kind == "frame":
                    meta, frame = payload
                    await ws.send_json({"type": "frame", **meta})
                    await ws.send_bytes(frame)
                else:
                    await ws.send_json({"type": "error", "error": payload})

        task: Optional[asyncio.Task] = None
        _log.info("AUDIT client connected ip=%s", who)
        try:
            try:
                first = int(ws.query_params.get("display", 0))
            except ValueError:
                first = 0
            await switch(first)
            task = asyncio.create_task(pump())
            while True:
                if device_caps_fn is not None and getattr(ws, "_clover_device_principal", None) is not None:
                    # Phone clients: re-read grants at least every 5 s so a revoke ends the stream.
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=5)
                    except asyncio.TimeoutError:
                        caps = await loop.run_in_executor(None, device_caps_fn, ws)
                        if not caps or "control_view" not in caps:
                            await ws.send_json({"type": "error", "error": "access revoked"})
                            await ws.close(code=4401)
                            break
                        if "control_drive" not in caps:
                            c.release(client, "revoked")
                        continue
                else:
                    msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                text = msg.get("text")
                if text is None:
                    continue
                if len(text) > MAX_WS_MESSAGE:
                    await ws.send_json({"type": "error", "error": "message too large"})
                    continue
                try:
                    m = json.loads(text)
                    t = m.get("type")
                except Exception:  # noqa: BLE001
                    await ws.send_json({"type": "error", "error": "bad json"})
                    continue
                if t == "select_display":
                    try:
                        await switch(int(m.get("display", 0)))
                    except (TypeError, ValueError):
                        await ws.send_json({"type": "error", "error": "unknown display"})
                elif t in ("take_control", *INPUT_TYPES) and device_caps_fn is not None \
                        and getattr(ws, "_clover_device_principal", None) is not None \
                        and "control_drive" not in ((await loop.run_in_executor(None, device_caps_fn, ws)) or ()):
                    c.release(client, "not granted")
                    await ws.send_json({"type": "error", "error": "control_drive not granted",
                                        "capability": "control_drive", "reason": "not_granted"})
                elif t == "take_control":
                    ok, err = c.take(client, who)
                    await ws.send_json({"type": "control", "owner": "user"} if ok else {"type": "error", "error": err})
                elif t == "release_control":
                    c.release(client, "client")
                    await ws.send_json({"type": "control", "owner": None})
                elif t in INPUT_TYPES:
                    err = await c.handle_input(client, m)
                    if err:
                        await ws.send_json({"type": "error", "error": err})
                else:
                    await ws.send_json({"type": "error", "error": "unknown message"})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            if task:
                task.cancel()
            c.release(client, "disconnect")
            if cur["cap"] is not None:
                cur["cap"].remove(cur["q"])
            _log.info("AUDIT client disconnected ip=%s", who)

    return router
