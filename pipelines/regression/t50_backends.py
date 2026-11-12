#!/usr/bin/env python3
"""Backend and session checks: the wrapper backends start, share and stop their own Resolve.

Not part of the shared live session: run.sh calls this module after the live block, when no Resolve is running.
Every check starts its own session through ../mcp-benchmark/resolve_mcp_wrapper.sh and must leave nothing behind:

  shared nogui session   native-nogui + nogui share one -nogui Resolve on Xvfb :99; the last client out stops both
  mcp_filter             native-nogui hides and refuses run_script_unsafe; run_script answers and stays sandboxed
  headless SIGTERM       the headless backend stops the Resolve it started when its client process gets SIGTERM
  forced command         the SSH forced command (~/bin/mcp_dispatch.sh) under an empty environment: both Resolve entries,
                         and an arbitrary command, which must still get an MCP server (never a shell)
  GUI under Xvfb         (only with --gui-xvfb) the xvfb backend runs a GUI Resolve on :99 and stops it on close

A Resolve already running anywhere, an Xvfb or an X socket on :99, or a live client registered with the shared
session fails the module before anything starts: a person's session is never touched (SSH MCP clients must not
connect while t50 runs). Every client must exit by itself on EOF (a client that needs SIGTERM/SIGKILL is a FAIL),
and "nothing left" also covers the MCP server processes this module started and the session's client registry.
A Resolve still up 60 s after its clients closed is reported and left for the operator (never killed from here).
The servers' stderr goes to t50_<label>.log next to the reports (rlib.REPORTS); a FAIL quotes its last lines.

Usage: t50_backends.py [--gui-xvfb]
"""
import argparse
import os
import pwd
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import rlib
from rlib import Skip, Warn, ca, expect, run_script

DISPLAY = ":99"                              # resolve_xvfb_stdio.py's default display
CLIENTS = Path(f"/tmp/resolve_xvfb_{DISPLAY.lstrip(':')}.clients")   # the shim's client registry
X_SOCKET = Path(f"/tmp/.X11-unix/X{DISPLAY.lstrip(':')}")
QUIT_LIMIT = 30          # seconds from the last client closing to no Resolve and no Xvfb left
HARD_LIMIT = 60          # after this a leftover is reported and left alone
BOOT_TIMEOUT = 300       # the first MCP answer waits for Resolve to boot behind the shim
CALL_TIMEOUT = 90
UNSAFE = "run_script_unsafe"


# ---------------------------------------------------------------- processes

cmdline = rlib.cmdline


def resolve_procs():
    return rlib.resolve_processes()


def xvfb_pids():
    return rlib.xvfb_pids(DISPLAY)


def live_clients():
    """Pids registered with the shared session that are still alive."""
    try:
        names = [n for n in os.listdir(CLIENTS) if n.isdigit()]
    except OSError:
        return []
    alive = []
    for n in names:
        try:
            os.kill(int(n), 0)
            alive.append(int(n))
        except ProcessLookupError:
            pass
        except PermissionError:
            alive.append(int(n))
    return alive


def own_processes():
    """Processes in this module's process group other than itself: the clients and the MCP servers and shims
    they started (Resolve and Xvfb are started in sessions of their own and are checked separately)."""
    me, group, out = os.getpid(), os.getpgrp(), []
    for n in os.listdir("/proc"):
        if not n.isdigit() or int(n) == me:
            continue
        try:
            stat = Path(f"/proc/{n}/stat").read_text()
        except OSError:
            continue
        fields = stat[stat.rfind(")") + 2:].split()
        if len(fields) > 2 and fields[0] != "Z" and int(fields[2]) == group:
            out.append(f"pid {n} {' '.join(cmdline(int(n)))[:80]}")
    return out


def leftovers():
    """'' when no Resolve (on any display), no Xvfb on :99, no server process of this module and no live
    registered client is left, else what is."""
    parts = [f"Resolve pid {p['pid']} ({'-nogui' if p['nogui'] else 'GUI'}, DISPLAY={p['display']})" for p in resolve_procs()]
    parts += [f"Xvfb {DISPLAY} pid {pid}" for pid in xvfb_pids()]
    parts += [f"server process {x}" for x in own_processes()]
    parts += [f"registered client pid {pid} in {CLIENTS.name}" for pid in live_clients()]
    return ", ".join(parts)


def require_clean():
    left = leftovers()
    if left:
        raise AssertionError(f"not starting: {left} is still up (left by an earlier check?)")


def wait_clean(t0, hard=HARD_LIMIT):
    """Seconds from t0 until nothing is left, or None if something is still up `hard` seconds after t0."""
    while True:
        elapsed = time.monotonic() - t0
        if not leftovers():
            return elapsed
        if elapsed >= hard:
            return None
        time.sleep(0.5)


def expect_gone(elapsed, limit):
    if elapsed is None:
        raise AssertionError(f"still up {HARD_LIMIT}s after the last client closed: {leftovers()}; left for the operator")
    expect(elapsed <= limit, f"clean-up took {elapsed:.1f}s (> {limit}s)")
    return elapsed


def reap(proc, grace):
    """Wait for a client process; then SIGTERM (the shims clean up on it); SIGKILL only as the last resort.
    -> None when it exited by itself, else the signal that was needed ("SIGTERM" / "SIGKILL")."""
    try:
        proc.wait(timeout=max(grace, 1))
        return None
    except subprocess.TimeoutExpired:
        pass
    proc.terminate()
    try:
        proc.wait(timeout=30)
        return "SIGTERM"
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        return "SIGKILL"


# ---------------------------------------------------------------- clients and sessions

def child_env():
    env = dict(os.environ)
    # the checks assert the default display and the default block list
    for k in ("RESOLVE_XVFB_DISPLAY", "RESOLVE_XVFB_NOGUI", "RESOLVE_MCP_NATIVE_BLOCK"):
        env.pop(k, None)
    return env


class Client(rlib.McpStdio):
    """McpStdio that does not leak its server process when the MCP handshake fails, and keeps the server's stderr
    (the shims' REFUSE / FAILED lines) in a log file."""

    def __init__(self, label, argv):
        self.label = label
        rlib.REPORTS.mkdir(parents=True, exist_ok=True)
        self.log_path = rlib.REPORTS / ("t50_" + re.sub(r"\W+", "_", label) + ".log")
        self.log = open(self.log_path, "w")
        try:
            super().__init__([str(a) for a in argv], env=child_env(), timeout=BOOT_TIMEOUT, stderr=self.log)
        except Exception as exc:
            if getattr(self, "p", None) is not None:
                self.begin_close()
                reap(self.p, 60)
            raise RuntimeError(f"{label}: no MCP session ({type(exc).__name__}: {exc}); stderr: {self.tail()}") from exc

    def tail(self, n=4):
        try:
            self.log.flush()
            lines = [l for l in self.log_path.read_text(errors="replace").splitlines() if l.strip()]
        except OSError:
            return "?"
        return " | ".join(lines[-n:])[-400:] or "(empty)"

    def begin_close(self):
        try:
            self.p.stdin.close()
        except Exception:
            pass


class Session:
    """The clients of one check. On the way out any client still open is closed and the check fails if a Resolve or
    the Xvfb is still up HARD_LIMIT seconds later (left for the operator)."""

    def __init__(self):
        self.clients = []

    def open(self, label, argv):
        c = Client(label, argv)
        self.clients.append(c)
        return c

    def close(self, c, grace=60):
        """Close one client; it must exit by itself with code 0."""
        c.begin_close()
        forced = reap(c.p, grace)
        self.clients.remove(c)
        expect(forced is None, f"{c.label} did not exit on EOF within {grace}s and needed {forced}; stderr: {c.tail()}")
        expect(c.p.returncode == 0, f"{c.label} exited with code {c.p.returncode} on EOF; stderr: {c.tail()}")
        return c.p.returncode

    def forget(self, c, grace=30):
        forced = reap(c.p, grace)
        self.clients.remove(c)
        return forced

    def close_all(self):
        """Close every client at once; seconds until nothing is left (None: still up after HARD_LIMIT). Every
        client must exit by itself with code 0 (checked after the wait, so a leftover is reported first)."""
        t0 = time.monotonic()
        for c in self.clients:
            c.begin_close()
        bad = []
        for c in self.clients:
            forced = reap(c.p, HARD_LIMIT - (time.monotonic() - t0))
            if forced or c.p.returncode != 0:
                bad.append(f"{c.label}: {'needed ' + forced if forced else 'exit ' + str(c.p.returncode)} ({c.tail(2)})")
        elapsed = wait_clean(t0)
        self.clients.clear()
        if bad and elapsed is not None:
            raise AssertionError("client(s) did not close cleanly on EOF: " + "; ".join(bad))
        return elapsed

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.clients:
            try:
                elapsed = self.close_all()
            except AssertionError as late:
                if exc is not None:
                    raise AssertionError(f"{type(exc).__name__}: {exc}; and {late}") from exc
                raise
        elif exc is not None and not isinstance(exc, Skip):
            elapsed = wait_clean(time.monotonic())   # e.g. a handshake that failed after the shim started Resolve
        else:
            return False
        if elapsed is not None:
            return False
        msg = f"still up {HARD_LIMIT}s after closing the clients: {leftovers()}; left for the operator"
        if exc is not None:
            raise AssertionError(f"{type(exc).__name__}: {exc}; and {msg}") from exc
        raise AssertionError(msg)


# ---------------------------------------------------------------- capability gates

def wrapper_backends():
    try:
        text = rlib.WRAPPER.read_text()
    except OSError:
        return set()
    names = set()
    for m in re.finditer(r"^\s*([\w|-]+)\)\s*$", text, re.M):
        names.update(n for n in m.group(1).split("|") if n and n != "*")
    return names


def need_backends(*names):
    missing = [n for n in names if n not in wrapper_backends()]
    if missing:
        raise Skip(f"resolve_mcp_wrapper.sh has no backend {', '.join(missing)}")


def need_xvfb():
    if not shutil.which("Xvfb"):
        raise Skip("Xvfb is not installed")


def need_vendor():
    binary = os.environ.get("RESOLVE_MCP_NATIVE_BINARY", "/opt/resolve/bin/ResolveMCP")
    if not os.access(binary, os.X_OK):
        raise Skip(f"no vendor MCP server at {binary} (Resolve without ResolveMCP)")


# ---------------------------------------------------------------- MCP probes

def vendor_version(v):
    ver = run_script(v, "result = resolve.GetVersionString()", timeout=30)
    expect(isinstance(ver, str) and re.match(r"^\d+\.\d+", ver), f"vendor GetVersionString -> {ver!r}")
    return ver


def community_version(c):
    is_error, data, _ = ca(c, "resolve_control", "get_version", timeout=CALL_TIMEOUT)
    expect(not is_error and isinstance(data, dict) and data.get("version_string"),
           f"community get_version -> {str(data)[:200]}")
    return data["version_string"]


def one_resolve(display=None):
    procs = resolve_procs()
    expect(len(procs) == 1, f"expected exactly one Resolve process, found {len(procs)}: {procs}")
    p = procs[0]
    if display is not None:
        expect(p["display"] == display, f"Resolve pid {p['pid']} runs on DISPLAY={p['display']}, not {display}")
    return p


def expect_refused(v, tool):
    """Refused by mcp_filter itself (its message), not by a vendor that happens not to know the tool."""
    is_error, data, _ = v.call(tool, {"script": "result = 1"}, timeout=CALL_TIMEOUT)
    expect(is_error and isinstance(data, dict) and data.get("code") == -32601
           and "is not available through this connection" in str(data.get("message", "")),
           f"direct {tool} call was not refused by mcp_filter (-32601, 'not available through this connection'): {str(data)[:200]}")


# ---------------------------------------------------------------- checks

def check_preconditions():
    left = leftovers()
    expect(not left, f"refusing to run: {left} (never touch a running Resolve or a session in use; quit it first)")
    expect(not X_SOCKET.exists(), f"refusing to run: {X_SOCKET} exists without our Xvfb ({DISPLAY} is taken by "
                                  "something else, or a stale socket is left)")
    return f"no Resolve, no Xvfb or X socket on {DISPLAY}, no registered client"


def check_shared_nogui():
    need_backends("native-nogui", "nogui")
    need_xvfb()
    need_vendor()
    require_clean()
    with Session() as s:
        v = s.open("native-nogui", [rlib.WRAPPER, "native-nogui"])
        c = s.open("nogui", [rlib.WRAPPER, "nogui"])
        vv, cv = vendor_version(v), community_version(c)
        expect(vv == cv, f"the two servers report different Resolve versions: vendor {vv!r}, community {cv!r}")
        p = one_resolve(DISPLAY)
        expect(p["nogui"], f"the shared Resolve (pid {p['pid']}) was not started with -nogui: {cmdline(p['pid'])}")
        expect(len(xvfb_pids()) == 1, f"expected one Xvfb on {DISPLAY}, found {xvfb_pids()}")
        rc = s.close(c)
        after = vendor_version(v)
        expect(after == vv, f"vendor answer changed after the community client left: {after!r}")
        pids = [q["pid"] for q in resolve_procs()]
        expect(pids == [p["pid"]], f"Resolve did not stay up unchanged when the community client left: {pids}")
        expect(xvfb_pids(), f"Xvfb {DISPLAY} went away while the vendor client was still connected")
        elapsed = expect_gone(s.close_all(), QUIT_LIMIT)
    return (f"both answer {vv}; one -nogui Resolve (pid {p['pid']}) on {DISPLAY}; community left (rc {rc}) and the "
            f"vendor still answers on the same pid; vendor left -> nothing up after {elapsed:.1f}s")


def check_filter():
    need_backends("native-nogui", "native")
    need_xvfb()
    need_vendor()
    if not (rlib.MCP_BENCH / "mcp_filter.py").exists():
        raise Skip("mcp_filter.py is not in mcp-benchmark")
    require_clean()
    with Session() as s:
        v = s.open("native-nogui", [rlib.WRAPPER, "native-nogui"])
        filtered = v.tools()
        raw = s.open("native", [rlib.WRAPPER, "native"])   # the bare vendor binary: tools/list only, never a call
        unfiltered = raw.tools()
        s.close(raw)
        if UNSAFE not in unfiltered:
            raise Skip(f"the vendor server no longer lists {UNSAFE}: nothing to filter ({len(unfiltered)} tools)")
        hidden = set(unfiltered) - set(filtered)
        expect(hidden == {UNSAFE}, f"the filter hid {sorted(hidden)}, expected only {UNSAFE}")
        expect(set(filtered) <= set(unfiltered), f"filtered list has extra tools: {sorted(set(filtered) - set(unfiltered))}")
        expect("run_script" in filtered, "run_script is missing from the filtered list")
        expect_refused(v, UNSAFE)
        ver = vendor_version(v)   # control: run_script works through the filter
        is_error, data, _ = v.call("run_script", {"script": "import os\nresult = os.getcwd()", "timeout": 20}, timeout=60)
        expect(is_error and re.search(r"PermissionError: import not allowed: os", str(data)),
               f"run_script `import os` was not refused by the sandbox: isError={is_error} {str(data)[-200:]}")
        elapsed = expect_gone(s.close_all(), QUIT_LIMIT)
    return (f"{len(filtered)}/{len(unfiltered)} tools listed, {UNSAFE} hidden and refused (-32601); run_script answers "
            f"({ver}) and refuses `import os`; nothing up {elapsed:.1f}s after close")


def check_headless_sigterm():
    need_backends("headless")
    displays = [n for n in os.listdir("/tmp/.X11-unix") if n != "X" + DISPLAY.lstrip(":")] if os.path.isdir("/tmp/.X11-unix") else []
    if not displays:
        raise Skip("the headless backend binds Resolve to the desktop's X display and none is up")
    require_clean()
    with Session() as s:
        c = s.open("headless", [rlib.WRAPPER, "headless"])
        ver = community_version(c)
        p = one_resolve()
        expect(p["nogui"], f"the headless Resolve (pid {p['pid']}) was not started with -nogui: {cmdline(p['pid'])}")
        shim = cmdline(c.p.pid)
        expect(any(a.endswith("resolve_headless_stdio.py") for a in shim),
               f"client pid {c.p.pid} is not the headless shim (wrapper no longer execs it?): {shim}")
        t0 = time.monotonic()
        os.kill(c.p.pid, signal.SIGTERM)
        elapsed = wait_clean(t0)
        forced = s.forget(c, grace=10)
        rc = c.p.returncode
        expect_gone(elapsed, QUIT_LIMIT)
        expect(forced is None, f"the headless shim did not exit after SIGTERM and needed {forced}; stderr: {c.tail()}")
        expect(rc in (-signal.SIGTERM, 128 + signal.SIGTERM), f"headless shim exit code {rc} after SIGTERM, want 143")
    return f"get_version {ver} on a -nogui Resolve (pid {p['pid']}, DISPLAY={p['display']}); SIGTERM -> shim rc {rc}, nothing up after {elapsed:.1f}s"


def check_dispatcher():
    dispatcher = Path.home() / "bin" / "mcp_dispatch.sh"
    if not os.access(dispatcher, os.X_OK):
        raise Skip("no SSH forced-command dispatcher (~/bin/mcp_dispatch.sh) on this host")
    need_xvfb()
    need_vendor()
    require_clean()
    user = pwd.getpwuid(os.getuid()).pw_name
    # RESOLVE_MCP_WRAPPER: the dispatcher's own variable, so it runs this checkout's wrapper, the one under test
    base = ["env", "-i", f"HOME={Path.home()}", f"USER={user}", f"LOGNAME={user}", "PATH=/usr/local/bin:/usr/bin:/bin",
            f"RESOLVE_MCP_WRAPPER={rlib.WRAPPER}"]
    with Session() as s:
        v = s.open("dispatch davinci-native", base + ["SSH_ORIGINAL_COMMAND=davinci-native", dispatcher])
        c = s.open("dispatch davinci", base + ["SSH_ORIGINAL_COMMAND=davinci", dispatcher])
        x = s.open("dispatch sh -c id", base + ["SSH_ORIGINAL_COMMAND=sh -c id", dispatcher])
        vtools, ctools, xtools = v.tools(), c.tools(), x.tools()
        expect("resolve_control" in xtools and "run_script" not in xtools,
               f"an arbitrary SSH command did not get the community MCP server: {sorted(xtools)[:10]}")
        expect(UNSAFE not in vtools and "run_script" in vtools,
               f"davinci-native tools: {UNSAFE} listed={UNSAFE in vtools}, run_script listed={'run_script' in vtools}")
        expect_refused(v, UNSAFE)
        expect("resolve_control" in ctools and "run_script" not in ctools,
               f"davinci did not start the community server: {sorted(ctools)[:10]}")
        vv, cv = vendor_version(v), community_version(c)
        expect(vv == cv, f"the two entries report different Resolve versions: {vv!r} vs {cv!r}")
        p = one_resolve(DISPLAY)
        # env -i: no backend override can apply, so a GUI Resolve means the dispatcher's own default changed
        expect(p["nogui"], f"the dispatcher ran Resolve with its GUI, not -nogui (pid {p['pid']}): {cmdline(p['pid'])}")
        elapsed = expect_gone(s.close_all(), QUIT_LIMIT)
    return (f"davinci-native ({len(vtools)} tools, {UNSAFE} hidden and refused), davinci and 'sh -c id' (community, "
            f"{len(ctools)} tools) share one -nogui Resolve {vv} on {DISPLAY}; nothing up {elapsed:.1f}s after all closed")


def check_gui_xvfb(enabled):
    if not enabled:
        raise Skip("GUI Resolve under Xvfb: pass --gui-xvfb to run it")
    need_backends("xvfb")
    need_xvfb()
    require_clean()
    with Session() as s:
        c = s.open("xvfb", [rlib.WRAPPER, "xvfb"])
        ver = community_version(c)
        p = one_resolve(DISPLAY)
        expect(not p["nogui"], "the xvfb backend started Resolve with -nogui (a -nogui session was reused?)")
        expect(len(xvfb_pids()) == 1, f"expected one Xvfb on {DISPLAY}, found {xvfb_pids()}")
        is_error, data, _ = ca(c, "resolve_control", "get_page", timeout=CALL_TIMEOUT)   # read only; never open_page
        page = data.get("page") if isinstance(data, dict) else None
        # a freshly booted GUI Resolve with no project may report no page; only the Color page is wrong here
        expect(not is_error and page != "color", f"get_page -> {str(data)[:200]}")
        elapsed = expect_gone(s.close_all(), HARD_LIMIT)
    return f"GUI Resolve {ver} (pid {p['pid']}) on {DISPLAY}, page {page!r}; nothing up {elapsed:.1f}s after close"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--gui-xvfb", action="store_true", help="also start a GUI Resolve under Xvfb (the xvfb backend)")
    args = ap.parse_args()
    suite = rlib.Suite("t50_backends")
    if suite.check("preconditions: nothing running", check_preconditions) != "OK":
        raise SystemExit(suite.finish())
    suite.check("shared -nogui session (native-nogui + nogui)", check_shared_nogui)
    suite.check("mcp_filter on native-nogui", check_filter)
    suite.check("headless backend stops Resolve on SIGTERM", check_headless_sigterm)
    suite.check("SSH forced command (mcp_dispatch.sh)", check_dispatcher)
    suite.check("xvfb backend: GUI Resolve under Xvfb", lambda: check_gui_xvfb(args.gui_xvfb))
    raise SystemExit(suite.finish())


if __name__ == "__main__":
    main()
