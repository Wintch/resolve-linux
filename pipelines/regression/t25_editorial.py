#!/usr/bin/env python3
"""Live checks of editorial and grading features beyond the basics: transitions, timeline grades, subtitle presets.

1. `timeline_item add_transition` puts a real transition on the track, and what that does to addressing:
   on Resolve 21.1+ GetItemListInTrack lists transitions too, so `item_index` past the transition moves by
   one (that is expected and checked as such), while `timeline_item_id` keeps finding the same clip.
2. The community's Node server (`davinci-resolve-advanced-mcp`, offline .drx codec): `drx generate` with
   `target: "timeline"` writes a timeline grade (<pTrackVer>) and the default (clip) target does not; the
   timeline file applied with `graph apply_grade_from_drx source="timeline"` (after the server's
   confirm_token step) gives the timeline graph a node and visibly changes the exported frame
   (saturation 0 -> grey).
3. `timeline_ai set_subtitle_preset` with the server's bundled presets (Word Highlight, Lollipop) on a
   timeline with an .srt: each revision renders with burned-in subtitles to the end of the range, and
   the text is really in the frame. These presets need fonts that Debian lacks (Segoe UI Black for Word
   Highlight, Open Sans Bold for Lollipop); without them the render fails or the text is silently blank
   (see t00's font check). ExportFrame does not draw the subtitle track, so this goes through a render.

Touches only the MCP-Benchmark fixture through SCRATCH_PREFIX timelines, the synthetic clip and an .srt
(imported into its Media Pool and deleted again) and zz_reg_ files in rlib.SCRATCH; cleanup runs in a
finally block.
"""
import base64
import io
import json
import subprocess
import time

import rlib
from rlib import FIXTURE_PRELUDE, SCRATCH, SCRATCH_PREFIX, Skip, expect, run_script

suite = rlib.Suite("t25_editorial")
NODE_BIN = rlib.COMMUNITY_REPO / "bin" / "davinci-resolve-advanced-mcp.mjs"
DRX = SCRATCH / (SCRATCH_PREFIX + "t25_tlgrade.drx")
SRT = SCRATCH / (SCRATCH_PREFIX + "t25_subs.srt")
RENDER_DIR = SCRATCH / (SCRATCH_PREFIX + "t25_render")
TRANSITION = {"type": "Cross Dissolve", "category": "simple", "position": "end", "alignment": "center", "duration": 12}
PRESETS = ("Word Highlight", "Lollipop")
RENDER_FRAMES = 50


def raw(client, tool, action, params, timeout=180):
    """-> (isError flag, parsed body)."""
    r = client.rpc("tools/call", {"name": tool, "arguments": {"action": action, "params": params}}, timeout=timeout)
    if "error" in r:
        return True, r["error"]
    out = r.get("result", {})
    text = "".join(x.get("text", "") for x in out.get("content", []) if x.get("type") == "text")
    try:
        return bool(out.get("isError")), json.loads(text)
    except ValueError:
        return bool(out.get("isError")), text


def ok(client, tool, action, params, timeout=180):
    flag, data = raw(client, tool, action, params, timeout)
    expect(not flag and not (isinstance(data, dict) and data.get("error")), f"{tool}.{action}: {str(data)[:300]}")
    return data


def unknown_action(data):
    return isinstance(data, (dict, str)) and "unknown action" in json.dumps(data).lower()


def track_items(v, tl):
    return run_script(v, FIXTURE_PRELUDE + f"""
t = timeline({tl!r})
result = [{{"name": i.GetName(), "start": i.GetStart(), "end": i.GetEnd(), "id": i.GetUniqueId()}}
          for i in (t.GetItemListInTrack("video", 1) or [])]
""")


def clip_timeline(v, name, path):
    """A scratch timeline made of the clip at `path` (imported into the Media Pool if it is not there yet)."""
    return run_script(v, FIXTURE_PRELUDE + f"""
old = timeline({name!r})
if old is not None:
    mp.DeleteTimelines([old])
clip = None
for x in mp.GetRootFolder().GetClipList() or []:
    if x.GetClipProperty("File Path") == {path!r}:
        clip = x
if clip is None:
    clip = (resolve.GetMediaStorage().AddItemListToMediaPool([{path!r}]) or [None])[0]
if clip is None:
    raise RuntimeError("cannot import {path}")
t = mp.CreateTimelineFromClips({name!r}, [clip])
if t is None:
    raise RuntimeError("CreateTimelineFromClips failed")
proj.SetCurrentTimeline(t)
result = t.GetName()
""")


def frame(v, tl, rel=50):
    r = run_script(v, FIXTURE_PRELUDE + f"""
x = timeline({tl!r}).ExportFrame({rel}, {{"encodedImageFormat": "png", "maxDimension": 480}}) or {{}}
result = x.get("data") or ""
""")
    expect(r, f"ExportFrame on {tl} returned no image")
    return base64.b64decode(r)


def pixels(im):
    return im.get_flattened_data() if hasattr(im, "get_flattened_data") else im.getdata()


def mean_saturation(png):
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("HSV")
    return sum(p[1] for p in pixels(im)) / (im.width * im.height)


def timeline_nodes(v, tl):
    return run_script(v, FIXTURE_PRELUDE + f"g = timeline({tl!r}).GetNodeGraph()\nresult = g.GetNumNodes() if g else None")


# ── checks ───────────────────────────────────────────────────────────────────

def check_transition_index(c, v):
    tl = rlib.scratch_timeline(v, "t25_trans")
    before = track_items(v, tl)
    expect(len(before) >= 4, f"{tl} has {len(before)} items, need 4")
    pin = {"timeline_name": tl, "track_type": "video", "track_index": 1}
    flag, data = raw(c, "timeline_item", "add_transition", {**pin, "item_index": 1, "options": TRANSITION})
    if unknown_action(data):
        raise Skip("timeline_item has no add_transition on this server")
    if isinstance(data, dict) and (data.get("error") or {}).get("code") == "API_UNAVAILABLE":
        raise Skip(f"AddTransition unavailable: {str(data)[:160]}")
    expect(not flag and isinstance(data, dict) and data.get("success"), f"add_transition: {str(data)[:300]}")
    tr = data["transition"]
    cut = before[1]["end"]
    expect(tr["duration"] == TRANSITION["duration"], f"transition duration {tr['duration']}, asked {TRANSITION['duration']}")
    expect(tr["start"] < cut < tr["end"], f"transition {tr['start']}-{tr['end']} not centred on the cut at {cut}")
    after = track_items(v, tl)
    clips_after = [i for i in after if i["id"] != tr["id"]]
    expect([i["id"] for i in clips_after] == [i["id"] for i in before], "the clips on the track changed")
    listed = any(i["id"] == tr["id"] for i in after)
    at2 = ok(c, "timeline_item", "get_unique_id", {**pin, "item_index": 2}).get("id")
    by_id = ok(c, "timeline_item", "get_start", {"timeline_name": tl, "timeline_item_id": before[2]["id"]}).get("start")
    expect(by_id == before[2]["start"], f"timeline_item_id of clip 2 now gives start {by_id}, expected {before[2]['start']}")
    if listed:
        expect(at2 == tr["id"], f"transition listed in the track but item_index 2 is {at2}, not the transition")
        at3 = ok(c, "timeline_item", "get_unique_id", {**pin, "item_index": 3}).get("id")
        expect(at3 == before[2]["id"], f"item_index 3 is {at3}, expected clip 2 ({before[2]['id']})")
        how = "item_index counts the transition: index 2 is now the transition, clip 2 moved to index 3"
    else:
        expect(at2 == before[2]["id"], f"transition not listed, yet item_index 2 is {at2}")
        how = "transition not listed in the track: item_index unchanged"
    return f"{tr['name']} {tr['start']}-{tr['end']} on the cut at {cut}; {how}; timeline_item_id still finds clip 2"


def check_drx_timeline_target(v, c, node):
    if node is None:
        raise Skip(f"Node advanced server not found at {NODE_BIN}")
    if "drx" not in node.tools():
        raise Skip("Node advanced server has no drx tool")
    SCRATCH.mkdir(parents=True, exist_ok=True)
    flag, gen = raw_node(node, {"action": "generate", "args": {
        "gradeParams": {"saturation": 0}, "target": "timeline", "resolveVersion": 21, "outputPath": str(DRX)}})
    expect(not flag and DRX.exists(), f"drx generate target timeline: {str(gen)[:300]}")
    text = DRX.read_text()
    expect("<pTrackVer>" in text, "target timeline wrote no <pTrackVer>")
    expect("<HasCorrection>true" in text.split("<pTrackVer>", 1)[1], "the <pTrackVer> grade has no correction")
    flag, clip_gen = raw_node(node, {"action": "generate", "args": {"gradeParams": {"saturation": 0}, "resolveVersion": 21}})
    content = clip_gen.get("content", "") if isinstance(clip_gen, dict) else str(clip_gen)
    expect(not flag and content, f"drx generate (clip target): {str(clip_gen)[:200]}")
    expect("<pTrackVer>" not in content, "the default (clip) target also wrote a timeline grade")
    tl = clip_timeline(v, SCRATCH_PREFIX + "t25_tlgrade", str(rlib.media_clip()))
    f0, n0 = frame(v, tl), timeline_nodes(v, tl)
    if n0 is None:
        raise Skip("Timeline.GetNodeGraph() is not available")
    params = {"path": str(DRX), "source": "timeline", "timeline_name": tl}
    flag, ap = raw(c, "graph", "apply_grade_from_drx", params)
    confirmed = isinstance(ap, dict) and bool(ap.get("confirm_token"))
    if confirmed:
        flag, ap = raw(c, "graph", "apply_grade_from_drx", {**params, "confirm_token": ap["confirm_token"]})
    expect(not flag and isinstance(ap, dict) and ap.get("success"), f"apply_grade_from_drx: {str(ap)[:300]}")
    expect("timeline" in (ap.get("changed_graphs") or []), f"changed_graphs {ap.get('changed_graphs')}")
    f1, n1 = frame(v, tl), timeline_nodes(v, tl)
    s0, s1 = mean_saturation(f0), mean_saturation(f1)
    expect(n1 >= 1, f"timeline graph nodes {n0} -> {n1}: the apply left the timeline graph empty")
    expect(s0 > 50 and s1 < 5, f"mean saturation {s0:.0f} -> {s1:.0f}: the timeline grade (saturation 0) did not reach the frame")
    return (f"pTrackVer written (clip target: none); applied {'after confirm_token' if confirmed else 'directly'}; "
            f"timeline nodes {n0} -> {n1}; frame saturation {s0:.0f} -> {s1:.0f}")


def raw_node(node, arguments, timeout=120):
    r = node.rpc("tools/call", {"name": "drx", "arguments": arguments}, timeout=timeout)
    if "error" in r:
        return True, r["error"]
    out = r.get("result", {})
    text = "".join(x.get("text", "") for x in out.get("content", []) if x.get("type") == "text")
    try:
        return bool(out.get("isError")), json.loads(text)
    except ValueError:
        return bool(out.get("isError")), text


def subtitle_timeline(v):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    SRT.write_text("1\n00:00:00,000 --> 00:00:04,000\nHELLO WORLD TEST\n\n2\n00:00:04,000 --> 00:00:08,000\nSECOND LINE HERE\n\n")
    tl = rlib.scratch_timeline(v, "t25_subs")
    r = run_script(v, FIXTURE_PRELUDE + f"""
t = timeline({tl!r})
proj.SetCurrentTimeline(t)
clips = mp.ImportMedia([{str(SRT)!r}]) or []
appended = bool(mp.AppendToTimeline(clips)) if clips else False
result = {{"clips": len(clips), "appended": appended, "subs": len(t.GetItemListInTrack("subtitle", 1) or [])}}
""")
    expect(r["subs"] == 2, f"the .srt did not land on a subtitle track: {r}")
    return tl


def render_subtitles(v, tl, key):
    """Render RENDER_FRAMES frames from the first subtitle with burned-in subtitles. -> (status, frames, png)."""
    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    for f in RENDER_DIR.glob(key + "*"):
        f.unlink()
    job = run_script(v, FIXTURE_PRELUDE + f"""
t = timeline({tl!r})
proj.SetCurrentTimeline(t)
subs = t.GetItemListInTrack("subtitle", 1) or []
first = subs[0].GetStart()
proj.DeleteAllRenderJobs()
proj.SetCurrentRenderFormatAndCodec("mov", "ProRes422")
ok = proj.SetRenderSettings({{"SelectAllFrames": False, "MarkIn": first, "MarkOut": first + {RENDER_FRAMES - 1},
    "TargetDir": {str(RENDER_DIR)!r}, "CustomName": {key!r}, "ExportVideo": True, "ExportAudio": False,
    "ExportSubtitle": True, "SubtitleFormat": "BurnIn"}})
jid = proj.AddRenderJob() if ok else None
result = {{"job": jid, "started": bool(proj.StartRendering([jid])) if jid else False}}
""")
    expect(job["started"], f"render did not start: {job}")
    st = [True, {}]
    for _ in range(120):
        time.sleep(1)
        st = run_script(v, FIXTURE_PRELUDE + f"result = [bool(proj.IsRenderingInProgress()), proj.GetRenderJobStatus({job['job']!r})]")
        if not st[0]:
            break
    status = st[1] or {}
    movs = sorted(RENDER_DIR.glob(key + "*.mov"))
    frames, png = 0, b""
    if movs:
        frames = int(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=nb_frames",
                                     "-of", "csv=p=0", str(movs[0])], capture_output=True, text=True).stdout.strip() or 0)
        png = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", "0.5", "-i", str(movs[0]), "-frames:v", "1",
                              "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
    run_script(v, FIXTURE_PRELUDE + "proj.DeleteAllRenderJobs()\nresult = True")
    return status, frames, png


def bright_pixels(png):
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGB")
    return sum(1 for p in pixels(im) if max(p) > 150)


def check_subtitle_presets(c, v):
    tl = subtitle_timeline(v)
    st, frames, png = render_subtitles(v, tl, SCRATCH_PREFIX + "t25_plain")
    expect(frames == RENDER_FRAMES and png, f"plain subtitles render: {st}, {frames} frames")
    plain = bright_pixels(png)
    expect(plain > 500, f"plain subtitles: only {plain} bright pixels, burn-in not working")
    out = [f"plain {plain} px"]
    for preset in PRESETS:
        key = preset.split()[0].lower()
        run_script(v, FIXTURE_PRELUDE + f"proj.SetCurrentTimeline(timeline({tl!r}))\nresult = True")
        flag, data = raw(c, "timeline_ai", "set_subtitle_preset",
                         {"preset": preset, "revision_name": f"{SCRATCH_PREFIX}t25_subs_{key}"}, timeout=240)
        if unknown_action(data):
            raise Skip("timeline_ai has no set_subtitle_preset on this server")
        expect(not flag and isinstance(data, dict) and data.get("success"), f"set_subtitle_preset {preset}: {str(data)[:300]}")
        after = (data.get("patch") or {}).get("after") or {}
        font = f"{after.get('font')} {after.get('fontStyle')}"
        st, frames, png = render_subtitles(v, data["timeline"], f"{SCRATCH_PREFIX}t25_{key}")
        expect(frames == RENDER_FRAMES and st.get("JobStatus") == "Complete",
               f"{preset} ({font}): render {st.get('JobStatus')} {st.get('Error', '')}, {frames}/{RENDER_FRAMES} frames "
               "(a missing font fails the Fusion title: see t00's font check)")
        px = bright_pixels(png)
        expect(px > 500, f"{preset} ({font}): rendered, but only {px} bright pixels: the text is blank (missing font?)")
        out.append(f"{preset} ({font}) {px} px")
    return "; ".join(out)


# ── run ──────────────────────────────────────────────────────────────────────

def cleanup(v):
    notes, errors = [], []
    for step, fn in (("timelines", rlib.drop_scratch), ("clips", rlib.drop_scratch_clips)):
        try:
            r = fn(v)
            (notes if r.get("ok") else errors).append(f"{step}: {r}")
        except Exception as exc:
            errors.append(f"{step}: {type(exc).__name__}: {exc}")
    for p in (DRX, SRT):
        p.unlink(missing_ok=True)
    for f in RENDER_DIR.glob("*") if RENDER_DIR.exists() else []:
        f.unlink()
    if RENDER_DIR.exists():
        RENDER_DIR.rmdir()
    expect(not errors, "; ".join(errors))
    return "scratch timelines, clips, .drx, .srt and renders removed"


def main():
    rlib.main_guard()
    node = rlib.McpStdio(["node", str(NODE_BIN)], timeout=120) if NODE_BIN.exists() else None
    try:
        with rlib.vendor() as v, rlib.community() as c:
            try:
                suite.check("add_transition: real transition, item_index vs timeline_item_id",
                            lambda: check_transition_index(c, v))
                suite.check("drx generate target timeline -> timeline grade applied and rendered",
                            lambda: check_drx_timeline_target(v, c, node))
                suite.check("subtitle presets render burned-in text (Word Highlight, Lollipop)",
                            lambda: check_subtitle_presets(c, v))
            finally:
                suite.check("cleanup", lambda: cleanup(v))
    finally:
        if node is not None:
            node.close()
    raise SystemExit(suite.finish())


if __name__ == "__main__":
    main()
