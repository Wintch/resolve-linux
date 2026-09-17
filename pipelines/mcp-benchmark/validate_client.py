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


async def main():
    checks = []

    async def check(label, coro_fn):
        try:
            checks.append((label, True, str(await coro_fn())[:120]))
        except Exception as exc:
            checks.append((label, False, repr(exc)[:200]))

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

        async def grab_still():
            r = await client.run_script(
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
resolve.OpenPage("color")
result = {{"timeline": tl.GetName(), "page": resolve.GetCurrentPage()}}
""",
                timeout=30,
            )
            expect(r == {"timeline": MEDIA_TIMELINE, "page": "color"}, r)
            png = await client.grab_still("validate_client")
            return expect(png.startswith(PNG_MAGIC), f"{len(png)} byte PNG")

        async def render_ok():
            # bench_tl_media is still current from the grab_still check
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
        await check("render + loudness gate", render_ok)
        await check("NEG render outside volume raises", lambda: must_raise(
            "render", lambda: client.render(RENDER_PRESET, "/tmp/mcp-bench-render", "nope")))
        await check("NEG render unknown preset raises", lambda: must_raise(
            "render", lambda: client.render("No Such Preset", SCRATCH / "render", "nope")))
        await check("reselect bench_tl", select_fixture_timeline)
        await check(
            "NEG rename count mismatch raises",
            lambda: must_raise("rename_clips", lambda: client.rename_clips(["only one"])),
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
    all_ok = True
    for label, ok, preview in checks:
        all_ok &= ok
        print(f"{label:<36} {'OK' if ok else 'FAIL':<5} {preview}")

    print("\nALL CHECKS PASSED" if all_ok else "\nSOME CHECKS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
