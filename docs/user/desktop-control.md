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
  width: 1280        # stream width in pixels, 320-1920
```

Or: `clover config set desktop_control.enabled true`.

## What it does

* `GET /desk/displays` lists your monitors (or says why capture is unavailable).
* `GET /desk/snapshot?display=N` returns one JPEG.
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
