"""Manual live contract check against an owned, harmless GTK counter.

Run with the candidate Python environment and a normal accessible X11 desktop.
The fixture never touches browser content, credentials or permission dialogs.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from tools.computer_use.cua_backend import CuaDriverBackend

GTK = '''
import gi, json, os, sys
gi.require_version('Gtk', '3.0')
gi.require_version('GdkX11', '3.0')
from gi.repository import Gtk, GdkX11
from pathlib import Path
root=Path(sys.argv[1]); count=0
window=Gtk.Window(title='Clover CUA repair counter')
window.set_default_size(320,120)
box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
label=Gtk.Label(label='Count: 0')
button=Gtk.Button(label='Increment CUA test counter')
def increment(_):
 global count
 count+=1
 label.set_text('Count: '+str(count))
 button.set_label('Increment CUA test counter ('+str(count)+')')
 (root/'count').write_text(str(count))
button.connect('clicked',increment)
box.pack_start(label,True,True,0);box.pack_start(button,True,True,0)
window.add(box);window.connect('destroy',Gtk.main_quit);window.show_all()
(root/'count').write_text('0')
(root/'ready.json').write_text(json.dumps({'pid':os.getpid(),'window_id':window.get_window().get_xid()}))
Gtk.main()
'''


def main():
    if not os.environ.get('DISPLAY'):
        raise RuntimeError('Run on the normal authorized X11 desktop (DISPLAY required)')
    with tempfile.TemporaryDirectory(prefix='clover-cua-counter-') as directory:
        root = Path(directory)
        script = root / 'counter.py'
        script.write_text(GTK)
        proc = subprocess.Popen(['/usr/bin/python', str(script), str(root)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        backend = CuaDriverBackend()
        try:
            deadline = time.monotonic() + 15
            while not (root / 'ready.json').exists():
                if proc.poll() is not None:
                    raise RuntimeError(proc.stderr.read().decode()[:1000] if proc.stderr else 'GTK process exited')
                if time.monotonic() > deadline:
                    raise TimeoutError('GTK fixture did not become ready')
                time.sleep(.1)
            target = json.loads((root / 'ready.json').read_text())
            backend.start()
            capture = backend.capture(mode='ax', **target)
            button = next(e for e in capture.elements if e.label == 'Increment CUA test counter')
            assert button.element_token, 'Real driver capture did not supply a token'
            assert (root / 'count').read_text() == '0'
            result = backend.click(element=button.index)
            assert result.ok, result.message
            deadline = time.monotonic() + 5
            while (root / 'count').read_text() != '1' and time.monotonic() < deadline:
                time.sleep(.1)
            assert (root / 'count').read_text() == '1', result.message
            after = backend.capture(mode='ax', **target)
            changed = next(e for e in after.elements if e.label == 'Increment CUA test counter (1)')
            assert changed.element_token != button.element_token
            print(json.dumps({'result':'PASS', 'native_counter_before':0, 'native_counter_after':1,
                              'ax_readback':changed.label, 'element_index':button.index,
                              'opaque_token_from_capture':True, 'fresh_capture_replaced_token':True,
                              'delivery':'background', 'candidate_module':__import__('tools.computer_use.cua_backend',fromlist=['']).__file__}))
        finally:
            backend.stop()
            proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait()


if __name__ == '__main__':
    main()
