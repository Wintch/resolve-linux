#!/usr/bin/env python3
"""Live checks of the Resolve 21.2 scripting additions, run through the vendor ResolveMCP's
sandboxed `run_script`.

Covers effects (GetEffectsList, AddEffect / GetEffects / DeleteEffects, Effect enable toggle),
still export (Timeline / TimelineItem / MediaPoolItem ExportFrame and ExportThumbnails with their
options), edit operations (GetPosition / SetPosition, DeleteGaps, MoveItems, SetLeftOffset /
SetRightOffset, RippleDeleteSilence), grading (AutoColor, GetCDL, CopyAttributes, the new Graph
node label / color / enabled getters and setters), Timeline enable state and the read-only
Extras / job status getters.

Every check proves the feature with values: exported frames are compared byte for byte (an effect
must change the frame and disabling or deleting it must bring the exact original back), durations
and positions must move by the requested number of frames, round trips must read back what was set.

Below Resolve 21.2 every check that needs a 21.2 call is skipped (a version gate: Resolve.GetVersion),
and a call that is not there at all is skipped too (the method is missing on the object).
Only scratch timelines (SCRATCH_PREFIX) in the fixture project are touched; they are dropped at the
end, the synthetic clips imported into the Media Pool for the run are deleted from it again, and the
one frame written to disk is removed. The synthetic media files themselves are cached in SCRATCH.
"""
import base64
import re
import struct

import rlib
from rlib import FIXTURE_PRELUDE, PNG_MAGIC, SCRATCH, SCRATCH_PREFIX, Skip, Warn, expect, run_script, sha

GRAIN_ID = "Resolve FX/Resolve FX Film Emulation/Film Grain"
JPEG_MAGIC = b"\xff\xd8\xff"
SMALL = {"encodedImageFormat": "png", "maxDimension": 480}
FRAME_FILE = SCRATCH / (SCRATCH_PREFIX + "t40_frame.png")
NODE_LABEL = SCRATCH_PREFIX + "node"
TEST_CDL = {"Slope": "0.5 0.4 0.2", "Offset": "0.04 0.03 0.02", "Power": "0.9 1.0 1.1", "Saturation": 0.8}
STATUS_GETTERS = ("GetTranscribeAudioStatus", "GetAnalyzeForSlateStatus", "GetSmartReframeStatus",
                  "GetDetectSceneCutsStatus", "GetCreateSubtitlesFromAudioStatus", "GetRenderWithQuickExportStatus")

# Runs inside the sandbox after FIXTURE_PRELUDE. No dunder attributes, no imports.
HELPERS = FIXTURE_PRELUDE + '''
def need(obj, *names):
    for n in names:
        if getattr(obj, n, None) is None:
            raise RuntimeError("ABSENT:" + n)
def scratch(name):
    tl = timeline(name)
    if tl is None:
        raise RuntimeError("scratch timeline " + name + " is missing")
    proj.SetCurrentTimeline(tl)
    return tl
def video_item(tl, i=0):
    items = tl.GetItemListInTrack("video", 1) or []
    if len(items) <= i:
        raise RuntimeError("video track 1 has only " + str(len(items)) + " items")
    return items[i]
def plain(x):
    if x is None or isinstance(x, (bool, int, float, str)):
        return x
    if isinstance(x, dict):
        return {str(k): plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [plain(v) for v in x]
    return repr(x)
def shot(obj, n=10, opts=None):
    r = obj.ExportFrame(n, dict(opts) if opts else {"encodedImageFormat": "png"})
    if not isinstance(r, dict):
        return {"success": False, "error": "ExportFrame returned " + repr(r)[:80], "data": ""}
    return {"success": bool(r.get("success")), "error": r.get("error"), "width": r.get("width"),
            "height": r.get("height"), "data": r.get("data") or ""}
def thumbs(obj, frames, opts):
    r = obj.ExportThumbnails(frames, dict(opts))
    if not isinstance(r, dict):
        return {"success": False, "error": "ExportThumbnails returned " + repr(r)[:80], "data": ""}
    out = plain({k: v for k, v in r.items() if k != "data"})
    out["success"] = bool(r.get("success"))
    out["data"] = r.get("data") or ""
    return out
'''


class Ctx:
    v = None
    version = None
    timelines = {}


def rs(body, timeout=60):
    """run_script with HELPERS; a sandbox `need()` miss becomes a Skip."""
    try:
        return run_script(Ctx.v, HELPERS + body, timeout)
    except RuntimeError as exc:
        m = re.search(r"ABSENT:(\w+)", str(exc))
        if m:
            raise Skip(f"{m.group(1)} is not in this Resolve")
        raise


def gate():
    """Skip below Resolve 21.2 (capability gate for the calls this module exercises)."""
    if Ctx.version is None:
        Ctx.version = run_script(Ctx.v, "result = resolve.GetVersion()")
    try:
        major, minor = int(Ctx.version[0]), int(Ctx.version[1])
    except (TypeError, ValueError, IndexError):
        raise Skip(f"unreadable Resolve version {Ctx.version!r}")
    if (major, minor) < (21, 2):
        raise Skip(f"Resolve {major}.{minor} predates the 21.2 scripting additions")


def dup_timeline(suffix):
    """bench_tl duplicate (8 Solid Color generators), built once per run."""
    key = ("dup", suffix)
    if key not in Ctx.timelines:
        Ctx.timelines[key] = rlib.scratch_timeline(Ctx.v, suffix)
    return Ctx.timelines[key]


def clip_timeline(suffix, path, copies=1, track="video"):
    """A scratch timeline holding `copies` back-to-back instances of one synthetic clip on `track` 1
    ("video" for an A/V clip, "audio" for an audio-only file). The clip is imported once per run and
    reused from the Media Pool; main() removes it again."""
    key = ("clip", suffix)
    if key in Ctx.timelines:
        return Ctx.timelines[key]
    name = SCRATCH_PREFIX + suffix
    r = run_script(Ctx.v, FIXTURE_PRELUDE + f'''
path = {str(path)!r}
old = timeline({name!r})
if old is not None:
    mp.DeleteTimelines([old])
def find(folder):
    for c in folder.GetClipList() or []:
        if c.GetClipProperty("File Path") == path:
            return c
    for sub in folder.GetSubFolderList() or []:
        hit = find(sub)
        if hit:
            return hit
    return None
clip = find(mp.GetRootFolder())
if clip is None:
    got = resolve.GetMediaStorage().AddItemListToMediaPool([path]) or []
    clip = got[0] if got else None
if clip is None:
    raise RuntimeError("could not import " + path + " (it must sit inside a Media Storage volume)")
tl = mp.CreateTimelineFromClips({name!r}, [clip])
if tl is None:
    raise RuntimeError("CreateTimelineFromClips failed for {name}")
proj.SetCurrentTimeline(tl)
for _ in range({copies} - 1):
    if not mp.AppendToTimeline([clip]):
        raise RuntimeError("AppendToTimeline failed for {name}")
result = {{"name": tl.GetName(), "video": len(tl.GetItemListInTrack("video", 1) or []),
          "audio": len(tl.GetItemListInTrack("audio", 1) or [])}}
''')
    expect(r["name"] == name, f"timeline got name {r['name']!r}")
    expect(r[track] == copies, f"{name}: {r['video']} video / {r['audio']} audio items, expected {copies} on {track} 1")
    Ctx.timelines[key] = name
    return name


def media_tl(suffix):
    return clip_timeline(suffix, rlib.media_clip())


def img(s, what):
    expect(isinstance(s, dict) and s.get("success"), f"{what}: export failed: {s.get('error') if isinstance(s, dict) else s!r}")
    data = base64.b64decode(s["data"])
    expect(data, f"{what}: empty image data")
    return data


def png_size(data, what):
    expect(data[:8] == PNG_MAGIC, f"{what}: not a PNG (starts {data[:8]!r})")
    return struct.unpack(">II", data[16:24])


def close(a, b, tol=1e-3):
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def triple(s):
    try:
        vals = [float(x) for x in str(s).split()]
    except ValueError:
        return None
    return vals if len(vals) == 3 else None


# ---- (1) effects list ---------------------------------------------------------------------

def c_effects_list():
    gate()
    r = rs('''
need(resolve, "GetEffectsList")
vid = resolve.GetEffectsList("video")
aud = resolve.GetEffectsList("audio")
result = {"product": resolve.GetProductName(),
          "video": plain(vid) if isinstance(vid, list) else None, "video_raw": repr(vid)[:80],
          "audio": plain(aud) if isinstance(aud, list) else None, "audio_raw": repr(aud)[:80]}
''')
    vid, aud = r["video"], r["audio"]
    expect(isinstance(vid, list) and vid, f"GetEffectsList('video') -> {r['video_raw']}")
    bad = [e for e in vid if not (isinstance(e, dict) and e.get("id") and e.get("category") in ("fusion", "ofx"))]
    expect(not bad, f"{len(bad)} video effects without id/category fusion|ofx, e.g. {bad[:1]}")
    expect(isinstance(aud, list), f"GetEffectsList('audio') is not a list: {r['audio_raw']}")
    grain = [e for e in vid if e["id"] == GRAIN_ID]
    if not grain:
        msg = f"{GRAIN_ID!r} not in {len(vid)} video effects"
        if "studio" not in str(r["product"]).lower():
            raise Warn(msg + f" (product {r['product']!r}, Film Grain is a Studio effect)")
        raise AssertionError(msg)
    expect(grain[0]["category"] == "ofx", f"Film Grain category {grain[0]['category']!r}")
    cats = sorted({e["category"] for e in vid})
    return f"{len(vid)} video effects {cats}, Film Grain ofx present; {len(aud)} audio effects"


# ---- (2) effect round trip ----------------------------------------------------------------

def c_effect_roundtrip():
    gate()
    name = media_tl("fx")
    pre = f"tl = scratch({name!r})\nitem = video_item(tl)\nGRAIN = {GRAIN_ID!r}\n"
    find = ('eff = next((e for e in item.GetEffects("ofx") or [] if (e.GetInfo() or {}).get("id") == GRAIN), None)\n'
            'if eff is None:\n    raise RuntimeError("the added Film Grain effect is gone")\n')
    r = rs(pre + 'need(item, "AddEffect", "GetEffects", "DeleteEffects")\nresult = [shot(tl), shot(tl)]')
    base, again = img(r[0], "base"), img(r[1], "base repeat")
    expect(base == again, f"ExportFrame not deterministic: {sha(base)} vs {sha(again)}")
    try:
        r = rs(pre + '''
eff = item.AddEffect("ofx", GRAIN)
if eff is None:
    raise RuntimeError("AddEffect returned None")
need(eff, "GetInfo", "GetIsEnabled", "SetIsEnabled")
result = {"info": plain(eff.GetInfo()), "enabled": eff.GetIsEnabled(),
          "ids": [(e.GetInfo() or {}).get("id") for e in item.GetEffects("ofx") or []], "shot": shot(tl)}
''')
        info = r["info"] or {}
        expect(info.get("id") == GRAIN_ID and info.get("category") == "ofx", f"Effect.GetInfo -> {info}")
        expect(GRAIN_ID in r["ids"], f"GetEffects('ofx') ids {r['ids']}")
        expect(r["enabled"] is True, f"new effect GetIsEnabled -> {r['enabled']!r}")
        fx = img(r["shot"], "with Film Grain")
        expect(fx != base, f"Film Grain did not change the frame ({sha(base)})")

        r = rs(pre + find + 'ok = eff.SetIsEnabled(False)\nresult = {"ok": ok, "enabled": eff.GetIsEnabled(), "shot": shot(tl)}')
        expect(r["ok"] is True and r["enabled"] is False, f"SetIsEnabled(False) -> {r['ok']!r}, GetIsEnabled {r['enabled']!r}")
        off = img(r["shot"], "effect disabled")
        expect(off == base, f"disabled effect frame {sha(off)} != base {sha(base)}")

        r = rs(pre + find + 'ok = eff.SetIsEnabled(True)\nresult = {"ok": ok, "enabled": eff.GetIsEnabled(), "shot": shot(tl)}')
        expect(r["ok"] is True and r["enabled"] is True, f"SetIsEnabled(True) -> {r['ok']!r}, GetIsEnabled {r['enabled']!r}")
        on = img(r["shot"], "effect re-enabled")
        expect(on != base, "re-enabled effect leaves the frame unchanged")

        r = rs(pre + find + '''
ok = item.DeleteEffects([eff])
result = {"ok": ok, "ids": [(e.GetInfo() or {}).get("id") for e in item.GetEffects("ofx") or []], "shot": shot(tl)}
''')
        expect(r["ok"] is True and GRAIN_ID not in r["ids"], f"DeleteEffects -> {r['ok']!r}, left {r['ids']}")
        gone = img(r["shot"], "effect deleted")
        expect(gone == base, f"frame after DeleteEffects {sha(gone)} != base {sha(base)}")
    finally:
        try:
            rs(pre + '''
left = [e for e in item.GetEffects("ofx") or [] if (e.GetInfo() or {}).get("id") == GRAIN]
result = item.DeleteEffects(left) if left else True
''')
        except Exception:
            pass
    return (f"base {sha(base)} stable; grain {sha(fx)} (re-enabled {'same' if on == fx else sha(on)}); "
            "disabled and deleted both == base")


# ---- (3) ExportFrame options --------------------------------------------------------------

def timeline_size(name):
    r = rs(f'tl = scratch({name!r})\nresult = [tl.GetSetting("timelineResolutionWidth"), tl.GetSetting("timelineResolutionHeight")]')
    return int(float(r[0])), int(float(r[1]))


def c_export_maxdim():
    gate()
    name = media_tl("media")
    W, H = timeline_size(name)
    r = rs(f'tl = scratch({name!r})\nneed(tl, "ExportFrame")\nresult = shot(tl, 10, {{"encodedImageFormat": "png", "maxDimension": 320}})')
    data = img(r, "maxDimension 320")
    w, h = png_size(data, "maxDimension 320")
    expect((w, h) == (r["width"], r["height"]), f"PNG is {w}x{h}, result says {r['width']}x{r['height']}")
    expect(max(w, h) == min(320, max(W, H)), f"{w}x{h} for maxDimension 320 (timeline {W}x{H})")
    expect(abs(w / h - W / H) < 0.02, f"aspect {w}x{h} vs timeline {W}x{H}")
    return f"{w}x{h} from a {W}x{H} timeline"


def c_export_jpg():
    gate()
    name = media_tl("media")
    r = rs(f'''tl = scratch({name!r})
result = [shot(tl, 10, {{"encodedImageFormat": "jpg", "maxDimension": 320, "quality": q}}) for q in (95, 20)]''')
    hi, lo = img(r[0], "jpg q95"), img(r[1], "jpg q20")
    expect(hi[:3] == JPEG_MAGIC and lo[:3] == JPEG_MAGIC, f"not JPEG: {hi[:4]!r} / {lo[:4]!r}")
    expect(len(hi) > len(lo), f"quality ignored: q95 {len(hi)} B, q20 {len(lo)} B")
    return f"JPEG q95 {len(hi)} B > q20 {len(lo)} B"


def c_export_file():
    gate()
    name = media_tl("media")
    SCRATCH.mkdir(parents=True, exist_ok=True)
    FRAME_FILE.unlink(missing_ok=True)
    try:
        r = rs(f'tl = scratch({name!r})\nr = shot(tl, 10, {{"outputType": "file", "filePath": {str(FRAME_FILE)!r}}})\n'
               'r.pop("data")\nresult = r')
        expect(r.get("success"), f"ExportFrame to file failed: {r.get('error')}")
        expect(FRAME_FILE.exists(), f"success but no file at {FRAME_FILE.name}")
        w, h = png_size(FRAME_FILE.read_bytes(), "file output")
        if r.get("width"):
            expect((w, h) == (r["width"], r["height"]), f"file is {w}x{h}, result says {r['width']}x{r['height']}")
        return f"{FRAME_FILE.name}: PNG {w}x{h}, {FRAME_FILE.stat().st_size} B"
    finally:
        FRAME_FILE.unlink(missing_ok=True)


def c_export_roi():
    """roiWidth / roiHeight are fractions per axis: a 0.5 x 0.5 region is half the frame on each side, at
    full resolution. It must be that region, not the whole frame scaled down (compared with the whole frame
    exported at the same size), and roiX / roiY must move it (centre vs top-left corner differ)."""
    gate()
    name = media_tl("media")
    W, H = timeline_size(name)
    r = rs(f'tl = scratch({name!r})\n'
           'result = {"centre": shot(tl, 10, {"encodedImageFormat": "png", "roiX": 0.25, "roiY": 0.25, "roiWidth": 0.5, "roiHeight": 0.5}),\n'
           '          "corner": shot(tl, 10, {"encodedImageFormat": "png", "roiX": 0.0, "roiY": 0.0, "roiWidth": 0.5, "roiHeight": 0.5}),\n'
           f'          "whole": shot(tl, 10, {{"encodedImageFormat": "png", "maxDimension": {max(W, H) // 2}}})}}')
    centre, corner, whole = img(r["centre"], "roi centre"), img(r["corner"], "roi corner"), img(r["whole"], "whole frame, half size")
    w, h = png_size(centre, "roi centre")
    expect((w, h) != (W, H), f"roi ignored: full {w}x{h}")
    expect(abs(w - W / 2) <= 2 and abs(h - H / 2) <= 2, f"roi 0.5x0.5 of {W}x{H} gave {w}x{h}, expected about {W // 2}x{H // 2}")
    expect(png_size(corner, "roi corner") == (w, h), f"corner roi {png_size(corner, 'roi corner')} vs centre {w}x{h}")
    expect(centre != whole, f"the centre region is byte-identical to the whole frame at {w}x{h}: the region was not cropped")
    expect(centre != corner, "centre and top-left regions are identical: roiX / roiY ignored")
    return f"roi 0.5x0.5 -> {w}x{h}; centre {sha(centre)} != corner {sha(corner)} != whole frame scaled {sha(whole)}"


# ---- (4) thumbnails and item / clip exports -----------------------------------------------

def check_sheet(r, what, cells, cap):
    data = img(r, what)
    w, h = png_size(data, what)
    expect((w, h) == (r.get("width"), r.get("height")), f"{what}: PNG {w}x{h}, result {r.get('width')}x{r.get('height')}")
    expect(max(w, h) <= cap, f"{what}: {w}x{h} exceeds maxDimension {cap}")
    cols, rows = r.get("columns") or 0, r.get("rows") or 0
    expect(cols * rows >= cells, f"{what}: grid {cols}x{rows} for {cells} frames")
    cw, ch = r.get("cellWidth") or 0, r.get("cellHeight") or 0
    expect(0 < cw * cols <= w and 0 < ch * rows <= h, f"{what}: cells {cw}x{ch} x {cols}x{rows} vs sheet {w}x{h}")
    return f"{w}x{h} grid {cols}x{rows}"


def c_timeline_thumbnails():
    gate()
    name = media_tl("media")
    r = rs(f'tl = scratch({name!r})\nneed(tl, "ExportThumbnails")\n'
           'result = thumbs(tl, [0, 25, {"frameOffset": 50, "label": "zz"}, 75], {"encodedImageFormat": "png", "maxDimension": 640})')
    return "Timeline sheet " + check_sheet(r, "Timeline.ExportThumbnails", 4, 640)


def c_mediapoolitem_export():
    gate()
    name = media_tl("media")
    r = rs(f'''tl = scratch({name!r})
mpi = video_item(tl).GetMediaPoolItem()
if mpi is None:
    raise RuntimeError("timeline item has no MediaPoolItem")
need(mpi, "ExportFrame", "ExportThumbnails")
result = {{"a": shot(mpi, 10, {SMALL!r}), "b": shot(mpi, 60, {SMALL!r}),
          "sheet": thumbs(mpi, [0, 50, 100], {{"encodedImageFormat": "png", "maxDimension": 480}})}}''')
    a, b = img(r["a"], "clip frame 10"), img(r["b"], "clip frame 60")
    wa, ha = png_size(a, "clip frame 10")
    expect(max(wa, ha) <= 480, f"clip frame {wa}x{ha} exceeds maxDimension 480")
    expect(a != b, "clip frames 10 and 60 are identical (testsrc2 moves)")
    return f"clip frame {wa}x{ha}, 10 != 60; sheet " + check_sheet(r["sheet"], "MediaPoolItem.ExportThumbnails", 3, 480)


def c_timelineitem_export():
    gate()
    name = media_tl("media")
    r = rs(f'''tl = scratch({name!r})
item = video_item(tl)
need(item, "ExportFrame", "ExportThumbnails")
result = {{"a": shot(item, 10, {SMALL!r}), "b": shot(item, 60, {SMALL!r}), "tl": shot(tl, 10, {SMALL!r}),
          "sheet": thumbs(item, [0, 30], {{"encodedImageFormat": "png", "maxDimension": 480}})}}''')
    a, b, t = img(r["a"], "item frame 10"), img(r["b"], "item frame 60"), img(r["tl"], "timeline frame 10")
    wa, ha = png_size(a, "item frame 10")
    expect(max(wa, ha) <= 480, f"item frame {wa}x{ha} exceeds maxDimension 480")
    expect(a != b, "item frames 10 and 60 are identical (testsrc2 moves)")
    same = "== timeline frame 10" if a == t else "differs from timeline frame 10"
    return f"item frame {wa}x{ha} {same}, 10 != 60; sheet " + check_sheet(r["sheet"], "TimelineItem.ExportThumbnails", 2, 480)


# ---- (5) positions and gaps, (6) MoveItems, (7) trims ---------------------------------------

def c_position_gaps():
    gate()
    name = dup_timeline("pos")
    n = 12
    r = rs(f'''tl = scratch({name!r})
items = tl.GetItemListInTrack("video", 1) or []
if len(items) < 3:
    raise RuntimeError("bench_tl duplicate has " + str(len(items)) + " items")
need(items[1], "GetPosition", "SetPosition")
need(tl, "DeleteGaps")
p2 = plain(items[1].GetPosition())
s2 = items[1].GetStart()
last, prev = items[-1], items[-2]
before = {{"start": last.GetStart(), "pos": plain(last.GetPosition()), "prev_end": prev.GetEnd()}}
ok = last.SetPosition({{"startFrame": before["pos"]["startFrame"] + {n}}})
items = tl.GetItemListInTrack("video", 1) or []
result = {{"p2": p2, "s2": s2, "before": before, "ok": ok, "count": len(items),
          "after": {{"start": items[-1].GetStart(), "pos": plain(items[-1].GetPosition())}}}}''')
    p2 = r["p2"] or {}
    expect(p2.get("trackType") == "video" and p2.get("trackIndex") == 1 and close(p2.get("startFrame"), r["s2"]),
           f"item 2 GetPosition {p2} vs GetStart {r['s2']}")
    b, a = r["before"], r["after"]
    expect(r["ok"] is True, f"SetPosition returned {r['ok']!r}")
    expect(close(a["start"], b["start"] + n) and close(a["pos"]["startFrame"], b["pos"]["startFrame"] + n),
           f"last item start {b['start']} -> {a['start']} (pos {a['pos']}), wanted +{n}")
    r = rs(f'''tl = scratch({name!r})
ok = tl.DeleteGaps()
items = tl.GetItemListInTrack("video", 1) or []
result = {{"ok": ok, "count": len(items), "start": items[-1].GetStart(), "prev_end": items[-2].GetEnd()}}''')
    expect(r["ok"] is True, f"DeleteGaps returned {r['ok']!r}")
    expect(close(r["start"], r["prev_end"]), f"after DeleteGaps last starts {r['start']}, previous ends {r['prev_end']}")
    expect(close(r["start"], b["start"]), f"after DeleteGaps last starts {r['start']}, originally {b['start']}")
    return f"item 2 at {p2.get('startFrame')}; last item +{n} -> gap -> DeleteGaps back to {r['start']}"


def c_move_items():
    gate()
    name = dup_timeline("move")
    n = 10
    r = rs(f'''tl = scratch({name!r})
need(tl, "MoveItems")
items = tl.GetItemListInTrack("video", 1) or []
if len(items) < 3:
    raise RuntimeError("bench_tl duplicate has " + str(len(items)) + " items")
before = [i.GetStart() for i in items]
p = plain(items[-2].GetPosition())
ok = tl.MoveItems(items[-2:], {{"startFrame": p["startFrame"] + {n}}})
result = {{"ok": ok, "before": before, "after": [i.GetStart() for i in tl.GetItemListInTrack("video", 1) or []]}}''')
    b, a = r["before"], r["after"]
    expect(r["ok"] is True, f"MoveItems returned {r['ok']!r}")
    expect(len(a) == len(b), f"item count {len(b)} -> {len(a)}")
    expect(all(close(x, y) for x, y in zip(a[:-2], b[:-2])), f"unmoved items changed: {b[:-2]} -> {a[:-2]}")
    expect(all(close(x, y + n) for x, y in zip(a[-2:], b[-2:])), f"moved starts {b[-2:]} -> {a[-2:]}, wanted +{n}")
    return f"last two items {b[-2:]} -> {a[-2:]}"


def c_trim_offsets():
    gate()
    name = media_tl("trim")
    r = rs(f'''tl = scratch({name!r})
def state():
    v = video_item(tl)
    auds = tl.GetItemListInTrack("audio", 1) or []
    return {{"d": v.GetDuration(), "l": v.GetLeftOffset(), "r": v.GetRightOffset(), "s": v.GetStart(),
            "ad": auds[0].GetDuration() if auds else None, "as": auds[0].GetStart() if auds else None}}
need(video_item(tl), "SetLeftOffset", "SetRightOffset")
b = state()
okl = video_item(tl).SetLeftOffset(b["l"] + 10)
m = state()
okr = video_item(tl).SetRightOffset(b["r"] + 5)
a = state()
result = {{"b": b, "m": m, "a": a, "okl": okl, "okr": okr}}''')
    b, m, a = r["b"], r["m"], r["a"]
    expect(r["okl"] is True, f"SetLeftOffset returned {r['okl']!r}")
    expect(close(m["d"], b["d"] - 10) and close(m["l"], b["l"] + 10),
           f"left trim 10: duration {b['d']} -> {m['d']}, left offset {b['l']} -> {m['l']}")
    expect(close(m["s"], b["s"] + 10), f"left trim 10 is a trim: start {b['s']} -> {m['s']}, want +10 (not a slip or ripple)")
    expect(r["okr"] is True, f"SetRightOffset returned {r['okr']!r}")
    expect(close(a["d"], m["d"] - 5) and close(a["r"], b["r"] + 5),
           f"right trim 5: duration {m['d']} -> {a['d']}, right offset {b['r']} -> {a['r']}")
    expect(close(a["s"], m["s"]), f"right trim moved the start {m['s']} -> {a['s']}")
    detail = f"start {b['s']} -> {m['s']} -> {a['s']}, duration {b['d']} -> {m['d']} -> {a['d']}"
    if b["ad"] is not None:   # updateLinkedItems defaults to True: the linked audio must follow
        expect(close(a["ad"], a["d"], 0.5) and close(a["as"], a["s"], 0.5),
               detail + f"; linked audio not trimmed along (start {b['as']} -> {a['as']}, duration {b['ad']} -> {a['ad']})")
    return detail + ("; linked audio followed" if b["ad"] is not None else "")


# ---- (8) RippleDeleteSilence --------------------------------------------------------------

def c_ripple_delete_silence():
    gate()
    name = clip_timeline("silence", rlib.tone_with_silence(), track="audio")
    r = rs(f'''tl = scratch({name!r})
def span():
    its = tl.GetItemListInTrack("audio", 1) or []
    return {{"n": len(its), "total": sum(i.GetDuration() for i in its),
            "first": min(i.GetStart() for i in its) if its else None, "end": max(i.GetEnd() for i in its) if its else None}}
items = tl.GetItemListInTrack("audio", 1) or []
if not items:
    raise RuntimeError("no audio item on the silence timeline")
need(items[0], "RippleDeleteSilence")
before = span()
ok = items[0].RippleDeleteSilence({{"threshold": -45.0}})
result = {{"fps": tl.GetSetting("timelineFrameRate"), "ok": ok, "before": before, "after": span()}}''')
    fps = float(str(r["fps"]).split()[0])
    b, a = r["before"], r["after"]
    expect(r["ok"] is True, f"RippleDeleteSilence returned {r['ok']!r} (audio items {b['n']} -> {a['n']})")
    expect(a["n"] > 0, f"RippleDeleteSilence(threshold -45 dB) returned True and left {a['n']} audio items "
                       f"(was {b['n']}, {b['total']} frames: tone, 2 s silence, tone)")
    removed = b["total"] - a["total"]
    shrunk = (b["end"] - b["first"]) - (a["end"] - a["first"])
    want, tol = 2 * fps, 0.25 * fps
    expect(abs(removed - want) <= tol, f"removed {removed} frames of audio, expected about {want:g} (2 s at {fps:g} fps)")
    expect(abs(shrunk - want) <= tol, f"timeline span shrank {shrunk} frames (no ripple?), expected about {want:g}")
    return f"{b['total']} -> {a['total']} frames ({a['n']} pieces), {removed / fps:.2f} s of silence removed"


# ---- (9) AutoColor ------------------------------------------------------------------------

def c_auto_color():
    gate()
    name = media_tl("auto")
    pre = f"tl = scratch({name!r})\nitem = video_item(tl)\n"
    try:
        r = rs(pre + f'''need(item, "ApplyAutoColor", "GetAutoColorEnabled", "SetAutoColorEnabled", "ClearAutoColor")
base = shot(tl, 10, {SMALL!r})
s0 = item.GetAutoColorEnabled()
ok = item.ApplyAutoColor()
result = {{"base": base, "s0": s0, "ok": ok, "s1": item.GetAutoColorEnabled(), "graded": shot(tl, 10, {SMALL!r})}}''')
        base, graded = img(r["base"], "base"), img(r["graded"], "auto color")
        # GetAutoColorEnabled: None = no auto color on the item, True/False = present and enabled/disabled
        expect(r["s0"] is None, f"GetAutoColorEnabled on a fresh item -> {r['s0']!r}, want None (no auto color)")
        expect(r["ok"] is True, f"ApplyAutoColor returned {r['ok']!r}")
        expect(r["s1"] is True, f"GetAutoColorEnabled after apply -> {r['s1']!r}")
        r2 = rs(pre + f'''off_ok = item.SetAutoColorEnabled(False)
s2 = item.GetAutoColorEnabled()
off = shot(tl, 10, {SMALL!r})
on_ok = item.SetAutoColorEnabled(True)
clear_ok = item.ClearAutoColor()
result = {{"off_ok": off_ok, "s2": s2, "off": off, "on_ok": on_ok, "clear_ok": clear_ok, "s3": item.GetAutoColorEnabled(),
          "cleared": shot(tl, 10, {SMALL!r})}}''')
        off, cleared = img(r2["off"], "auto color disabled"), img(r2["cleared"], "auto color cleared")
        expect(r2["off_ok"] is True and r2["s2"] is False, f"SetAutoColorEnabled(False) -> {r2['off_ok']!r}, state {r2['s2']!r}")
        expect(r2["on_ok"] is True, f"SetAutoColorEnabled(True) -> {r2['on_ok']!r}")
        expect(r2["clear_ok"] is True and r2["s3"] is None,
               f"ClearAutoColor -> {r2['clear_ok']!r}, state {r2['s3']!r} (want None: False means still present, disabled)")
        states = f"state {r['s0']!r} -> {r['s1']!r} -> {r2['s2']!r} -> {r2['s3']!r}"
        expect(cleared == base, f"after ClearAutoColor frame {sha(cleared)} != base {sha(base)}")
        if graded == base:   # nothing to see: the toggle cannot be judged from the frame
            raise Warn(f"ApplyAutoColor left the frame unchanged ({sha(base)}); cleared == base; {states}")
        expect(off == base, f"auto color disabled: frame {sha(off)} != base {sha(base)} (graded {sha(graded)}); {states}")
        return f"frame {sha(base)} -> {sha(graded)} -> disabled/cleared back to base; {states}"
    finally:
        try:
            rs(pre + "result = item.ClearAutoColor()")
        except Exception:
            pass


# ---- (10) CDL -----------------------------------------------------------------------------

def cdl_matches(got, want):
    bad = []
    for k in ("Slope", "Offset", "Power"):
        g, w = triple(got.get(k)), triple(want[k])
        if g is None or any(abs(x - y) > 0.01 for x, y in zip(g, w)):
            bad.append(f"{k} {got.get(k)!r}")
    if not close(got.get("Saturation"), want["Saturation"], 0.01):
        bad.append(f"Saturation {got.get('Saturation')!r}")
    return bad


def c_cdl():
    gate()
    name = media_tl("cdl")
    r = rs(f'''tl = scratch({name!r})
item = video_item(tl)
need(item, "GetCDL")
c0 = plain(item.GetCDL(1))
base = shot(tl, 10, {SMALL!r})
cdl = dict({TEST_CDL!r})
cdl["NodeIndex"] = 1
ok = item.SetCDL(cdl)
if not ok:
    cdl["NodeIndex"] = "1"
    ok = item.SetCDL(cdl)
result = {{"c0": c0, "ok": ok, "c1": plain(item.GetCDL(1)), "base": base, "graded": shot(tl, 10, {SMALL!r})}}''')
    c0, c1 = r["c0"], r["c1"]
    expect(isinstance(c0, dict), f"GetCDL(1) -> {c0!r}")
    missing = [k for k in ("Slope", "Offset", "Power", "Saturation") if k not in c0]
    expect(not missing, f"GetCDL(1) lacks {missing}: {c0}")
    expect(all(triple(c0[k]) for k in ("Slope", "Offset", "Power")), f"GetCDL(1) values not 3 numbers each: {c0}")
    expect(r["ok"] is True, f"SetCDL returned {r['ok']!r}")
    base, graded = img(r["base"], "base"), img(r["graded"], "after SetCDL")
    expect(graded != base, "SetCDL did not change the frame")
    identity = not cdl_matches(c0, {"Slope": "1 1 1", "Offset": "0 0 0", "Power": "1 1 1", "Saturation": 1})
    bad = cdl_matches(c1 if isinstance(c1, dict) else {}, TEST_CDL)
    expect(not bad, f"GetCDL after SetCDL differs from what was set: {', '.join(bad)} (fresh {c0})")
    if not identity:
        raise Warn(f"fresh item CDL is not identity: {c0}")
    return f"fresh {c0}; SetCDL changed the frame and reads back"


# ---- (11) CopyAttributes ------------------------------------------------------------------

def c_copy_attributes():
    gate()
    # Two instances of the real clip: bench_tl's Solid Color generators have no transform properties
    # (GetProperty() is {} and SetProperty returns False on them).
    name = clip_timeline("copy", rlib.media_clip(), copies=2)
    r = rs(f'''tl = scratch({name!r})
src, dst = video_item(tl, 0), video_item(tl, 1)
need(src, "CopyAttributes")
keys = ["ZoomX", "ZoomY", "Pan", "Opacity"]
d0 = {{k: dst.GetProperty(k) for k in keys}}
sets = [src.SetProperty("ZoomX", 1.5), src.SetProperty("ZoomY", 1.5), src.SetProperty("Pan", 120.0), src.SetProperty("Opacity", 40.0)]
s1 = {{k: src.GetProperty(k) for k in keys}}
ok = src.CopyAttributes([dst], {{"allZoomAttributes": True, "position": True}})
result = {{"d0": plain(d0), "sets": sets, "s1": plain(s1), "ok": ok, "d1": plain({{k: dst.GetProperty(k) for k in keys}})}}''')
    s1, d0, d1 = r["s1"], r["d0"], r["d1"]
    expect(all(x is True for x in r["sets"]) and close(s1["ZoomX"], 1.5) and close(s1["Opacity"], 40.0),
           f"setting up the source failed: SetProperty {r['sets']}, source now {s1}")
    expect(not close(d0["ZoomX"], 1.5), f"target already has ZoomX {d0['ZoomX']}")
    expect(r["ok"] is True, f"CopyAttributes returned {r['ok']!r}")
    expect(close(d1["ZoomX"], 1.5) and close(d1["ZoomY"], 1.5), f"zoom not copied: target {d0} -> {d1}")
    expect(close(d1["Opacity"], d0["Opacity"]), f"Opacity copied although not selected: {d0['Opacity']} -> {d1['Opacity']}")
    expect(close(d1["Pan"], 120.0), f"zoom copied but 'position' (selected) did not copy Pan: target {d0} -> {d1}")
    return f"target {d0} -> {d1} (opacity left alone)"


# ---- (12) Graph node label / color / enabled ----------------------------------------------

def c_graph_nodes():
    gate()
    name = media_tl("graph")
    pre = f"tl = scratch({name!r})\nitem = video_item(tl)\ng = item.GetNodeGraph()\nif g is None:\n    raise RuntimeError('GetNodeGraph returned None')\n"
    r = rs(pre + f'''need(g, "SetNodeLabel", "GetNodeLabel", "SetNodeColor", "GetNodeColor", "GetNodeEnabled", "SetNodeEnabled")
lab0 = g.GetNodeLabel(1)
okl = g.SetNodeLabel(1, {NODE_LABEL!r})
lab1 = g.GetNodeLabel(1)
col0 = g.GetNodeColor(1)
okc = g.SetNodeColor(1, "Teal")
col1 = g.GetNodeColor(1)
okc2 = g.SetNodeColor(1, "")
col2 = g.GetNodeColor(1)
g.SetNodeLabel(1, lab0 or "")
result = {{"n": g.GetNumNodes(), "lab0": lab0, "okl": okl, "lab1": lab1, "col0": col0, "okc": okc, "col1": col1,
          "okc2": okc2, "col2": col2, "en0": g.GetNodeEnabled(1)}}''')
    expect((r["n"] or 0) >= 1, f"GetNumNodes -> {r['n']!r}")
    expect(r["okl"] is True and r["lab1"] == NODE_LABEL, f"SetNodeLabel -> {r['okl']!r}, GetNodeLabel {r['lab1']!r}")
    expect(r["okc"] is True and r["col1"] == "Teal", f"SetNodeColor('Teal') -> {r['okc']!r}, GetNodeColor {r['col1']!r}")
    expect(r["okc2"] is True and r["col2"] == "", f"SetNodeColor('') -> {r['okc2']!r}, GetNodeColor {r['col2']!r}")
    expect(r["en0"] is True, f"GetNodeEnabled(1) on a fresh node -> {r['en0']!r} (want bool True)")
    r2 = rs(pre + f'''plainshot = shot(tl, 10, {SMALL!r})
cdl = dict({TEST_CDL!r})
cdl["NodeIndex"] = 1
cdl_ok = item.SetCDL(cdl)
if not cdl_ok:
    cdl["NodeIndex"] = "1"
    cdl_ok = item.SetCDL(cdl)
graded = shot(tl, 10, {SMALL!r})
okd = g.SetNodeEnabled(1, False)
en1 = g.GetNodeEnabled(1)
off = shot(tl, 10, {SMALL!r})
oke = g.SetNodeEnabled(1, True)
result = {{"plain": plainshot, "graded": graded, "cdl_ok": cdl_ok, "okd": okd, "en1": en1, "off": off, "oke": oke,
          "en2": g.GetNodeEnabled(1), "on": shot(tl, 10, {SMALL!r})}}''')
    expect(r2["cdl_ok"] is True, f"SetCDL on node 1 returned {r2['cdl_ok']!r}: the disable test needs a graded node")
    expect(r2["okd"] is True and r2["en1"] is False, f"SetNodeEnabled(1, False) -> {r2['okd']!r}, GetNodeEnabled {r2['en1']!r}")
    expect(r2["oke"] is True and r2["en2"] is True, f"SetNodeEnabled(1, True) -> {r2['oke']!r}, GetNodeEnabled {r2['en2']!r}")
    p, g_, off, on = (img(r2[k], k) for k in ("plain", "graded", "off", "on"))
    detail = f"{r['n']} node(s); label, color round trip; enabled True -> False -> True"
    if g_ == p:
        raise Warn(detail + "; SetCDL on node 1 left the frame unchanged, so the disable could not be seen")
    expect(off == p, f"node 1 disabled: frame {sha(off)} != ungraded {sha(p)}")
    expect(on == g_, f"node 1 re-enabled: frame {sha(on)} != graded {sha(g_)}")
    return detail + "; disabling the graded node restores the ungraded frame"


# ---- (13) Timeline enable -----------------------------------------------------------------

def c_timeline_enabled():
    gate()
    name = dup_timeline("misc")
    r = rs(f'''tl = scratch({name!r})
need(tl, "GetIsEnabled", "SetIsEnabled")
e0 = tl.GetIsEnabled()
ok1 = tl.SetIsEnabled(not e0)
e1 = tl.GetIsEnabled()
ok2 = tl.SetIsEnabled(e0)
result = {{"e0": e0, "ok1": ok1, "e1": e1, "ok2": ok2, "e2": tl.GetIsEnabled()}}''')
    expect(isinstance(r["e0"], bool), f"GetIsEnabled -> {r['e0']!r}")
    expect(r["ok1"] is True and r["e1"] is (not r["e0"]), f"SetIsEnabled({not r['e0']}) -> {r['ok1']!r}, reads {r['e1']!r}")
    expect(r["ok2"] is True and r["e2"] is r["e0"], f"SetIsEnabled({r['e0']}) -> {r['ok2']!r}, reads {r['e2']!r}")
    return f"{r['e0']} -> {r['e1']} -> {r['e2']}"


# ---- (14) read-only getters ---------------------------------------------------------------

def c_extras_packages():
    gate()
    r = rs('need(resolve, "GetExtrasPackages")\npk = resolve.GetExtrasPackages()\n'
           'result = {"list": isinstance(pk, list), "raw": repr(pk)[:80], "pk": plain(pk) if isinstance(pk, list) else None}')
    expect(r["list"], f"GetExtrasPackages -> {r['raw']}")
    if not r["pk"]:
        product = run_script(Ctx.v, "result = resolve.GetProductName()")
        if "studio" in str(product).lower():
            raise Warn(f"GetExtrasPackages returned an empty list on {product} (it normally lists the download manager's packages)")
        return f"0 packages ({product})"
    states = ("installed", "downloading", "installing", "notInstalled")
    bad = [p for p in r["pk"] if not (isinstance(p, dict) and p.get("name") and p.get("status") in states)]
    expect(not bad, f"{len(bad)} entries without name / known status, e.g. {bad[:1]}")
    summary = ", ".join(f"{p['name']}={p['status']}" for p in r["pk"][:6])
    return f"{len(r['pk'])} packages: {summary}" + (" ..." if len(r["pk"]) > 6 else "")


def c_status_getters():
    # These getters already answered on 21.1 at runtime: capability only, no version gate. A getter is absent
    # only when the object has no such attribute; one that exists and returns None is broken.
    r = rs(f'''
out = {{}}
for n in {list(STATUS_GETTERS)!r}:
    f = getattr(proj, n, None)
    if f is None:
        out[n] = "ABSENT"
        continue
    v = f()
    out[n] = plain(v) if isinstance(v, dict) else ("RETURNED None" if v is None else "NOT A DICT: " + repr(v)[:60])
result = out''')
    absent = [n for n, v in r.items() if v == "ABSENT"]
    if len(absent) == len(STATUS_GETTERS):
        raise Skip("none of the Project status getters exist here")
    bad = {n: v for n, v in r.items() if isinstance(v, str) and v != "ABSENT"}
    expect(not bad, f"getters that exist but give no status dict: {bad}")
    nojob = [n for n, v in r.items() if isinstance(v, dict) and not v.get("JobStatus")]
    expect(not nojob, f"status without JobStatus: {({n: r[n] for n in nojob})}")
    jobs = {n: v.get("JobStatus") for n, v in r.items() if isinstance(v, dict)}
    detail = ", ".join(f"{n[3:]}={j}" for n, j in jobs.items()) + (f"; absent {absent}" if absent else "")
    if absent:
        raise Warn(detail)
    return detail


def main():
    rlib.main_guard()
    suite = rlib.Suite("t40_api212")
    with rlib.vendor() as v:
        Ctx.v = v
        try:
            suite.check("GetEffectsList video/audio", c_effects_list)
            suite.check("Effect round trip (add/disable/enable/delete)", c_effect_roundtrip)
            suite.check("ExportFrame maxDimension", c_export_maxdim)
            suite.check("ExportFrame jpg quality", c_export_jpg)
            suite.check("ExportFrame to file", c_export_file)
            suite.check("ExportFrame roi", c_export_roi)
            suite.check("Timeline.ExportThumbnails", c_timeline_thumbnails)
            suite.check("MediaPoolItem.ExportFrame/ExportThumbnails", c_mediapoolitem_export)
            suite.check("TimelineItem.ExportFrame/ExportThumbnails", c_timelineitem_export)
            suite.check("GetPosition/SetPosition/DeleteGaps", c_position_gaps)
            suite.check("Timeline.MoveItems", c_move_items)
            suite.check("SetLeftOffset/SetRightOffset trim", c_trim_offsets)
            suite.check("RippleDeleteSilence", c_ripple_delete_silence)
            suite.check("AutoColor apply/disable/clear", c_auto_color)
            suite.check("GetCDL / SetCDL", c_cdl)
            suite.check("CopyAttributes", c_copy_attributes)
            suite.check("Graph node label/color/enabled", c_graph_nodes)
            suite.check("Timeline GetIsEnabled/SetIsEnabled", c_timeline_enabled)
            suite.check("GetExtrasPackages", c_extras_packages)
            suite.check("Project status getters", c_status_getters)
        finally:
            def cleanup():
                FRAME_FILE.unlink(missing_ok=True)
                gone = rlib.drop_scratch(v)
                expect(gone.get("ok"), f"drop_scratch: {gone}")
                clips = rlib.drop_scratch_clips(v)
                expect(clips.get("ok"), f"drop_scratch_clips: {clips}")
                return f"dropped {gone.get('deleted')} scratch timeline(s) and {len(clips['deleted'])} scratch clip(s)"
            suite.check("cleanup", cleanup)
    raise SystemExit(suite.finish())


if __name__ == "__main__":
    main()
