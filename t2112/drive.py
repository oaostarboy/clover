import subprocess, time, os, sys, hashlib, glob, traceback
import pyautogui
from pywinauto import Desktop, Application
S = r"shots"; os.makedirs(S, exist_ok=True)
def shot(n):
    p = f"{S}\\{n}.png"; pyautogui.screenshot().save(p); print("SHOT", p, flush=True)
def wins():
    return [(w.window_text(), w.class_name()) for w in Desktop(backend="win32").windows() if w.is_visible()]
def log(*a): print(*a, flush=True)
exe = glob.glob("dl\\Clover-Setup-*.exe")[0]
log("sha256", hashlib.sha256(open(exe,"rb").read()).hexdigest())
shot("00-desktop")
p = subprocess.Popen([exe]); time.sleep(8)
log("windows", wins()); shot("01-welcome")
try:
    w = Desktop(backend="win32").window(title_re=".*Clover.*Setup.*|.*Setup.*Clover.*")
    log("found installer window", w.window_text())
    for c in w.children(): log(" child", c.class_name(), repr(c.window_text()))
    w.child_window(title_re="&?Next.*|&?Install.*", class_name="Button").click()
except Exception: traceback.print_exc()
time.sleep(5); shot("02-install-page"); log("windows", wins())
# wait for console window running setup
def find_console():
    for w in Desktop(backend="win32").windows():
        if w.is_visible() and w.class_name() in ("ConsoleWindowClass","CASCADIA_HOSTING_WINDOW_CLASS"):
            return w
t0=time.time(); con=None
while time.time()-t0 < 900:
    con = find_console()
    if con: break
    time.sleep(10)
log("console", con and con.window_text(), "after", int(time.time()-t0)); shot("03-console-appeared")


import psutil, ctypes
t0=time.time()
def clover_exe():
    for root in (os.path.expandvars(r"%LOCALAPPDATA%\clover"), os.path.expandvars(r"%USERPROFILE%\.clover")):
        for f in ("clover-c1\\venv\\Scripts\\clover.exe",):
            if os.path.exists(os.path.join(root,f)): return os.path.join(root,f)
while time.time()-t0<1800 and not clover_exe():
    time.sleep(20)
    if p.poll() is not None and not clover_exe() and time.time()-t0>60:
        pass
    if int(time.time()-t0)%120<20: shot(f"03b-installing-{int(time.time()-t0)}")
log("clover.exe:", clover_exe(), int(time.time()-t0))
log("LOCALAPPDATA clover:", os.listdir(os.path.expandvars(r"%LOCALAPPDATA%\clover")) if os.path.exists(os.path.expandvars(r"%LOCALAPPDATA%\clover")) else None)
time.sleep(30)
log("windows", wins())
# find all consoles, show each and screenshot
for k,w in enumerate(Desktop(backend="win32").windows()):
    if w.is_visible() and w.class_name() in ("ConsoleWindowClass","CASCADIA_HOSTING_WINDOW_CLASS"):
        log("console", k, w.window_text())
        try:
            w.restore(); w.set_focus(); time.sleep(2); shot(f"05-console-{k}")
        except Exception as e: log("err", e)
