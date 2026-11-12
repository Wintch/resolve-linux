"""Shared plumbing for the regression suite (see README.md).

Every test module builds a `Suite`, registers checks with `suite.check(label, fn)` and ends with
`raise SystemExit(suite.finish())`. A check returns a short detail string (OK), raises `Skip` (cannot
run in this setup: older Resolve, missing tool), raises `Warn` (ran, but something changed that a
person should look at, e.g. a baseline drift) or raises anything else (FAIL).

Live modules talk to an already running Resolve (run.sh starts one shared -nogui session for them)
through the two MCP servers over stdio, with `McpStdio`. They only ever touch the disposable
MCP-Benchmark fixture project, and only through objects named with SCRATCH_PREFIX, which
`drop_scratch` removes again.
"""
import base64
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MCP_BENCH = HERE.parent / "mcp-benchmark"
WRAPPER = MCP_BENCH / "resolve_mcp_wrapper.sh"
BASELINES = HERE / "baselines"
FIXTURE_PROJECT = "MCP-Benchmark"
FIXTURE_TIMELINE = "bench_tl"          # 8 "Solid Color" generators, see mcp-benchmark/setup_bench_project.py
MEDIA_TIMELINE = "bench_tl_media"      # one real clip, built by validate_client.py
SCRATCH_PREFIX = "zz_reg_"
SCRATCH = Path(os.environ.get("REGRESSION_SCRATCH", Path.home() / "Videos" / "mcp-bench-scratch" / "regression"))
REPORTS = Path(os.environ.get("REGRESSION_REPORTS", SCRATCH / "reports"))
COMMUNITY_REPO = Path(os.environ.get("RESOLVE_MCP_COMMUNITY_REPO", Path.home() / "resolve-install" / "davinci-resolve-mcp"))
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class Skip(Exception):
    """Cannot run in this setup (not a failure)."""


class Warn(Exception):
    """Ran, but a person should look at the detail (e.g. baseline drift)."""


def expect(condition, detail):
    if not condition:
        raise AssertionError(detail)
    return detail


def sha(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:12]


class Suite:
    def __init__(self, name: str):
        self.name = name
        self.results = []   # (label, status, detail, seconds)

    def check(self, label, fn):
        t0 = time.monotonic()
        try:
            status, detail = "OK", str(fn())
        except Skip as exc:
            status, detail = "SKIP", str(exc)
        except Warn as exc:
            status, detail = "WARN", str(exc)
        except Exception as exc:   # a failing check never stops the suite
            status, detail = "FAIL", f"{type(exc).__name__}: {exc}"
        self.results.append((label, status, detail, round(time.monotonic() - t0, 2)))
        print(f"  {status:<4} {label}: {detail[:160]}", flush=True)
        return status

    def finish(self) -> int:
        counts = {s: sum(r[1] == s for r in self.results) for s in ("OK", "WARN", "SKIP", "FAIL")}
        REPORTS.mkdir(parents=True, exist_ok=True)
        out = REPORTS / f"{self.name}.json"
        out.write_text(json.dumps({"suite": self.name, "counts": counts, "results": [
            {"check": l, "status": s, "detail": d, "seconds": t} for l, s, d, t in self.results]}, indent=1))
        print(f"== {self.name}: " + ", ".join(f"{k} {v}" for k, v in counts.items()) + f"  ({out})", flush=True)
        return 1 if counts["FAIL"] else 0


class McpStdio:
    """Minimal synchronous MCP client over a stdio server process, with a timeout on every call
    (a scripting call can block forever, e.g. behind a dialog a -nogui Resolve cannot show)."""

    def __init__(self, argv, env=None, timeout=120, stderr=None):
        """stderr: a file object for the server's stderr (default: discarded)."""
        self.timeout = timeout
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=stderr if stderr is not None else subprocess.DEVNULL,
                                  text=True, env=env, bufsize=1)
        self._q = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()
        self._id = 0
        self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                "clientInfo": {"name": "resolve-linux-regression", "version": "1"}})
        self.rpc("notifications/initialized", notify=True)

    def _reader(self):
        for line in self.p.stdout:
            try:
                self._q.put(json.loads(line))
            except ValueError:
                continue
        self._q.put(None)

    def rpc(self, method, params=None, notify=False, timeout=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if not notify:
            self._id += 1
            msg["id"] = self._id
        if params is not None:
            msg["params"] = params
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()
        if notify:
            return None
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"{method}: no answer in {timeout or self.timeout}s")
            try:
                r = self._q.get(timeout=left)
            except queue.Empty:
                continue
            if r is None:
                raise ConnectionError(f"{method}: server closed the connection")
            if r.get("id") == self._id:
                return r

    def tools(self):
        return [t["name"] for t in self.rpc("tools/list")["result"]["tools"]]

    def call(self, name, arguments=None, timeout=None):
        """-> (is_error, data, images). data is the parsed JSON text when it parses, else the text.
        A JSON-RPC level error (e.g. a filtered tool) comes back as is_error with the error object."""
        r = self.rpc("tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout)
        if "error" in r:
            return True, r["error"], []
        res = r.get("result", {})
        text = "".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        images = [(c.get("mimeType"), base64.b64decode(c["data"])) for c in res.get("content", []) if c.get("type") == "image"]
        try:
            data = json.loads(text)
        except ValueError:
            data = text
        if isinstance(data, dict):
            data.pop("_operation", None)
        is_error = bool(res.get("isError")) or (isinstance(data, dict) and (bool(data.get("error")) or data.get("success") is False))
        return is_error, data, images

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=90)
        except Exception:
            self.p.kill()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def community(timeout=300):
    """The community server against the running Resolve (wrapper backend `community`)."""
    return McpStdio([str(WRAPPER), "community"], timeout=timeout)


def vendor(timeout=120):
    """The vendor ResolveMCP against the running Resolve (wrapper backend `native`), behind mcp_filter.py so that
    `run_script_unsafe` (full filesystem and subprocess access) is not reachable from any check."""
    return McpStdio([sys.executable, str(MCP_BENCH / "mcp_filter.py"), "--block", "run_script_unsafe", "--",
                     str(WRAPPER), "native"], timeout=timeout)


def ca(client, tool, action, params=None, timeout=None):
    """A community compound call: tool(action=..., params={...}). -> (is_error, data, images)."""
    args = {"action": action}
    if params is not None:
        args["params"] = params
    return client.call(tool, args, timeout=timeout)


def run_script(v, script, timeout=60):
    """Run `script` with the vendor's sandboxed run_script; return what it assigned to `result`.
    Raises on a script error."""
    is_error, data, _ = v.call("run_script", {"script": script, "timeout": min(int(timeout), 60)}, timeout=timeout + 15)
    if is_error or not isinstance(data, dict):
        raise RuntimeError(f"run_script failed: {str(data)[:400]}")
    return data.get("result")


FIXTURE_PRELUDE = f"""
pm = resolve.GetProjectManager()
proj = pm.GetCurrentProject()
if proj is None or proj.GetName() != {FIXTURE_PROJECT!r}:
    proj = pm.LoadProject({FIXTURE_PROJECT!r})
if proj is None or proj.GetName() != {FIXTURE_PROJECT!r}:
    raise RuntimeError("cannot load the fixture project {FIXTURE_PROJECT}")
mp = proj.GetMediaPool()
def timeline(name):
    for i in range(1, proj.GetTimelineCount() + 1):
        t = proj.GetTimelineByIndex(i)
        if t.GetName() == name:
            return t
    return None
"""


def scratch_timeline(v, suffix, source=FIXTURE_TIMELINE):
    """Duplicate `source` (default bench_tl) into SCRATCH_PREFIX+suffix, make it current, return its name.
    Any leftover of the same name is deleted first."""
    name = SCRATCH_PREFIX + suffix
    r = run_script(v, FIXTURE_PRELUDE + f"""
old = timeline({name!r})
if old is not None:
    mp.DeleteTimelines([old])
src = timeline({source!r})
if src is None:
    raise RuntimeError("fixture timeline {source} is missing")
proj.SetCurrentTimeline(src)
dup = src.DuplicateTimeline({name!r})
if dup is None:
    raise RuntimeError("DuplicateTimeline failed")
proj.SetCurrentTimeline(dup)
result = {{"name": dup.GetName(), "items": len(dup.GetItemListInTrack("video", 1) or [])}}
""")
    expect(r["name"] == name, f"duplicate got name {r['name']!r}")
    return name


def drop_scratch(v):
    """Delete every timeline in the fixture whose name starts with SCRATCH_PREFIX; back to bench_tl."""
    return run_script(v, FIXTURE_PRELUDE + f"""
gone = [proj.GetTimelineByIndex(i) for i in range(1, proj.GetTimelineCount() + 1)]
gone = [t for t in gone if t.GetName().startswith({SCRATCH_PREFIX!r})]
base = timeline({FIXTURE_TIMELINE!r})
if base is not None:
    proj.SetCurrentTimeline(base)
result = {{"deleted": len(gone), "ok": bool(mp.DeleteTimelines(gone)) if gone else True}}
""")


def synth_media(name, ffmpeg_args):
    """Make (once) a test file in SCRATCH, which is inside a Media Storage volume. -> Path."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    out = SCRATCH / name
    if not out.exists():
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *ffmpeg_args, str(out)], check=True)
    return out


def media_clip():
    """testsrc2 video + 1 kHz tone, 6 s, 1080p25, PCM (Resolve on Linux decodes no AAC)."""
    return synth_media("zz_reg_testsrc_av.mov", [
        "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=25:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=6",
        "-c:v", "prores_ks", "-profile:v", "1", "-c:a", "pcm_s16le", "-shortest"])


def tone_with_silence():
    """2 s tone, 2 s digital silence, 2 s tone (48 kHz PCM WAV): for RippleDeleteSilence."""
    return synth_media("zz_reg_tone_silence.wav", [
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-filter_complex", "[1]atrim=duration=2[s];[0][s][2]concat=n=3:v=0:a=1", "-ar", "48000", "-c:a", "pcm_s16le"])


# Community-server capabilities the live checks depend on: {name: (tool, text its description must contain)}.
# t00 records which ones are present in baselines/community_tools.json; a live check skips a capability that is
# absent only when the baseline never had it (otherwise it was removed: FAIL).
COMMUNITY_CAPS = {
    "timeline_pin": ("timeline", "timeline_name"),
    "timeline_pin_id": ("timeline", "timeline_id"),
    "timeline_item_id": ("timeline_item", "timeline_item_id"),
    "duplicate_make_current": ("timeline", "make_current"),
    "drx_track_grade_check": ("graph", "pTrackVer"),
    "interchange_roundtrip": ("timeline", "probe_interchange_roundtrip"),
}

# One graded node (node colour Blue) as Resolve writes it in a .drx, version body only.
_DRX_BODY = ("8128b52ffd60b8004d090044100a9003080510011a4808800f10b8081d0000803f20800f28b808350000803f38800f40b808"
             "48ffffffff0f7a240000803f003a98020805100120be0128b4013801402c4ae4010a930108011801328c0112890108011a0d"
             "08c28180b00812050d1a2f5dbe1a0d08c3c4c5af47e1be1a0d08c6c7c84c37493f1a0d08c9ca0a45080918013212121008"
             "021a0c089d80c06112050dcdcccc3e321408788c3f321508ee019a99d93f12050304050612520c0a0a08818080800c1202"
             "100260ccc4c9f4607a0d436c6970436f6c6f72426c75654a0e080510501a080807104018052005520e080610401a0808"
             "0810401806200560988993e9c1011a23544a0312010691fd83a8190e005ef4ba80566174cadb9cc881718164e032200b"
             "2019b80cc8c264ca30d30b720028")


def _drx_version(db_id, ver_type, has_correction):
    return (f'<ListMgt::LmVersion DbId="{db_id}"><FieldsBlob/><Name/>'
            f"<HasCorrection>{'true' if has_correction else 'false'}</HasCorrection><VerType>{ver_type}</VerType>"
            f"<ImplVersion>1</ImplVersion><Body>{_DRX_BODY}</Body></ListMgt::LmVersion>")


def write_drx(path, track):
    """Write a one-node .drx to `path`. Its clip grade (<pClipFullVer>) is always there; the timeline grade
    (<pTrackVer>) is "present" (the same node, HasCorrection true), "empty" (HasCorrection false) or "missing"."""
    track_block = {"present": f"<pTrackVer>{_drx_version('9a7e0c51-0002-4002-8002-9a7e0c519a7e', 1, True)}</pTrackVer>\n",
                   "empty": f"<pTrackVer>{_drx_version('9a7e0c51-0003-4003-8003-9a7e0c519a7e', 1, False)}</pTrackVer>\n",
                   "missing": ""}[track]
    Path(path).write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Gallery::GyStill DbId="9a7e0c51-0000-4000-8000-9a7e0c519a7e">\n'
        "<FieldsBlob/><SrcType>1</SrcType><Label>zz_reg one node</Label>\n"
        "<Width>1920</Width><Height>1080</Height><BitDepth>10</BitDepth><PAR>1</PAR><Endianship>1</Endianship>\n"
        f"<pClipFullVer>{_drx_version('9a7e0c51-0001-4001-8001-9a7e0c519a7e', 0, True)}</pClipFullVer>\n"
        + track_block + "<PrimaryCCMode>0</PrimaryCCMode>\n</Gallery::GyStill>\n")
    return Path(path)


def drop_scratch_clips(v):
    """Delete the fixture's Media Pool clips that the suite imported (name starts with SCRATCH_PREFIX and the file
    lives in SCRATCH). Call after drop_scratch: a clip still used by a timeline would take it along."""
    return run_script(v, FIXTURE_PRELUDE + f"""
scratch = {str(SCRATCH)!r} + "/"
hits = []
def walk(folder):
    for c in folder.GetClipList() or []:
        if c.GetName().startswith({SCRATCH_PREFIX!r}) and (c.GetClipProperty("File Path") or "").startswith(scratch):
            hits.append(c)
    for sub in folder.GetSubFolderList() or []:
        walk(sub)
walk(mp.GetRootFolder())
names = [c.GetName() for c in hits]
result = {{"deleted": names, "ok": bool(mp.DeleteClips(hits)) if hits else True}}
""")


# ---- processes and the shared session ----------------------------------------------------------

RESOLVE_BIN = "/opt/resolve/bin/resolve"
LIVE_ENV = "REGRESSION_LIVE_SESSION"   # set by live.py for the modules it runs: "<pid of live.py>"


def cmdline(pid):
    try:
        return [a.decode(errors="replace") for a in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if a]
    except OSError:
        return []


def display_of(pid):
    try:
        for kv in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
            if kv.startswith(b"DISPLAY="):
                return kv[8:].decode(errors="replace")
    except OSError:
        pass
    return None


def _exe(pid):
    try:
        return os.readlink(f"/proc/{pid}/exe").removesuffix(" (deleted)")
    except OSError:
        return None


def _pids():
    return sorted(int(n) for n in os.listdir("/proc") if n.isdigit() and int(n) != os.getpid())


def resolve_processes():
    """Every running DaVinci Resolve, found by executable (/proc/<pid>/exe) as well as by command line, so one started
    through a symlink, a relative path or another install directory is seen too.
    -> [{"pid", "nogui", "display", "cmd"}]"""
    out = []
    for pid in _pids():
        exe, cmd = _exe(pid), cmdline(pid)
        by_exe = exe is not None and (exe == RESOLVE_BIN or (os.path.basename(exe) == "resolve"
                                                             and "resolve" in os.path.dirname(exe).lower()))
        by_cmd = bool(cmd) and (cmd[0] == RESOLVE_BIN or (os.path.basename(cmd[0]) == "resolve"
                                                          and "resolve" in os.path.dirname(cmd[0]).lower()))
        if by_exe or by_cmd:
            out.append({"pid": pid, "nogui": "-nogui" in cmd, "display": display_of(pid), "cmd": " ".join(cmd)[:200]})
    return out


def xvfb_pids(display):
    """Xvfb servers for `display`, by executable or argv[0] basename (also when started by full path)."""
    out = []
    for pid in _pids():
        cmd = cmdline(pid)
        exe = _exe(pid)
        if cmd and display in cmd[1:2] and "Xvfb" in (os.path.basename(cmd[0]), os.path.basename(exe or "")):
            out.append(pid)
    return out


def session_problem():
    """None when every running Resolve is a -nogui one bound to this process's DISPLAY and that display is an Xvfb
    (the suite's shared session); otherwise why this is not the suite's session."""
    procs = resolve_processes()
    if not procs:
        return "no running Resolve: start the suite with run.sh (it starts a shared -nogui session)"
    display = os.environ.get("DISPLAY")
    foreign = [p for p in procs if not p["nogui"] or p["display"] != display]
    if foreign:
        return ("a Resolve that is not the suite's -nogui session on DISPLAY=" + str(display) + " is running: "
                + "; ".join(f"pid {p['pid']} ({'-nogui' if p['nogui'] else 'GUI'}, DISPLAY={p['display']})" for p in foreign))
    if not display or not xvfb_pids(display):
        return f"DISPLAY={display} is not an Xvfb display: this Resolve is not the suite's shared session"
    return None


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True


def main_guard():
    """Live modules refuse to run outside the suite's own shared session: a Resolve must be up, every Resolve must be
    the -nogui one on this process's Xvfb DISPLAY, and live.py (started by run.sh) must have marked the session.
    So a person's Resolve is never attached to, even when a module is started by hand."""
    problem = session_problem()
    if problem is None and not _alive(os.environ.get(LIVE_ENV, "")):
        problem = f"not started by live.py ({LIVE_ENV} unset or stale): run it with ./run.sh --only <id>"
    if problem:
        print("refusing to run: " + problem, file=sys.stderr)
        raise SystemExit(2)
