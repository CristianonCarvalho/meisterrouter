"""
meister.herdr.tui — Terminal User Interface (TUI) overlay for Herdr.

Renders real-time telemetry, multi-model worker statuses, token counts,
and deterministic cost savings comparisons inside the Herdr overlay pane.
"""

import os
import sys
import time
import select
import termios
import tty
import webbrowser
from typing import Dict, Any, Optional, Tuple, List

from meister.logger import read_events
from meister.dashboard.server import compute_metrics
from meister.i18n import t


# ANSI Color & Formatting Constants
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
RED = "\033[31m"
WHITE = "\033[37m"
CLEAR_SCREEN = "\033[2J\033[H"


def render_tui_dashboard(session_state: Optional[Dict[str, Any]] = None,
                         cost_metrics: Optional[Dict[str, Any]] = None) -> str:
    """
    Renders an ANSI formatted text dashboard for the Herdr overlay pane.

    Args:
        session_state: Active session state dictionary.
        cost_metrics: Cost and token metrics dictionary.

    Returns:
        Formatted multi-line ANSI/Unicode string.
    """
    state = session_state or {}
    metrics = cost_metrics or {}

    workspace = state.get("workspace", os.path.basename(os.getcwd()) or "default")
    status = state.get("status", "IDLE")
    active_worker = state.get("active_worker", "none")
    task_desc = state.get("task_desc", "Waiting for tasks...")

    tokens = metrics.get("tokens", metrics.get("total_tokens", 0))
    cost = metrics.get("cost", metrics.get("total_cost", 0.0))
    traditional_cost = metrics.get("traditional_cost", metrics.get("baseline_cost", 0.0))
    savings_pct = metrics.get("savings_pct", 0.0)
    savings_usd = metrics.get("savings_usd", max(0.0, traditional_cost - cost))

    # Width of the dashboard box
    width = 76
    inner_width = width - 4

    lines = []
    lines.append(f"{CYAN}╔{'═' * (width - 2)}╗{RESET}")
    title = "MeisterRouter Live Telemetry & Cost Dashboard"
    lines.append(f"{CYAN}║{RESET} {BOLD}{WHITE}{title:^{inner_width}}{RESET} {CYAN}║{RESET}")
    lines.append(f"{CYAN}╠{'═' * (width - 2)}╣{RESET}")

    # Section 1: Active Session
    lines.append(f"{CYAN}║{RESET} {BOLD}{YELLOW}ACTIVE SESSION:{RESET}{' ' * (inner_width - 15)} {CYAN}║{RESET}")
    ws_line = f"  Workspace     : {BOLD}{workspace}{RESET}"
    lines.append(f"{CYAN}║{RESET} {ws_line:<{inner_width + 8}} {CYAN}║{RESET}")
    st_line = f"  Status        : {GREEN if status in ['ORCHESTRATING', 'RUNNING'] else WHITE}{status}{RESET}"
    lines.append(f"{CYAN}║{RESET} {st_line:<{inner_width + 8}} {CYAN}║{RESET}")
    aw_line = f"  Active Worker : {MAGENTA}{active_worker}{RESET}"
    lines.append(f"{CYAN}║{RESET} {aw_line:<{inner_width + 8}} {CYAN}║{RESET}")
    desc_line = f"  Task          : {task_desc[:55]}"
    lines.append(f"{CYAN}║{RESET} {desc_line:<{inner_width}} {CYAN}║{RESET}")

    lines.append(f"{CYAN}╟{'─' * (width - 2)}╢{RESET}")

    # Section 2: Worker Statuses (if provided or present)
    workers: List[Dict[str, Any]] = state.get("workers", [])
    if workers:
        lines.append(f"{CYAN}║{RESET} {BOLD}{BLUE}WORKERS STATUS:{RESET}{' ' * (inner_width - 15)} {CYAN}║{RESET}")
        lines.append(f"{CYAN}║{RESET}   {'ID':<6} {'NAME':<14} {'MODEL':<18} {'STATUS':<12} {'TASK':<18} {CYAN}║{RESET}")
        lines.append(f"{CYAN}║{RESET}   {'-' * 4:<6} {'-' * 12:<14} {'-' * 16:<18} {'-' * 10:<12} {'-' * 16:<18} {CYAN}║{RESET}")
        for w in workers[:5]:
            wid = str(w.get("id", ""))[:5]
            wname = str(w.get("name", wid))[:13]
            wmodel = str(w.get("model", "-"))[:17]
            wstatus = str(w.get("status", "IDLE"))[:11]
            wtask = str(w.get("task", ""))[:17]
            color = GREEN if wstatus in ["RUNNING", "ACTIVE"] else (YELLOW if wstatus == "WAITING" else WHITE)
            w_row = f"   {wid:<6} {wname:<14} {wmodel:<18} {color}{wstatus:<12}{RESET} {wtask:<18}"
            lines.append(f"{CYAN}║{RESET}{w_row:<{inner_width + 8}} {CYAN}║{RESET}")
        lines.append(f"{CYAN}╟{'─' * (width - 2)}╢{RESET}")

    # Section 3: Cost Savings & Telemetry
    lines.append(f"{CYAN}║{RESET} {BOLD}{GREEN}COST SAVINGS & TOKEN METRICS:{RESET}{' ' * (inner_width - 29)} {CYAN}║{RESET}")
    tok_line = f"  Tokens Processed : {tokens:,}"
    lines.append(f"{CYAN}║{RESET} {tok_line:<{inner_width}} {CYAN}║{RESET}")
    cst_line = f"  Meister Cost     : ${cost:.4f}"
    lines.append(f"{CYAN}║{RESET} {cst_line:<{inner_width}} {CYAN}║{RESET}")
    if metrics.get("savings") is None and "savings" in metrics:
        trad_line = f"  Traditional Cost : {DIM}not measured (no baseline){RESET}"
    else:
        trad_line = f"  Traditional Cost : ${traditional_cost:.4f} (Single-Model Baseline)"
    lines.append(f"{CYAN}║{RESET} {trad_line:<{inner_width}} {CYAN}║{RESET}")
    if metrics.get("savings") is None and "savings" in metrics:
        sav_line = f"  Estimated Savings: {BOLD}{DIM}not measured{RESET}"
    else:
        sav_line = f"  Estimated Savings: {BOLD}{GREEN}{savings_pct:.1f}% (${savings_usd:.4f}){RESET}"
    lines.append(f"{CYAN}║{RESET} {sav_line:<{inner_width + 12}} {CYAN}║{RESET}")

    lines.append(f"{CYAN}╠{'═' * (width - 2)}╣{RESET}")

    # Section 4: Key Shortcuts & Controls
    shortcuts = t("reports.tui.shortcuts")
    lines.append(f"{CYAN}║{RESET} {DIM}{shortcuts:^{inner_width}}{RESET} {CYAN}║{RESET}")
    lines.append(f"{CYAN}╚{'═' * (width - 2)}╝{RESET}")

    return "\n".join(lines)


def get_live_metrics_and_state() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Computes current metrics and state from logged telemetry events.
    """
    events = read_events(limit=500)
    summary = compute_metrics(events)

    total_tokens = 0
    active_worker = "none"
    last_task_desc = "None"
    status = "IDLE"
    workers_map: Dict[str, Dict[str, Any]] = {}

    for ev in events:
        etype = ev.get("event_type")
        tokens_in = ev.get("tokens_in", 0)
        tokens_out = ev.get("tokens_out", 0)
        total_tokens += (tokens_in + tokens_out)

        if etype == "classify":
            last_task_desc = ev.get("context", "Task")[:60]
            active_worker = ev.get("recommended_implementer", active_worker)
            status = "ORCHESTRATING"
        elif etype == "task_start":
            sub = ev.get("subagent", "worker")
            status = "RUNNING"
            active_worker = sub
            workers_map[sub] = {
                "id": ev.get("task_id", sub)[:6],
                "name": ev.get("instance_label", sub),
                "model": sub,
                "status": "RUNNING",
                "task": last_task_desc,
            }
        elif etype == "task_end":
            sub = ev.get("subagent", "worker")
            if sub in workers_map:
                workers_map[sub]["status"] = "COMPLETED"

    state: Dict[str, Any] = {
        "workspace": os.path.basename(os.getcwd()) or "default",
        "status": status,
        "active_worker": active_worker,
        "task_desc": last_task_desc,
        "workers": list(workers_map.values()),
    }

    metrics: Dict[str, Any] = {
        "tokens": total_tokens,
        "cost": summary.get("total_cost", 0.0),
        "savings": None,
    }

    return state, metrics


def open_browser(url: str = "http://127.0.0.1:5050") -> None:
    """Opens the web browser dashboard."""
    try:
        webbrowser.open(url)
    except Exception:
        pass


def check_key_press(timeout: float = 0.0) -> Optional[str]:
    """
    Non-blocking check for a single key press from stdin.
    Returns character if pressed, or None.
    """
    if not sys.stdin.isatty():
        return None

    try:
        fd = sys.stdin.fileno()
        orig = termios.tcgetattr(fd)
        is_canonical = bool(orig[3] & termios.ICANON)
        if is_canonical:
            tty.setcbreak(fd)

        try:
            rlist, _, _ = select.select([sys.stdin], [], [], timeout)
            if rlist:
                ch = sys.stdin.read(1)
                if ch == "\x1b":
                    r2, _, _ = select.select([sys.stdin], [], [], 0.05)
                    if r2:
                        sys.stdin.read(2)
                        return None
                    return "\x1b"
                return ch
            return None
        finally:
            if is_canonical:
                termios.tcsetattr(fd, termios.TCSADRAIN, orig)
    except Exception:
        return None


def run_tui_loop(poll_interval: float = 1.0,
                 max_iterations: Optional[int] = None) -> None:
    """
    Runs the TUI dashboard loop, refreshing output until interrupted or Q pressed.

    Args:
        poll_interval: Seconds between screen refreshes.
        max_iterations: Optional loop limit (useful for testing).
    """
    old_settings = None
    fd = None
    if sys.stdin.isatty():
        try:
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        except Exception:
            old_settings = None
            fd = None

    iterations = 0
    try:
        while True:
            state, metrics = get_live_metrics_and_state()
            output = render_tui_dashboard(state, metrics)

            # Clear screen and print rendered TUI
            sys.stdout.write(CLEAR_SCREEN)
            sys.stdout.write(output + "\n")
            sys.stdout.flush()

            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break

            # Poll for key press during interval
            start_time = time.monotonic()
            while True:
                remaining = poll_interval - (time.monotonic() - start_time)
                if remaining <= 0:
                    break
                key = check_key_press(timeout=min(0.2, remaining))
                if key:
                    key_lower = key.lower()
                    if key_lower in ("q", "x") or key in ("\x1b", "\x03", "\x04"):
                        sys.stdout.write("\nClosing TUI dashboard overlay...\n")
                        sys.stdout.flush()
                        return
                    elif key_lower == "o":
                        open_browser("http://127.0.0.1:5050")
                    elif key_lower == "t":
                        from meister.timeline_cli import open_timeline

                        open_timeline()
                        break

    except (KeyboardInterrupt, SystemExit):
        sys.stdout.write("\nClosing TUI dashboard overlay...\n")
        sys.stdout.flush()
    finally:
        if fd is not None and old_settings is not None:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            except Exception:
                pass
