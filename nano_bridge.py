#!/usr/bin/env python3
"""
AiGAIN — nanoKONTROL2 bridge
Fader 1 (CC 0):  intensity
Fader 2 (CC 1):  depth
Fader 4 (CC 3):  room (verbosity)
S1 (CC 32): EXPLORE mode
R1 (CC 64): BUILD mode
"""

import json, sys, time, urllib.request
from pathlib import Path

try:
    import mido
except ImportError:
    sys.exit("[aigain-nano] mido not installed: pip3 install mido python-rtmidi")

STATE_FILE = Path.home() / ".aigain" / "state.json"
STATE_FILE.parent.mkdir(exist_ok=True)
AIGAIN_URL = "http://127.0.0.1:5571/set"

CC_MAP = {
    0:  "intensity",
    1:  "depth",
    3:  "room",
}
CC_EXPLORE = 32
CC_BUILD   = 64

def read_state() -> dict:
    try:    return json.loads(STATE_FILE.read_text())
    except: return {"intensity": 0.6, "depth": 0.5, "room": 0.4, "mode": "BUILD"}

def write_state(s: dict):
    STATE_FILE.write_text(json.dumps(s))

def push(s: dict):
    try:
        body = json.dumps(s).encode()
        req  = urllib.request.Request(AIGAIN_URL, data=body,
                                      headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=2)
    except Exception as e:
        print(f"[aigain-nano] push failed: {e}")

def find_port() -> str | None:
    for p in mido.get_input_names():
        if "nanoKONTROL" in p or "nano" in p.lower():
            return p
    return None

def run():
    port_name = find_port()
    if not port_name:
        print("[aigain-nano] nanoKONTROL2 not found — waiting...")
        while not (port_name := find_port()):
            time.sleep(2)

    print(f"[aigain-nano] connected: {port_name}")
    state = read_state()

    with mido.open_input(port_name) as port:
        for msg in port:
            if msg.type != "control_change":
                continue
            cc, val = msg.control, msg.value

            if cc in CC_MAP:
                state[CC_MAP[cc]] = round(val / 127, 3)
                write_state(state)
                push(state)
                print(f"  {CC_MAP[cc]} → {state[CC_MAP[cc]]:.2f}")

            elif cc == CC_EXPLORE:
                if val > 0:
                    state["mode"] = "EXPLORE"
                    write_state(state); push(state)
                    print("  mode → EXPLORE")

            elif cc == CC_BUILD:
                if val > 0:
                    state["mode"] = "BUILD"
                    write_state(state); push(state)
                    print("  mode → BUILD")

if __name__ == "__main__":
    run()
