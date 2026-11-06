"""Private loopback IRC and deterministic model fixture for the native cell."""
import json
import sys
import time
from pathlib import Path
from stage_independence_wire import ChatWire

root = Path(sys.argv[1])
wire = ChatWire(root, root / 'unused-effect')
wire.release.set()
wire.start()
(root / 'chat-ports.json').write_text(json.dumps({
    'irc': wire.irc.server_address[1], 'model': wire.model.server_address[1]}))
try:
    deadline = time.monotonic() + 1800
    count = 0
    while time.monotonic() < deadline:
        if count == 0 and (root / 'chat-pre').exists() and wire.ready():
            wire.send('STAGE_HISTORY_SENTINEL: reply with the local fixture auxiliary response.')
            count = 1
        messages = [m for m in wire.bot_messages() if 'Local acceptance auxiliary response.' in m]
        if count == 1 and messages:
            (root / 'chat-pre-reply.json').write_text(json.dumps({'messages': messages}))
            count = 2
        if count == 2 and (root / 'chat-post').exists() and wire.ready():
            wire.send('STAGE_INDEPENDENCE_TURN: reply with the local fixture auxiliary response.')
            count = 3
        if count == 3 and len(messages) >= 2:
            (root / 'chat-post-reply.json').write_text(json.dumps({'messages': messages}))
            count = 4
        if (root / 'chat-stop').exists():
            break
        time.sleep(.2)
finally:
    wire.close()
