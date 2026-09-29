#!/usr/bin/env python3
"""Amostra tabs/panes do workspace Herdr a cada 0.5s e imprime só quando o estado muda."""

import json
import os
import subprocess
import time

WS = os.environ.get("HERDR_WORKSPACE_ID", "")
DURATION = float(os.environ.get("MEISTER_E2E_SAMPLE_SECS", "120"))


def herdr(*args):
    try:
        r = subprocess.run(["herdr", *args], capture_output=True, text=True, timeout=5)
        return json.loads(r.stdout)["result"]
    except Exception:
        return None


def snapshot():
    tabs = herdr("tab", "list", "--workspace", WS)
    panes = herdr("pane", "list", "--workspace", WS)
    if not tabs or not panes:
        return None
    t = sorted((x["tab_id"], str(x.get("label"))) for x in tabs.get("tabs", []))
    p = sorted(
        (x["pane_id"], x.get("tab_id"), os.path.basename((x.get("cwd") or "").rstrip("/")))
        for x in panes.get("panes", [])
    )
    return t, p


def main():
    last = None
    t0 = time.time()
    while time.time() - t0 < DURATION:
        s = snapshot()
        if s is not None and s != last:
            print(f"[+{time.time() - t0:5.1f}s] tabs={s[0]}", flush=True)
            for pane in s[1]:
                print(f"           pane {pane[0]} tab={pane[1]} cwd={pane[2]}", flush=True)
            last = s
        time.sleep(0.5)


if __name__ == "__main__":
    main()
