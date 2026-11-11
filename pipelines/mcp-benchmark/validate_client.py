#!/usr/bin/env python3
"""
End-to-end smoke test for resolve_client.ResolveClient, against the
disposable MCP-Benchmark fixture only (never a real project).

Every check asserts on the *value* that came back, not just "no exception":
an earlier cut of this file counted any non-raising call as OK while the
calls underneath never raised on tool errors at all -- so 5 of its 8 green
checks would have stayed green with Resolve returning errors. The negative
checks at the end exist to prove failures are actually loud now.

Run: ~/resolve-install/davinci-resolve-mcp/venv/bin/python validate_client.py
Requires: setup_bench_project.py already run once (8 clips on bench_tl).
"""
import asyncio
import subprocess
from pathlib import Path

from resolve_client import ResolveClient, ResolveError

ORIGINAL_NAMES = ["Solid Color"] * 8
TEST_NAMES = [f"Clip {i:02d}" for i in range(1, 9)]
TEST_LUT = "validate_client_test.cube"
# Resolve itself refuses a double quote in a project name (CreateProject
# returns None, probed live) but takes an apostrophe and a backslash-free
# name fine -- so this is the worst case that can legitimately exist.
QUOTED_PROJECT = "MCP-Benchmark it's quoted"
MEDIA_TIMELINE = "bench_tl_media"
SCRATCH = Path.home() / "Videos" / "mcp-bench-scratch"
MEDIA_CLIP = SCRATCH / "validate_client_testsrc_av.mov"
RENDER_PRESET = "ProRes 422 HQ"
# Resolve 21.2+: an OFX effect added and removed through the API (TimelineItem.AddEffect /
# DeleteEffects), proven with lossless Timeline.ExportFrame frames
TEST_EFFECT_ID = "Resolve FX/Resolve FX Film Emulation/Film Grain"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def synthesize_clip() -> Path:
    """A real (non-generator) clip: generator clips have no node graph, so
    GrabStill returns nothing on bench_tl. Lives under a registered Media
    Storage volume, outside the repo, and is reused across runs. DNxHR + PCM
    so it decodes on any Linux Resolve (no NVIDIA H.264 path, and AAC is
    unsupported on Linux outright); the tone is normalized to -14 LUFS so an
    unity-gain render of it should pass the loudness gate."""
    if not MEDIA_CLIP.exists():
        MEDIA_CLIP.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=6",
                "-f", "lavfi", "-i", "sine=frequency=1000:duration=6",
                "-af", "loudnorm=I=-14:TP=-1.5:LRA=7", "-ar", "48000", "-ac", "2",
                "-c:v", "dnxhd", "-profile:v", "dnxhr_lb", "-pix_fmt", "yuv422p",
                "-c:a", "pcm_s16le", str(MEDIA_CLIP),
            ],
            check=True, capture_output=True,
        )
    return MEDIA_CLIP


def expect(condition, detail):
    if not condition:
        raise AssertionError(detail)
    return detail


class Skip(Exception):
    """A check that cannot run in this setup (reported as SKIP, not as a failure)."""


def resolve_is_nogui() -> bool:
    """True when the running Resolve was started with -nogui: it has no pages, so
    OpenPage("color") leaves the page unchanged and Timeline.GrabStill cannot work."""
    for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            argv = cmdline.read_bytes().split(b"\0")
        except OSError:
            continue
        if argv and argv[0].endswith(b"/resolve") and b"-nogui" in argv:
            return True
    return False


async def main():
    checks = []

    async def check(label, coro_fn):
        try:
            checks.append((label, "OK", str(await coro_fn())[:120]))
        except Skip as exc:
            checks.append((label, "SKIP", str(exc)[:200]))
        except Exception as exc:
            checks.append((label, "FAIL", repr(exc)[:200]))

    async with ResolveClient() as client:

        async def ensure_running():
            r = await client.ensure_running()
            return expect(r["project"] == "MCP-Benchmark" and r["page"], r)

        async def context():
            ctx = await client.get_context()
            expect(ctx["version"] and ctx["project"] == "MCP-Benchmark", ctx)
            expect(ctx["media_storage_volumes"], f"no Media Storage volumes registered: {ctx}")
            return ctx

        async def select_fixture_timeline():
            r = await client.run_script(
                """
proj = resolve.GetProjectManager().GetCurrentProject()
tl = next((proj.GetTimelineByIndex(i) for i in range(1, proj.GetTimelineCount() + 1)
           if proj.GetTimelineByIndex(i).GetName() == "bench_tl"), None)
result = {"selected": bool(tl and proj.SetCurrentTimeline(tl))}
"""
            )
            return expect(r["selected"], r)

        async def rename(names):
            return expect(await client.rename_clips(names) == names, names)

        async def lut_roundtrip():
            await client.generate_lut(path=TEST_LUT, size=9, transform="return (r, g, b)")
            listed = str(await client.list_luts())
            expect(TEST_LUT in listed, f"{TEST_LUT} missing from list_luts: {listed[:200]}")
            await client.delete_lut(TEST_LUT)
            expect(TEST_LUT not in str(await client.list_luts()), f"{TEST_LUT} still listed after delete_lut")
            return "generated, listed, deleted"

        async def on_fixture_timeline(coro_fn):
            current = await client.run_script(
                "result = resolve.GetProjectManager().GetCurrentProject().GetCurrentTimeline().GetName()"
            )
            expect(current == "bench_tl", f"current timeline is {current!r}, not bench_tl -- check not run")
            return await coro_fn()

        async def must_raise(label, coro_fn):
            try:
                result = await coro_fn()
            except ResolveError as exc:
                return f"raised as expected: {str(exc)[:80]}"
            raise AssertionError(f"{label} did not raise, returned {result!r}")

        async def quoted_project_name():
            # quotes used to be pasted raw into the script source
            existing = await client.run_script(
                "result = resolve.GetProjectManager().GetProjectListInCurrentFolder() or []"
            )
            expect(QUOTED_PROJECT not in existing, "test project name already exists -- not touching it")
            try:
                r = await client.ensure_project(QUOTED_PROJECT)
                return expect(r["project"] == QUOTED_PROJECT, r)
            finally:
                # only ever deletes the project this check itself just created
                await client.ensure_project("MCP-Benchmark")
                await client.run_script(
                    f"result = resolve.GetProjectManager().DeleteProject({QUOTED_PROJECT!r})"
                )

        async def import_real_clip():
            clip = synthesize_clip()
            first = await client.import_media([clip])
            again = await client.import_media([clip])  # dedup-by-path used to return nothing
            return expect(first == again == [clip.name], f"first={first} again={again}")

        async def select_media_timeline(page=None):
            """Make bench_tl_media (one real clip, built from the imported fixture clip on first
            use) the current timeline; open `page` if given. Returns the script's result."""
            return await client.run_script(
                f"""
proj = resolve.GetProjectManager().GetCurrentProject()
mp = proj.GetMediaPool()
tl = next((proj.GetTimelineByIndex(i) for i in range(1, proj.GetTimelineCount() + 1)
           if proj.GetTimelineByIndex(i).GetName() == {MEDIA_TIMELINE!r}), None)
if tl is None:
    # imports land in whatever Media Pool folder is current, not the root
    def find(folder):
        for c in folder.GetClipList() or []:
            if c.GetClipProperty("File Path") == {str(MEDIA_CLIP)!r}:
                return c
        for sub in folder.GetSubFolderList() or []:
            hit = find(sub)
            if hit:
                return hit
    clip = find(mp.GetRootFolder())
    if clip is None:
        raise RuntimeError("fixture clip isn't in the Media Pool")
    tl = mp.CreateTimelineFromClips({MEDIA_TIMELINE!r}, [clip])
    if tl is None:
        raise RuntimeError("CreateTimelineFromClips failed")
proj.SetCurrentTimeline(tl)
if {page!r}:
    resolve.OpenPage({page!r})
result = {{"timeline": tl.GetName(), "page": resolve.GetCurrentPage(), "start": tl.GetStartTimecode()}}
""",
                timeout=30,
            )

        async def grab_still():
            if resolve_is_nogui():
                await select_media_timeline()  # the render check below still needs it current
                raise Skip("needs the Color page; a -nogui Resolve has no pages (frame_image covers -nogui)")
            r = await select_media_timeline("color")
            expect((r["timeline"], r["page"]) == (MEDIA_TIMELINE, "color"), r)

            # A PNG header proves nothing: a still exported too soon after
            # GrabStill is a perfectly valid PNG of the *wrong* content (see
            # resolve_client.STILL_SETTLE_S). testsrc2 changes every frame, so
            # make the grab prove it follows the playhead: A != B, and A == A.
            async def grab_at(offset_frames):
                await client.run_script(
                    f"""
tl = resolve.GetProjectManager().GetCurrentProject().GetCurrentTimeline()
start = tl.GetStartTimecode()
result = tl.SetCurrentTimecode(start[:-2] + {format(offset_frames, '02d')!r})
"""
                )
                return await client.grab_still(f"validate_client_{offset_frames}")

            first, moved, back = await grab_at(0), await grab_at(20), await grab_at(0)
            expect(first.startswith(PNG_MAGIC), "not a PNG")
            expect(first != moved, "still did not change when the playhead moved 20 frames -- stale grab")
            expect(first == back, "same frame grabbed twice differs -- stale grab")
            expect(not client.fallbacks, f"native grab failed, community served it: {client.fallbacks}")
            return f"{len(first)} byte PNG, follows the playhead"

        async def frame_image():
            r = await select_media_timeline()
            expect(r["timeline"] == MEDIA_TIMELINE, r)
            if not await client._native_has("get_image"):
                raise Skip("the vendor get_image tool needs Resolve 21.2 or later")
            # same proof as grab_still, by timecode instead of playhead: A != B, and A == A
            at = lambda frames: r["start"][:-2] + format(frames, "02d")
            (mime, first), (_, moved), (_, back) = [await client.frame_image(at(f)) for f in (0, 20, 0)]
            expect(mime in ("image/jpeg", "image/png") and first, f"no image ({mime}, {len(first)} bytes)")
            expect(first != moved, "frame did not change 20 frames later -- stale image")
            expect(first == back, "same frame rendered twice differs")
            expect(not client.fallbacks, f"native failed, community served it: {client.fallbacks}")
            return f"{len(first)} byte {mime}, follows the timecode"

        async def effect_roundtrip():
            r = await select_media_timeline()
            expect(r["timeline"] == MEDIA_TIMELINE, r)
            probe = await client.run_script(
                f"""
tl = resolve.GetProjectManager().GetCurrentProject().GetCurrentTimeline()
item = tl.GetItemListInTrack("video", 1)[0]
listed = resolve.GetEffectsList("video") if hasattr(resolve, "GetEffectsList") else None
result = {{"api": listed is not None,
          "known": bool(listed) and any(e.get("id") == {TEST_EFFECT_ID!r} for e in listed),
          "ofx": len(item.GetEffects("ofx") or []) if listed is not None else None}}
"""
            )
            if not probe["api"]:
                raise Skip("TimelineItem.AddEffect / Resolve.GetEffectsList need Resolve 21.2 or later")
            expect(probe["known"], f"{TEST_EFFECT_ID} not in Resolve.GetEffectsList('video')")
            expect(probe["ofx"] == 0, f"fixture clip already carries {probe['ofx']} OFX effect(s) -- not touching it")
            base, again = await client.export_frame(10), await client.export_frame(10)
            expect(base.startswith(PNG_MAGIC) and base == again, "ExportFrame is not a stable PNG")
            item = 'resolve.GetProjectManager().GetCurrentProject().GetCurrentTimeline().GetItemListInTrack("video", 1)[0]'
            try:
                added = await client.run_script(f"result = bool({item}.AddEffect('ofx', {TEST_EFFECT_ID!r}))")
                expect(added, "AddEffect returned None")
                graded = await client.export_frame(10)
                expect(graded != base, "frame unchanged with the effect applied")
            finally:
                # removes only the effect this check added (the clip had none before)
                await client.run_script(
                    f"it = {item}\nfx = [e for e in it.GetEffects('ofx') or [] if (e.GetInfo() or {{}}).get('id') == {TEST_EFFECT_ID!r}]\n"
                    "result = it.DeleteEffects(fx) if fx else True"
                )
            restored = await client.export_frame(10)
            expect(restored == base, "frame differs after DeleteEffects")
            return f"{len(base)} byte PNG: changed with {TEST_EFFECT_ID.rsplit('/', 1)[-1]}, identical after removal"

        async def render_ok():
            # bench_tl_media is still current from the grab_still and frame_image checks
            out = SCRATCH / "render"
            try:
                r = await client.render(RENDER_PRESET, out, "validate_client_render", timeout_s=300)
                expect(r["loudness"] and r["loudness"]["pass"], r)
                return f"{r['wall_s']}s, {r['loudness']['integrated_lufs']} LUFS, TP {r['loudness']['true_peak_dbtp']}"
            finally:
                for f in out.glob("validate_client_render*"):
                    f.unlink()

        await check("ensure_running", ensure_running)
        await check("get_context", context)
        await check("select bench_tl", select_fixture_timeline)
        await check("rename_clips -> test names", lambda: rename(TEST_NAMES))
        await check("rename_clips -> restore", lambda: rename(ORIGINAL_NAMES))
        await check("generate/list/delete LUT", lut_roundtrip)
        await check("project name with quotes", quoted_project_name)
        await check("import_media (real clip, twice)", import_real_clip)
        await check("grab_still (native first)", grab_still)
        await check("frame_image (native get_image)", frame_image)
        await check("AddEffect + ExportFrame round trip", effect_roundtrip)
        await check("render + loudness gate", render_ok)
        await check("NEG render outside volume raises", lambda: must_raise(
            "render", lambda: client.render(RENDER_PRESET, "/tmp/mcp-bench-render", "nope")))
        await check("NEG render unknown preset raises", lambda: must_raise(
            "render", lambda: client.render("No Such Preset", SCRATCH / "render", "nope")))
        await check("reselect bench_tl", select_fixture_timeline)
        await check(
            "NEG rename count mismatch raises",
            # only meaningful on bench_tl (8 clips): on a one-clip timeline this rename would succeed
            lambda: on_fixture_timeline(lambda: must_raise("rename_clips", lambda: client.rename_clips(["only one"]))),
        )
        await check(
            "NEG missing track raises",
            lambda: must_raise("rename_clips", lambda: client.rename_clips(TEST_NAMES, track_index=99)),
        )
        await check(
            "NEG script error raises",
            lambda: must_raise("run_script", lambda: client.run_script("raise RuntimeError('boom')")),
        )
        await check(
            "NEG import outside volume raises",
            lambda: must_raise("import_media", lambda: client.import_media(["/tmp/nope.wav"])),
        )

    print(f"\n{'check':<36} {'ok':<5} detail")
    for label, status, preview in checks:
        print(f"{label:<36} {status:<5} {preview}")

    failed = sum(status == "FAIL" for _, status, _ in checks)
    skipped = sum(status == "SKIP" for _, status, _ in checks)
    note = f" ({skipped} skipped)" if skipped else ""
    print(f"\nALL CHECKS PASSED{note}" if not failed else f"\nSOME CHECKS FAILED ({failed}){note}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
