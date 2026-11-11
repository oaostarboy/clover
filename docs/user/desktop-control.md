# Desktop control (live screen + remote control)

Clover's web server can stream your computer's screen to the iPhone app's
**Control** tab and let the phone move the mouse and type. It is **off by
default** because it gives the phone full control of the machine.

## Turn it on

Add this to `config.yaml` (in your Clover home, usually `~/.clover/`) and
restart `clover serve` or the gateway:

```yaml
desktop_control:
  enabled: true      # default: false
  view_only: false   # true = stream the screen, never accept input
  allow_input: true  # false = same as view_only
  fps: 8             # 1-15
  width: 1920        # live-stream width cap in pixels, 320-1920 (never upscaled)
```

Or: `clover config set desktop_control.enabled true`.

## What it does

* `GET /desk/displays` lists your monitors (or says why capture is unavailable).
* `GET /desk/snapshot?display=N` returns one JPEG at the display's native width
  (capped at 2560) and high quality. Optional query parameters: `w` output width
  (320 to native, max 3840, aspect kept), `q` quality 1-100 (default about 95),
  and `x`, `y`, `cw`, `ch` to fetch only a native-pixel region at full sharpness
  (all four together; out-of-bounds is HTTP 400). A frame over about 1.8 MB is
  re-encoded once at lower quality so it fits the relay's 2 MiB response cap.
* `WS /desk/stream?v=1` is the phone's pushed live screen (protocol v1: ack-paced JPEG frames up to 30 per second,
  only when the screen changed, zoom crops, `/desk/displays` advertises `"stream":{"v":1}`). Taps and keys stay on the RPCs.
* `WS /desk/stream?display=N` streams JPEG frames (about 8 per second) and takes
  control messages. The phone must send `take_control` before any mouse or
  keyboard message is accepted. Only one phone controls at a time, and control is
  released when it disconnects.
* Every take/release shows an on-screen notice on the computer.
* Take/release and input counts are logged to `<CLOVER_HOME>/logs/desktop-control.log`.
  Typed text is never logged.

Requests use the same sign-in as the rest of the web server (Bearer token or
session). The WebSocket takes the `Authorization` header, or the one-time
`clover-gateway-ticket.<ticket>` subprotocol. Tokens in the URL are never accepted.
While the feature is off, signed-in callers get HTTP 403.

## Platform support

| Platform | Capture | Input | Notes |
|---|---|---|---|
| Linux (X11) | ffmpeg x11grab + xrandr | xdotool | Verified. Notices via `notify-send`. |
| Linux (Wayland) | not supported | not supported | Reports "unavailable". |
| macOS | ffmpeg avfoundation (main display) | cliclick | Untested. Needs Screen Recording permission; no scroll. |
| Windows | ffmpeg gdigrab | built-in Win32 input | Untested. |

Capture runs only while a phone is connected, at low priority, and stops a few
seconds after the last one leaves. A locked screen cannot be controlled.
