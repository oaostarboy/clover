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

import psutil
def setup_proc():
    for pr in psutil.process_iter(["name","cmdline"]):
        try:
            c=" ".join(pr.info["cmdline"] or [])
            if "setup" in c and ("clover" in c.lower()) and "install.ps1" not in c and "drive.py" not in c and "powershell" not in c.lower(): return c
        except Exception: pass
t0=time.time()
while time.time()-t0<1500 and not setup_proc():
    time.sleep(15); 
    if int(time.time()-t0)%60<15: shot(f"03b-installing-{int(time.time()-t0)}")
log("setup proc:", setup_proc(), int(time.time()-t0)); time.sleep(6)
def focus():
    c=find_console()
    if c:
        try: c.set_focus()
        except Exception as e: log("focus err", e)
    time.sleep(1)
def key(k, n):
    focus(); pyautogui.press(k) if isinstance(k,str) else None; time.sleep(2.5); shot(n)
def typ(t, n):
    focus(); pyautogui.write(t, interval=0.12); time.sleep(1.5); shot(n)
shot("05-setup-first")
key("enter","06-after-quickstart")
typ("Luna","07-name-typed")
key("enter","08-provider-list")
key("esc","09-esc-back")
key("enter","10-reenter")
for i in range(4): pyautogui.press("down"); time.sleep(.3)
shot("11-own-model-highlight")
key("enter","12-url-prompt")
typ("http://127.0.0.1:11434/v1","13-url-typed")
key("enter","14-after-url"); time.sleep(10); shot("15-after-url-wait")
key("enter","16-next"); time.sleep(8); shot("17-next-wait")
log("windows", wins())
