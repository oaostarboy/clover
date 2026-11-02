#!/usr/bin/env python3
"""Real E2E for the /desk/ API: temp CLOVER_HOME + loopback `clover serve`.

Linux/X11 only. Does ONE harmless mouse move to the centre of display 0 and
restores nothing else; it never clicks or types.  Usage:
    python scripts/e2e_desk.py
"""
import asyncio, json, os, socket, subprocess, sys, tempfile, time, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "e2e-desk-token-" + os.urandom(6).hex()
home = Path(tempfile.mkdtemp(prefix="clover-desk-e2e-"))
(home / "config.yaml").write_text("desktop_control:\n  enabled: true\n")
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
env = {**os.environ, "CLOVER_HOME": str(home), "CLOVER_DASHBOARD_SESSION_TOKEN": TOKEN, "PYTHONPATH": str(ROOT)}
proc = subprocess.Popen(["nice", "-n", "10", sys.executable, "-m", "clover_cli.main", "serve", "--port", str(port), "--skip-build"],
                        env=env, cwd=ROOT, stdout=open(home / "serve.log", "w"), stderr=subprocess.STDOUT)
base = f"http://127.0.0.1:{port}"
results = []

def check(name, ok, detail=""):
    results.append(ok); print(("PASS " if ok else "FAIL ") + name, detail, flush=True)

def get(path, auth=True):
    req = urllib.request.Request(base + path, headers={"Authorization": f"Bearer {TOKEN}"} if auth else {})
    try:
        r = urllib.request.urlopen(req, timeout=20); return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

async def ws_part(disp):
    import websockets
    hdr = {"Authorization": f"Bearer {TOKEN}"}
    url = f"ws://127.0.0.1:{port}/desk/stream?display=0"
    try:
        await websockets.connect(url, open_timeout=5)
        check("WS without auth rejected", False)
    except Exception as e:
        check("WS without auth rejected", "401" in str(e) or "403" in str(e), type(e).__name__)
    async with websockets.connect(url, additional_headers=hdr, max_size=None) as ws:
        frames = 0; binary = 0; t0 = time.time(); msgs = []
        while frames < 10 and time.time() - t0 < 30:
            m = await asyncio.wait_for(ws.recv(), 15)
            if isinstance(m, bytes): binary += 1; frames += 1 if m[:2] == b"\xff\xd8" else 0
            else: msgs.append(json.loads(m))
            # screen may be static: frames only arrive when changed; nudge by waiting
        check("WS received >=10 JPEG frames", frames >= 10, f"frames={frames} in {time.time()-t0:.1f}s")
        check("WS sent display meta", any(x.get("type") == "display" for x in msgs))
        await ws.send(json.dumps({"type": "move", "display": 0, "x": 10, "y": 10}))
        err = None
        for _ in range(50):
            m = await asyncio.wait_for(ws.recv(), 5)
            if isinstance(m, str) and json.loads(m).get("type") == "error": err = json.loads(m)["error"]; break
        check("input before take_control rejected", bool(err) and "take_control" in err, err)
        await ws.send(json.dumps({"type": "take_control"}))
        for _ in range(50):
            m = await asyncio.wait_for(ws.recv(), 5)
            if isinstance(m, str) and json.loads(m).get("type") == "control": check("take_control ok", json.loads(m)["owner"] == "user"); break
        d = disp[0]
        cx, cy = d["stream_width"] // 2, d["stream_height"] // 2
        await ws.send(json.dumps({"type": "move", "display": 0, "x": cx, "y": cy}))
        await asyncio.sleep(1.5)
        out = subprocess.run(["xdotool", "getmouselocation", "--shell"], capture_output=True, text=True).stdout
        loc = dict(l.split("=") for l in out.split())
        ex = d["x"] + round(cx * d["width"] / d["stream_width"]); ey = d["y"] + round(cy * d["height"] / d["stream_height"])
        check("mouse moved to centre of display 0", abs(int(loc["X"]) - ex) <= 2 and abs(int(loc["Y"]) - ey) <= 2, f"got=({loc['X']},{loc['Y']}) want=({ex},{ey})")
        await ws.send(json.dumps({"type": "key", "key": "rm -rf"}))
        err = None
        for _ in range(50):
            m = await asyncio.wait_for(ws.recv(), 5)
            if isinstance(m, str) and json.loads(m).get("type") == "error": err = json.loads(m)["error"]; break
        check("bad key rejected by whitelist", bool(err), err)
        await ws.send(json.dumps({"type": "release_control"}))
        for _ in range(50):
            m = await asyncio.wait_for(ws.recv(), 5)
            if isinstance(m, str) and json.loads(m).get("type") == "control": check("release_control ok", json.loads(m)["owner"] is None); break

try:
    for _ in range(120):
        try:
            if get("/api/status", auth=False)[0] == 200: break
        except Exception: pass
        if proc.poll() is not None: break
        time.sleep(1)
    st, _ = get("/desk/displays", auth=False); check("unauth /desk/displays -> 401", st == 401, st)
    st, body = get("/desk/displays"); check("auth /desk/displays -> 200", st == 200, st)
    disp = json.loads(body)["displays"]; print(json.dumps(json.loads(body)))
    check("displays non-empty", len(disp) > 0)
    st, body = get("/desk/snapshot?display=0"); check("snapshot is JPEG", st == 200 and body[:2] == b"\xff\xd8", f"{len(body)}B")
    st, _ = get("/desk/snapshot?display=0", auth=False); check("unauth snapshot -> 401", st == 401, st)
    asyncio.run(ws_part(disp))
    time.sleep(5)
    n = subprocess.run(["pgrep", "-f", f"x11grab.*"], capture_output=True, text=True).stdout.split()
    leftover = [p for p in n if str(proc.pid) in (open(f"/proc/{p}/stat").read().split()[3] if os.path.exists(f"/proc/{p}") else "")]
    check("ffmpeg capture stopped after disconnect", not leftover, leftover)
    log = (home / "logs" / "desktop-control.log")
    print(log.read_text() if log.exists() else "no audit log")
finally:
    proc.terminate()
    try: proc.wait(10)
    except Exception: proc.kill()
print("RESULT", "ALL PASS" if all(results) and results else "FAILURES", f"{sum(results)}/{len(results)}")
sys.exit(0 if all(results) else 1)
