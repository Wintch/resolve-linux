#!/usr/bin/env python3
"""
Robust DaVinci Resolve automation client over the two MCP servers on this rig:
native (Blackmagic, bundled since 21.1) and community
(samuelgursky/davinci-resolve-mcp).

Routing rule: **Resolve's own tooling first, always.** Every call goes to the
native server (raw scripting API via `run_script`, or a native tool).
community is a fallback only, started lazily the first time one is needed, so
the client works with that server absent. As of 21.1 nothing here *requires*
it: even Gallery stills, long assumed community-only because native ships no
stills tool, turned out to work native-only (see `grab_still`).

This replaces an earlier latency-based split (single lookups -> community for
its ~5.5ms vs native's ~66ms per call, per bench_01). Those numbers are still
real and still in the README, but they're a measurement, not a routing
criterion: 60ms on a lookup doesn't buy back a second server in the path.
`get_context()` fetches every common lookup in one native round trip instead.

Every method raises `ResolveError` on failure and returns parsed data --
unlike `bench_lib.timed_call`, which deliberately never raises (an error is a
data point to a benchmark, but a bug to a caller).

Also folds in the failure modes found live while building the benchmarks:
  - missing XAUTHORITY crashes Resolve's Qt init under this rig's Wayland
    (mutter + rootless Xwayland) session -- handled in bench_lib.resolve_env().
  - Right after `launch_resolve`, Resolve idles on the Project Manager
    screen, not inside a loaded project: GetCurrentProject() still returns
    a usable "Untitled Project" object, but GetCurrentPage() is None and
    every Media Pool write silently no-ops. Fixed by explicitly
    creating/loading a *named* project, which is what actually dismisses
    the Project Manager screen.
  - A scripting call made while the GUI is mid-playback never returns. Every
    call here carries a timeout, so that surfaces as ResolveError, not a hang.
  - Source media only imports from inside a registered Media Storage volume,
    `MediaPool.ImportMedia` silently fails where
    `MediaStorage.AddItemListToMediaPool` works, and the latter returns
    nothing for a path already in the pool -- see `import_media`.

Usage:
    async with ResolveClient() as client:
        await client.ensure_running()
        ctx = await client.get_context()
        await client.rename_clips(["A", "B"])
"""
import asyncio
import base64
import json
import subprocess
import tempfile
import time
from contextlib import AsyncExitStack
from pathlib import Path

import audio_loudness_check
from bench_lib import community_session, native_session, timed_call

# native's run_script: default 10s, hard max 60s (from its own tool schema)
SCRIPT_TIMEOUT_S = 10
SCRIPT_TIMEOUT_MAX_S = 60
# client-side read timeout sits above the server-side one, so a script that
# merely runs long fails with native's own error, and only a genuinely wedged
# Resolve (mid-playback hang) trips this one
CLIENT_TIMEOUT_MARGIN_S = 10
LAUNCH_TIMEOUT_S = 90  # launch_resolve itself waits up to 60s
RENDER_TERMINAL_STATUSES = {"Complete", "Cancelled", "Failed"}


class ResolveError(RuntimeError):
    pass


class ResolveClient:
    def __init__(self, default_project: str = "MCP-Benchmark"):
        self.default_project = default_project
        self._stack = None
        self.native = None
        self._community = None

    async def __aenter__(self):
        self._stack = AsyncExitStack()
        try:
            self.native = await self._stack.enter_async_context(native_session())
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *exc_info):
        await self._stack.aclose()

    async def _community_session(self):
        if self._community is None:
            try:
                self._community = await self._stack.enter_async_context(community_session("compound"))
            except Exception as exc:
                raise ResolveError(f"community MCP server failed to start: {exc!r}") from exc
        return self._community

    # ---- plumbing: every call raises on error and returns parsed data ------

    async def _native(self, tool: str, arguments: dict, label: str = None, timeout: float = None):
        r = await timed_call(self.native, tool, arguments, label=label or tool, timeout=timeout)
        if r["is_error"]:
            raise ResolveError(f"{r['label']}: {r['result_text']}")
        return r["result_json"] if r["result_json"] is not None else r["result_text"]

    async def run_script(self, script: str, unsafe: bool = False, timeout: int = SCRIPT_TIMEOUT_S, label: str = None):
        """Run `script` on native; returns whatever it assigned to `result`
        (None if it assigned nothing). native's envelope is
        {"output": <captured print()>, "result": <result>}."""
        timeout = min(int(timeout), SCRIPT_TIMEOUT_MAX_S)
        tool = "run_script_unsafe" if unsafe else "run_script"
        envelope = await self._native(
            tool,
            {"script": script, "timeout": timeout},
            label=label or tool,
            timeout=timeout + CLIENT_TIMEOUT_MARGIN_S,
        )
        if not isinstance(envelope, dict):
            raise ResolveError(f"{label or tool}: unexpected response {str(envelope)[:300]}")
        return envelope.get("result")

    # ---- robustness: launch + escape the Project Manager screen ------------

    async def ensure_running(self, project: str = None) -> dict:
        status = await self._native("get_resolve_status", {}, timeout=CLIENT_TIMEOUT_MARGIN_S)
        if not (isinstance(status, dict) and status.get("running")):
            await self._native("launch_resolve", {}, timeout=LAUNCH_TIMEOUT_S)
            status = await self._native("get_resolve_status", {}, timeout=CLIENT_TIMEOUT_MARGIN_S)
            if not (isinstance(status, dict) and status.get("running")):
                raise ResolveError(f"launch_resolve returned but Resolve isn't reachable: {status!r}")
        return await self.ensure_project(project or self.default_project)

    async def ensure_project(self, name: str) -> dict:
        script = f"""
name = {name!r}
pm = resolve.GetProjectManager()
current = pm.GetCurrentProject()
if current and current.GetName() == name and resolve.GetCurrentPage():
    proj = current
elif name in (pm.GetProjectListInCurrentFolder() or []):
    proj = pm.LoadProject(name)
else:
    proj = pm.CreateProject(name)
    if proj is None:
        raise RuntimeError(f"CreateProject({{name!r}}) refused -- Resolve rejects some characters, e.g. a double quote")
result = {{"project": proj.GetName() if proj else None, "page": resolve.GetCurrentPage()}}
"""
        r = await self.run_script(script, label="ensure_project", timeout=30)
        if not isinstance(r, dict) or r.get("project") != name or not r.get("page"):
            raise ResolveError(f"ensure_project({name!r}) failed to reach an editable page: {r!r}")
        return r

    # ---- lookups: one native round trip for all of them --------------------

    async def get_context(self) -> dict:
        script = """
proj = resolve.GetProjectManager().GetCurrentProject()
tl = proj.GetCurrentTimeline() if proj else None
result = {
    "product": resolve.GetProductName(),
    "version": resolve.GetVersionString(),
    "page": resolve.GetCurrentPage(),
    "project": proj.GetName() if proj else None,
    "timeline": tl.GetName() if tl else None,
    "timeline_count": proj.GetTimelineCount() if proj else 0,
    "media_storage_volumes": resolve.GetMediaStorage().GetMountedVolumeList() or [],
}
"""
        return await self.run_script(script, label="get_context")

    async def get_version(self) -> str:
        return (await self.get_context())["version"]

    async def get_project_name(self) -> str:
        return (await self.get_context())["project"]

    async def get_current_timeline_name(self) -> str:
        return (await self.get_context())["timeline"]

    # ---- compound/batch edits: one native run_script loop ------------------

    async def rename_clips(self, names: list, track_type: str = "video", track_index: int = 1) -> list:
        """Rename the clips on one track, in order. Returns the names as read
        back from Resolve. Refuses a count mismatch instead of renaming a
        prefix and calling it done."""
        script = f"""
names = {list(names)!r}
tl = resolve.GetProjectManager().GetCurrentProject().GetCurrentTimeline()
if tl is None:
    raise RuntimeError("no current timeline")
items = tl.GetItemListInTrack({track_type!r}, {int(track_index)}) or []
if len(items) != len(names):
    raise RuntimeError(f"{{len(names)}} names for {{len(items)}} clips on {track_type} track {int(track_index)}")
for item, name in zip(items, names):
    item.SetName(name)
result = [item.GetName() for item in items]
"""
        applied = await self.run_script(script, label="rename_clips")
        if applied != list(names):
            raise ResolveError(f"rename_clips: asked for {list(names)!r}, Resolve reports {applied!r}")
        return applied

    async def import_media(self, paths: list) -> list:
        """Import source files into the current project's Media Pool; returns
        their clip names. Encodes the three import gotchas bench_03 hit, each
        of which otherwise looks like the same silent empty list:
          1. the path must sit inside a registered Media Storage volume;
          2. `MediaPool.ImportMedia` fails on files
             `MediaStorage.AddItemListToMediaPool` imports fine -- use the latter;
          3. a path already in the pool returns nothing the second time, so
             already-imported paths are looked up instead of re-added."""
        script = f"""
paths = {[str(p) for p in paths]!r}
ms = resolve.GetMediaStorage()
volumes = ms.GetMountedVolumeList() or []
outside = [p for p in paths if not any(p == v or p.startswith(v.rstrip("/") + "/") for v in volumes)]
if outside:
    raise RuntimeError(f"not inside a registered Media Storage volume {{volumes}}: {{outside}}")

mp = resolve.GetProjectManager().GetCurrentProject().GetMediaPool()
by_path = {{}}
def walk(folder):
    for clip in folder.GetClipList() or []:
        by_path[clip.GetClipProperty("File Path")] = clip
    for sub in folder.GetSubFolderList() or []:
        walk(sub)
walk(mp.GetRootFolder())

new_paths = [p for p in paths if p not in by_path]
if new_paths:
    for clip in ms.AddItemListToMediaPool(new_paths) or []:
        by_path[clip.GetClipProperty("File Path")] = clip
missing = [p for p in paths if p not in by_path]
if missing:
    raise RuntimeError(f"AddItemListToMediaPool imported nothing for: {{missing}}")
result = [by_path[p].GetName() for p in paths]
"""
        return await self.run_script(script, label="import_media", timeout=30)

    # ---- export: render with a deadline, then gate the real deliverable ------

    async def render(
        self,
        preset: str,
        outdir,
        name: str,
        timeout_s: float = 3600,
        poll_s: float = 1.0,
        loudness_gate: bool = True,
    ) -> dict:
        """Render the current timeline with `preset` to `outdir/name.*`, wait
        for it, verify the file, and run the YouTube loudness gate on it.

        This is the render-lifecycle wrapper the README's fork question
        concluded was the actually-missing piece: Resolve's render API fails
        *quietly* (outdir outside a Media Storage volume, silent None from
        AddRenderJob, a livelocked job that never reaches a terminal status),
        so every one of those is turned into a ResolveError here. Only ever
        touches its own job -- never clears the user's render queue.

        A job still running at `timeout_s` gets StopRendering and an error;
        per the README, a wedged render may need Resolve killed and relaunched
        before the API answers again -- that call is left to the operator."""
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)  # the sandboxed script can't
        started = await self.run_script(
            f"""
outdir, name, preset = {str(outdir)!r}, {name!r}, {preset!r}
volumes = resolve.GetMediaStorage().GetMountedVolumeList() or []
if not any(outdir == v or outdir.startswith(v.rstrip("/") + "/") for v in volumes):
    raise RuntimeError(f"render target {{outdir}} is outside every registered Media Storage volume {{volumes}}")
proj = resolve.GetProjectManager().GetCurrentProject()
tl = proj.GetCurrentTimeline()
if tl is None:
    raise RuntimeError("no current timeline to render")
if proj.IsRenderingInProgress():
    raise RuntimeError("a render is already in progress")
if not proj.LoadRenderPreset(preset):
    raise RuntimeError(f"LoadRenderPreset({{preset!r}}) failed; available: {{proj.GetRenderPresetList()}}")
if not proj.SetRenderSettings({{"SelectAllFrames": True, "TargetDir": outdir, "CustomName": name}}):
    raise RuntimeError("SetRenderSettings refused TargetDir/CustomName")
job_id = proj.AddRenderJob()
if not job_id:
    raise RuntimeError("AddRenderJob returned no job id (offline media or an empty timeline are the usual causes)")
if not proj.StartRendering([job_id], False):
    proj.DeleteRenderJob(job_id)
    raise RuntimeError("StartRendering returned False")
result = {{"job_id": job_id, "timeline": tl.GetName()}}
""",
            label="render_start",
            timeout=30,
        )
        job_id = started["job_id"]

        t0 = time.monotonic()
        status = {}
        try:
            while True:
                status = await self.run_script(
                    f"result = resolve.GetProjectManager().GetCurrentProject().GetRenderJobStatus({job_id!r})",
                    label="render_poll",
                )
                if (status or {}).get("JobStatus") in RENDER_TERMINAL_STATUSES:
                    break
                if time.monotonic() - t0 > timeout_s:
                    await self.run_script(
                        "result = resolve.GetProjectManager().GetCurrentProject().StopRendering()", label="render_stop"
                    )
                    raise ResolveError(f"render {job_id} not finished after {timeout_s:.0f}s, stopped: {status}")
                await asyncio.sleep(poll_s)
        finally:
            await self.run_script(
                f"result = resolve.GetProjectManager().GetCurrentProject().DeleteRenderJob({job_id!r})",
                label="render_cleanup",
            )

        record = {**started, "preset": preset, "wall_s": round(time.monotonic() - t0, 2), "status": status}
        if status.get("JobStatus") != "Complete":
            raise ResolveError(f"render ended as {status.get('JobStatus')}: {status}")

        files = sorted(outdir.glob(f"{name}*"))
        if not files:
            raise ResolveError(f"render reported Complete but wrote no {name}* under {outdir}")
        record["file"] = str(files[0])
        record["ffprobe"] = _ffprobe(files[0])
        if not record["ffprobe"].get("streams"):
            raise ResolveError(f"render output isn't a readable media file: {record}")

        has_audio = any(st.get("codec_type") == "audio" for st in record["ffprobe"]["streams"])
        if loudness_gate and has_audio:
            record["loudness"] = audio_loudness_check.check_file(str(files[0]))
            if not record["loudness"]["pass"]:
                raise ResolveError(f"loudness gate FAILED for {files[0]} (file kept): {record['loudness']}")
        else:
            record["loudness"] = None  # gate off, or nothing to measure
        return record

    # ---- native-only capability --------------------------------------------

    async def generate_lut(self, path: str, size: int, transform: str):
        return await self._native(
            "generate_lut", {"path": path, "size": size, "transform": transform}, timeout=SCRIPT_TIMEOUT_MAX_S
        )

    async def list_luts(self):
        return await self._native("list_luts", {}, timeout=CLIENT_TIMEOUT_MARGIN_S)

    async def delete_lut(self, path: str):
        return await self._native("delete_lut", {"path": path}, timeout=CLIENT_TIMEOUT_MARGIN_S)

    # ---- Gallery stills: native first, community as fallback ----------------
    # native has no dedicated stills *tool*, which earlier got read as "native
    # can't do stills". It can: Timeline.GrabStill + GalleryStillAlbum.ExportStills
    # are in the scripting API, and run_script_unsafe can read the exported file
    # back. community's gallery_stills.grab_and_export stays as the fallback.

    async def grab_still(self, label: str = "still") -> bytes:
        """PNG bytes of the frame under the playhead (Color page). The proof
        primitive for any grade change: SetNodeEnabled & co. return a bool
        that says nothing about what actually rendered."""
        try:
            return await self._grab_still_native(label)
        except ResolveError as native_exc:
            try:
                return await self._grab_still_community(label)
            except ResolveError as community_exc:
                raise ResolveError(f"grab_still failed on both backends: {native_exc} / {community_exc}") from None

    async def _grab_still_native(self, label: str) -> bytes:
        script = f"""
import base64, glob, os, shutil, tempfile
proj = resolve.GetProjectManager().GetCurrentProject()
tl = proj.GetCurrentTimeline()
album = proj.GetGallery().GetCurrentStillAlbum()
still = tl.GrabStill()
if not still:
    raise RuntimeError("Timeline.GrabStill returned nothing (not on the Color page?)")
tmp = tempfile.mkdtemp(prefix="resolve_still_")
try:
    exported = album.ExportStills([still], tmp, {label!r}, "png")
    pngs = glob.glob(os.path.join(tmp, "*.png"))
    if not exported or not pngs:
        raise RuntimeError(f"ExportStills returned {{exported}}, files: {{os.listdir(tmp)}}")
    with open(pngs[0], "rb") as f:
        result = {{"png_base64": base64.b64encode(f.read()).decode()}}
finally:
    album.DeleteStills([still])
    shutil.rmtree(tmp, ignore_errors=True)
"""
        r = await self.run_script(script, unsafe=True, timeout=30, label=f"grab_still_{label}")
        if not isinstance(r, dict) or "png_base64" not in r:
            raise ResolveError(f"grab_still({label}): unexpected result {str(r)[:300]}")
        return base64.b64decode(r["png_base64"])

    async def _grab_still_community(self, label: str) -> bytes:
        """grab_and_export always emits a companion .drx (plain-text XML under
        "data") next to the requested format -- the PNG is a separate `files`
        entry with its bytes under "data_base64"."""
        community = await self._community_session()
        with tempfile.TemporaryDirectory() as tmp:
            res = await community.call_tool(
                "gallery_stills",
                {"action": "grab_and_export", "params": {"folder_path": tmp, "prefix": label, "format": "png"}},
            )
        text = "".join(getattr(c, "text", "") for c in res.content)
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            raise ResolveError(f"community grab_and_export({label}): {text[:300]}") from None
        png_entries = [f for f in obj.get("files", []) if f["name"].endswith(".png")]
        if not png_entries:
            raise ResolveError(f"community grab_and_export({label}) returned no .png: {text[:300]}")
        return base64.b64decode(png_entries[0]["data_base64"])


def _ffprobe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,size:stream=codec_type,codec_name,width,height",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return {"ffprobe_error": out.stderr.strip()}
