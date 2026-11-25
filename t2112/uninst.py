import subprocess, os, time, pyautogui, glob
os.makedirs("shots",exist_ok=True)
sm=os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Clover")
print("before:", os.path.exists(sm), os.listdir(sm) if os.path.exists(sm) else None)
pyautogui.screenshot().save("shots/20-before-uninstall.png")
un=os.path.expandvars(r"%LOCALAPPDATA%\Clover Installer\Uninstall.exe")
p=subprocess.Popen([un]); time.sleep(8)
pyautogui.screenshot().save("shots/21-uninstaller.png")
for _ in range(30):
    if p.poll() is not None: break
    time.sleep(2)
print("uninstaller rc", p.poll())
time.sleep(5)
pyautogui.screenshot().save("shots/22-after-uninstall.png")
print("after: shortcut dir exists:", os.path.exists(sm), "inst dir exists:", os.path.exists(os.path.expandvars(r"%LOCALAPPDATA%\Clover Installer")))
