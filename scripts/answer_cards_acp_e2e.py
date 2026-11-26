#!/usr/bin/env python3
"""Real-path check: spawn `clover acp` against CLOVER_HOME, send a prompt, print the
`_meta.clover.card` records seen on tool-call updates.
Usage: CLOVER_HOME=<temp> python scripts/answer_cards_acp_e2e.py "show the weather"
"""
import json
import subprocess
import sys

prompt = sys.argv[1] if len(sys.argv) > 1 else "show the weather"
p = subprocess.Popen(
    [sys.executable, "-c", "import sys;from clover_cli.main import main;sys.argv=['clover','acp'];main()"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
)


def send(i, method, params):
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n")
    p.stdin.flush()


def read_until(i):
    cards = []
    for line in p.stdout:
        j = json.loads(line)
        u = j.get("params", {}).get("update", {})
        c = ((u.get("_meta") or {}).get("clover") or {}).get("card")
        if c:
            cards.append(c)
        if j.get("id") == i:
            return j, cards
    return {}, cards


send(1, "initialize", {"protocolVersion": 1})
read_until(1)
send(2, "session/new", {"cwd": ".", "mcpServers": []})
r, _ = read_until(2)
send(3, "session/prompt", {"sessionId": r["result"]["sessionId"], "prompt": [{"type": "text", "text": prompt}]})
_, cards = read_until(3)
print(json.dumps(cards))
p.stdin.close()
p.terminate()
sys.exit(0 if cards else 1)
