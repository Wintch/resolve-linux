#!/usr/bin/env python3
"""Live checks of the vendor ResolveMCP server (the one shipped in /opt/resolve/bin).

Covers status, the scripting API/doc tools, rendered frames (get_image, get_thumbnails),
describe_grade, the LUT and DCTL file tools, the Extras package listing (read-only) and the
run_script sandbox boundary. Every tool-specific check skips when the running server does not
list the tool, so the module runs against older and newer Resolve releases alike.

Touches only the MCP-Benchmark fixture project: one scratch timeline (zz_reg_media, built from
the synthetic clip in rlib.SCRATCH, which is imported into the Media Pool for the run), a zz_reg_ .drx
in rlib.SCRATCH and zz_reg_ files in the vendor's LUT directory, all removed again at the end. It never
installs or uninstalls Extras packages.
"""
import json
import re
import struct
from pathlib import Path

import rlib
from rlib import FIXTURE_PRELUDE, PNG_MAGIC, SCRATCH_PREFIX, Skip, Warn, expect, sha

MEDIA_TL = SCRATCH_PREFIX + "media"
TEST_LUT = SCRATCH_PREFIX + "test.cube"
TEST_DCTL = SCRATCH_PREFIX + "pass.dctl"
BAD_DCTL = SCRATCH_PREFIX + "bad.dctl"
LUT_SIZE = 9
DRX_FILE = rlib.SCRATCH / (SCRATCH_PREFIX + "t30_timeline_grade.drx")
# Where the vendor server writes its files on Linux when list_luts does not say (luts_dir); only a fallback
# for the clean-up of the exact zz_reg_ names above.
DEFAULT_LUT_DIR = Path.home() / ".local" / "share" / "DaVinciResolve" / "LUT"

PASS_DCTL = """\
__DEVICE__ float3 transform(int p_Width, int p_Height, int p_X, int p_Y, float p_R, float p_G, float p_B)
{
    return make_float3(p_R, p_G, p_B);
}
"""
# The same pass-through returning a float3 local. write_dctl validates with Resolve's own
# Resolve.ValidateDCTL, which refuses this form ("main DCTL function's return value must be float3"),
# so it is checked separately and only as a WARN.
LOCAL_DCTL = SCRATCH_PREFIX + "local.dctl"
PASS_DCTL_LOCAL = """\
__DEVICE__ float3 transform(int p_Width, int p_Height, int p_X, int p_Y, float p_R, float p_G, float p_B)
{
    float3 o = make_float3(p_R, p_G, p_B);
    return o;
}
"""
# Words that hint at the machine (no GPU compiler, driver trouble) rather than at the tool: only added to
# a FAIL's detail, never a reason to skip on the reference machine.
ENV_COMPILE_ERROR = re.compile(r"\b(cuda|nvrtc|opencl|metal|driver|no gpu|gpu (is )?not)\b", re.I)
# What the run_script sandbox says when it refuses something (import hook, file audit hook, removed builtin).
SANDBOX_REFUSAL = re.compile(r"PermissionError: (import|filesystem access) not allowed|NameError: name '\w+' is not defined")


# ---------------------------------------------------------------- helpers

def text_of(data):
    """The longest text a tool answered with (a plain text answer, or the longest string field)."""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        strings = [x for x in data.values() if isinstance(x, str)]
        if strings:
            return max(strings, key=len)
    return json.dumps(data)


def squash(s):
    return "".join(s.split())


def image_size(data: bytes):
    """-> (format, width, height) from a PNG IHDR or a JPEG SOFn header."""
    if data.startswith(PNG_MAGIC):
        w, h = struct.unpack(">II", data[16:24])
        return "png", w, h
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker == 0xFF:
                i += 1
                continue
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seglen = struct.unpack(">H", data[i + 2:i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return "jpeg", w, h
            i += 2 + seglen
    raise ValueError(f"neither PNG nor JPEG (starts {data[:8]!r})")


def find_key(obj, pred):
    """First (key, value) in a nested dict/list whose key satisfies pred, depth-first."""
    if isinstance(obj, dict):
        for k, val in obj.items():
            if pred(k, val):
                return k, val
        obj = list(obj.values())
    if isinstance(obj, list):
        for val in obj:
            hit = find_key(val, pred)
            if hit:
                return hit
    return None


def tc_to_frames(tc, fps):
    hh, mm, ss, ff = (int(x) for x in re.split(r"[:;]", tc))
    return ((hh * 60 + mm) * 60 + ss) * fps + ff


def frames_to_tc(n, fps):
    ff, s = n % fps, n // fps
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{ff:02d}"


def main():
    rlib.main_guard()
    suite = rlib.Suite("t30_vendor")
    with rlib.vendor() as v:
        catalog = {t["name"]: t for t in v.rpc("tools/list")["result"]["tools"]}
        delete_tool = next((t for t in ("delete_lut_file", "delete_lut") if t in catalog), None)
        state = {}

        def need(*names):
            missing = [n for n in names if n not in catalog]
            if missing:
                raise Skip(f"server has no {', '.join(missing)}")

        def call_ok(name, args=None, timeout=None):
            is_error, data, images = v.call(name, args or {}, timeout=timeout)
            expect(not is_error, f"{name} failed: {str(data)[:300]}")
            return data, images

        def lut_listing():
            """-> (luts_dir or None, [relative file paths])."""
            data, _ = call_ok("list_luts")
            expect(isinstance(data, dict) and isinstance(data.get("files"), list), f"list_luts: no files list in {str(data)[:200]}")
            return data.get("luts_dir"), [str(f) for f in data["files"]]

        def listed(name):
            """Relative paths list_luts gives for files called `name` (exact file name, any sub-folder)."""
            return [f for f in lut_listing()[1] if f.rsplit("/", 1)[-1] == name]

        def version():
            if "version" not in state:
                ver = rlib.run_script(v, "result = resolve.GetVersion()")
                state["version"] = (int(ver[0]), int(ver[1]))
            return state["version"]

        # ------------------------------------------------------------ status and docs

        def resolve_status():
            need("get_resolve_status")
            data, _ = call_ok("get_resolve_status")
            if isinstance(data, dict):
                hit = find_key(data, lambda k, val: k in ("running", "is_running", "reachable", "connected"))
                if hit:
                    expect(hit[1] is True, f"{hit[0]}={hit[1]!r}: {data}")
                    running = "%d.%d" % version()
                    if "version" in data:
                        expect(re.match(rf"{re.escape(running)}(\b|\.|$)", str(data["version"])),
                               f"status says version {data['version']!r}, Resolve is {running}")
                    return f"{hit[0]}=True, version {data.get('version')!r} (Resolve {running})"
                text = json.dumps(data).lower()
            else:
                text = str(data).lower()
            expect("running" in text and "not running" not in text and "isn't running" not in text,
                   f"status does not say running: {text[:200]}")
            return text[:120]

        def search_api():
            need("search_scripting_api")
            is_error, data, _ = v.call("search_scripting_api", {"pattern": "AddEffect"})
            text = data if isinstance(data, str) else json.dumps(data)
            hits = len(re.findall(r"\bdef AddEffect\(|\bAddEffect\(", text))
            if is_error and "Nothing in the scripting API matches" not in text:
                raise AssertionError(f"search_scripting_api failed: {text[:300]}")
            if not hits:
                try:
                    in_stub = "TimelineItem.AddEffect" in json.loads((rlib.BASELINES / "stub_methods.json").read_text())["methods"]
                except (OSError, ValueError, KeyError):
                    in_stub = False
                msg = f"search for AddEffect found nothing: {text[:200]}"
                if in_stub:   # the accepted stub has it, so the search tool is broken
                    raise AssertionError(msg + " (TimelineItem.AddEffect is in baselines/stub_methods.json)")
                raise Warn(msg)
            return f"{hits} AddEffect( declarations found"

        def scripting_api():
            """The served stub is the installed one (same declared methods), and the tool names the running version."""
            need("get_scripting_api")
            data, _ = call_ok("get_scripting_api", {"api": "DaVinciResolveScript.pyi"})
            stub = text_of(data)
            for must in ("class Resolve", "class Timeline", "def GetVersion"):
                expect(must in stub, f"stub lacks {must!r} ({len(stub)} chars)")
            served = set(re.findall(r"\bdef (\w+)\(", stub))
            installed_path = Path("/opt/resolve/Developer/Scripting/DaVinciResolveScript.pyi")
            same = "installed stub not found"
            if installed_path.exists():
                installed = set(re.findall(r"\bdef (\w+)\(", installed_path.read_text(errors="replace")))
                expect(served == installed, f"served stub differs from the installed one: missing {sorted(installed - served)[:10]}, "
                                            f"extra {sorted(served - installed)[:10]}")
                same = f"same {len(served)} method names as the installed stub"
            running = "%d.%d" % version()
            desc = catalog["get_scripting_api"].get("description", "")
            advertised = re.search(r"version (\d+\.\d+)\b", desc)
            if advertised and advertised.group(1) == running:
                return f"{len(stub)} chars, {same}; the tool names version {running}"
            raise Warn(f"{same}; the tool names version {advertised.group(1) if advertised else 'none'}, Resolve is {running}")

        def whats_new():
            need("get_whats_new")
            major, _ = version()
            data, _ = call_ok("get_whats_new", {"since": f"{major - 1}.0"})
            text = data if isinstance(data, str) else json.dumps(data)
            expect(len(text) >= 200, f"only {len(text)} chars: {text[:200]}")
            if isinstance(data, dict) and isinstance(data.get("entries"), list):
                versions = [str(e.get("version")) for e in data["entries"] if isinstance(e, dict)]
                expect(any(x.startswith(f"{major}.") for x in versions),
                       f"no changelog entry for {major}.x since {major - 1}.0: versions {versions[:10]}")
                return f"{len(versions)} entries since {major - 1}.0, newest {versions[-1] if versions else None}"
            expect(re.search(rf"\b{major}\.\d", text), f"changelog since {major - 1}.0 has no {major}.x version")
            return f"{len(text)} chars since {major - 1}.0"

        def scripting_docs():
            need("get_scripting_docs")
            data, _ = call_ok("get_scripting_docs", {"document": "README.md", "section": "TOC"})
            toc = text_of(data)
            lines = [ln for ln in toc.splitlines() if ln.strip()]
            expect(len(lines) >= 5, f"README TOC has {len(lines)} lines: {toc[:200]}")
            data, _ = call_ok("get_scripting_docs", {"document": "DCTLReadme.txt"})
            dctl = text_of(data)
            expect("__DEVICE__" in dctl and "transform" in dctl, f"DCTL readme lacks the transform signature ({len(dctl)} chars)")
            return f"README TOC {len(lines)} lines, DCTL readme {len(dctl)} chars"

        # ------------------------------------------------------------ rendered frames

        def media_timeline():
            clip = str(rlib.media_clip())   # imported for this run, removed by the clean-up (drop_scratch_clips)
            r = rlib.run_script(v, FIXTURE_PRELUDE + f"""
path = {clip!r}
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
how = "reused"
if clip is None:
    got, how = mp.ImportMedia([path]) or [], "ImportMedia"
    if not got:
        got, how = mp.ImportMedia([{{"FilePath": path}}]) or [], "ImportMedia(FilePath)"
    if not got:
        got, how = resolve.GetMediaStorage().AddItemListToMediaPool([path]) or [], "AddItemListToMediaPool"
    clip = got[0] if got else find(mp.GetRootFolder())
if clip is None:
    raise RuntimeError("could not import the test clip")
old = timeline({MEDIA_TL!r})
if old is not None:
    mp.DeleteTimelines([old])
tl = mp.CreateTimelineFromClips({MEDIA_TL!r}, [clip])
if tl is None:
    raise RuntimeError("CreateTimelineFromClips failed")
proj.SetCurrentTimeline(tl)
result = {{"how": how, "current": proj.GetCurrentTimeline().GetName(), "start_tc": tl.GetStartTimecode(),
          "frames": tl.GetEndFrame() - tl.GetStartFrame(), "fps": tl.GetSetting("timelineFrameRate"),
          "w": tl.GetSetting("timelineResolutionWidth"), "h": tl.GetSetting("timelineResolutionHeight"),
          "items": len(tl.GetItemListInTrack("video", 1) or [])}}
""")
            expect(r["current"] == MEDIA_TL, f"current timeline is {r['current']!r}")
            expect(r["items"] == 1, f"{r['items']} items on V1")
            expect(r["frames"] >= 40, f"timeline only {r['frames']} frames long")
            state["media"] = r
            return f"{r['how']}, {r['frames']} frames at {r['fps']} fps, {r['w']}x{r['h']}, starts {r['start_tc']}"

        def media():
            need("get_image")
            if "media" not in state:
                raise Skip("scratch media timeline was not built")
            return state["media"]

        def image(**args):
            is_error, data, images = v.call("get_image", {"source": "timeline", **args}, timeout=180)
            expect(len(images) == 1, f"get_image {args}: {len(images)} images, error={is_error}: {str(data)[:300]}")
            return images[0]

        def image_repeatable():
            m = media()
            if ";" in m["start_tc"]:
                raise Skip("drop-frame timeline: timecode arithmetic not implemented")
            fps = round(float(m["fps"]))
            t0 = tc_to_frames(m["start_tc"], fps)
            state["tc0"], state["tc20"] = frames_to_tc(t0, fps), frames_to_tc(t0 + 20, fps)
            mime_a, a = image(timecode=state["tc0"])
            mime_b, b = image(timecode=state["tc0"])
            state["frame0"] = (mime_a, a)
            expect(a == b, f"same timecode {state['tc0']}, different bytes: {sha(a)} vs {sha(b)}")
            return f"{state['tc0']} twice -> {sha(a)} ({len(a)} bytes)"

        def image_follows_timecode():
            media()
            if "frame0" not in state:
                raise Skip("no reference frame")
            _, a = state["frame0"]
            _, c = image(timecode=state["tc20"])
            expect(c != a, f"{state['tc20']} rendered the same bytes as {state['tc0']}")
            _, d = image(frame_offset=20)
            expect(d == c, f"frame_offset 20 ({sha(d)}) differs from timecode {state['tc20']} ({sha(c)})")
            return f"{state['tc20']} -> {sha(c)}, frame_offset 20 matches"

        def image_format():
            m = media()
            if "frame0" not in state:
                raise Skip("no reference frame")
            mime, data = state["frame0"]
            expect(mime in ("image/jpeg", "image/png"), f"mime {mime!r}")
            fmt, w, h = image_size(data)
            expect(mime == f"image/{fmt}", f"mime {mime} but the bytes are {fmt}")
            expect(0 < max(w, h) <= 1024, f"{w}x{h} exceeds the 1024 px default")
            tw, th = int(m["w"]), int(m["h"])
            expect(abs(w / h - tw / th) < 0.02, f"{w}x{h} does not keep the {tw}x{th} aspect")
            state["frame0_size"] = (w, h)
            return f"{mime} {w}x{h}"

        def thumbnails():
            media()
            need("get_thumbnails")
            if "frame0_size" not in state:
                raise Skip("no reference frame size")
            is_error, data, images = v.call("get_thumbnails", {"source": "timeline", "count": 4}, timeout=180)
            expect(len(images) == 1, f"{len(images)} images, error={is_error}: {str(data)[:300]}")
            mime, sheet = images[0]
            fmt, w, h = image_size(sheet)
            fw, fh = state["frame0_size"]
            expect(w * h > fw * fh, f"sheet {w}x{h} not larger than one frame {fw}x{fh}")
            expect(sheet != state["frame0"][1], "sheet is the single frame")
            return f"one {fmt} sheet {w}x{h} > frame {fw}x{fh}"

        def node_list(data, what):
            expect(isinstance(data, dict), f"{what}: not JSON: {str(data)[:200]}")
            cm = find_key(data, lambda k, val: k == "color_management")
            expect(cm and cm[1], f"{what}: no color_management in {list(data)}")
            nodes = find_key(data, lambda k, val: "node" in k.lower() and isinstance(val, list))
            expect(nodes is not None, f"{what}: no node list in {list(data)}")
            return nodes[1]

        def grade_timeline():
            need("describe_grade")
            media()
            # Give the timeline graph a node first (a .drx with a timeline grade: one node, colour Blue), so the
            # node listing is really compared. The graph object is stale after the apply: fetch it again.
            rlib.write_drx(DRX_FILE, "present")
            r = rlib.run_script(v, FIXTURE_PRELUDE + f"""
t = timeline({MEDIA_TL!r})
g = t.GetNodeGraph()
applied = None if g is None else bool(g.ApplyGradeFromDRX({str(DRX_FILE)!r}, 0))
g = t.GetNodeGraph()
n = None if g is None else int(g.GetNumNodes() or 0)
result = {{"applied": applied, "n": n, "colors": [g.GetNodeColor(i) for i in range(1, (n or 0) + 1)] if g else []}}
""")
            expect(r["n"] is not None, "Timeline.GetNodeGraph() returned None")
            data, _ = call_ok("describe_grade", {"source": "timeline"}, timeout=120)
            nodes = node_list(data, "timeline")
            expect(data.get("source") == "timeline" and data.get("name") == MEDIA_TL,
                   f"describe_grade describes {data.get('source')!r} {data.get('name')!r}, not timeline {MEDIA_TL}")
            expect(len(nodes) == r["n"], f"describe_grade lists {len(nodes)} timeline nodes, Resolve has {r['n']}")
            if r["n"]:
                colors = [x.get("color") or "" if isinstance(x, dict) else None for x in nodes]
                expect(colors == r["colors"], f"node colours {colors} != Resolve's {r['colors']}")
                return (f"{MEDIA_TL}: {r['n']} timeline node(s) (seeded from a .drx) with Resolve's colours {r['colors']}; "
                        "color_management present")
            return (f"{MEDIA_TL}: 0 timeline nodes, as Resolve reports (node listing not exercised: seeding the timeline "
                    f"graph with ApplyGradeFromDRX returned {r['applied']!r}); color_management present")

        def grade_item():
            need("describe_grade")
            m = media()
            if "tc0" not in state:
                raise Skip("no start timecode")
            label = SCRATCH_PREFIX + "dg"
            ok = rlib.run_script(v, FIXTURE_PRELUDE + f"""
it = timeline({MEDIA_TL!r}).GetItemListInTrack("video", 1)[0]
result = [bool(it.GetNodeGraph().SetNodeLabel(1, {label!r})),
          bool(it.SetCDL({{"NodeIndex": 1, "Slope": "0.5 0.4 0.2", "Offset": "0 0 0", "Power": "1 1 1", "Saturation": 1.0}}))]
""")
            expect(ok == [True, True], f"setting up the item grade failed: SetNodeLabel, SetCDL -> {ok}")
            data, _ = call_ok("describe_grade", {"source": "timeline_item", "timecode": state["tc0"]}, timeout=120)
            nodes = node_list(data, "timeline_item")
            expect(nodes and isinstance(nodes[0], dict), f"no node 1 in {str(data)[:200]}")
            n1 = nodes[0]
            expect(n1.get("label") == label, f"node 1 label {n1.get('label')!r}, set {label!r}")
            expect(n1.get("enabled") is True, f"node 1 enabled {n1.get('enabled')!r}")
            slope = [float(x) for x in str((n1.get("cdl") or {}).get("Slope", "")).split()]
            expect(len(slope) == 3 and all(abs(a - b) < 0.01 for a, b in zip(slope, (0.5, 0.4, 0.2))),
                   f"node 1 CDL {n1.get('cdl')!r}, set Slope 0.5 0.4 0.2")
            expect(n1.get("tools"), f"graded node 1 lists no tools: {n1}")
            return f"node 1: label, enabled, CDL slope {slope}, tools {n1.get('tools')}"

        # ------------------------------------------------------------ LUT / DCTL files

        def lut_roundtrip():
            need("generate_lut", "list_luts")
            if delete_tool is None:
                raise Skip("server has neither delete_lut_file nor delete_lut")
            expect(not listed(TEST_LUT), f"{TEST_LUT} is already listed before generate_lut (left by an earlier run?): "
                                         "the clean-up removes it; rerun")
            call_ok("generate_lut", {"path": TEST_LUT, "size": LUT_SIZE, "transform": "return (r, g, b)"}, timeout=120)
            luts_dir, _ = lut_listing()
            hits = listed(TEST_LUT)
            expect(len(hits) == 1, f"{TEST_LUT} listed {len(hits)} times after generate_lut: {hits}")
            expect(luts_dir, "list_luts gives no luts_dir: the generated file cannot be inspected")
            f = Path(luts_dir) / hits[0]
            expect(f.exists(), f"listed as {hits[0]} but {f} does not exist")
            text = f.read_text()
            expect(re.search(rf"^LUT_3D_SIZE\s+{LUT_SIZE}\s*$", text, re.M), "no LUT_3D_SIZE 9 line")
            rows = [tuple(float(x) for x in ln.split()) for ln in text.splitlines()
                    if re.fullmatch(r"\s*[-+0-9.eE]+\s+[-+0-9.eE]+\s+[-+0-9.eE]+\s*", ln)]
            expect(len(rows) == LUT_SIZE ** 3, f"{len(rows)} lattice rows, expected {LUT_SIZE ** 3}")
            n = LUT_SIZE - 1
            dev = max(max(abs(r - i % LUT_SIZE / n), abs(g - i // LUT_SIZE % LUT_SIZE / n), abs(b - i // LUT_SIZE ** 2 / n))
                      for i, (r, g, b) in enumerate(rows))
            expect(dev < 1e-3, f"identity transform deviates by {dev:.4g}")
            call_ok(delete_tool, {"path": TEST_LUT})
            expect(not listed(TEST_LUT) and not f.exists(), f"{TEST_LUT} still there after {delete_tool}")
            return f"generated {hits[0]}: {len(rows)} rows, identity within {dev:.1g}; deleted with {delete_tool}"

        def dctl_roundtrip():
            need("write_dctl", "read_dctl", "list_luts")
            if delete_tool is None:
                raise Skip("server has neither delete_lut_file nor delete_lut")
            is_error, data, _ = v.call("write_dctl", {"path": TEST_DCTL, "content": PASS_DCTL}, timeout=120)
            if is_error:
                msg = str(data)[:300]
                hint = " (the message points at the GPU/driver: check the machine too)" if ENV_COMPILE_ERROR.search(msg) else ""
                raise AssertionError(f"write_dctl refused a pass-through DCTL: {msg}{hint}")
            expect(listed(TEST_DCTL), f"{TEST_DCTL} not listed after write_dctl")
            got = None
            for path in (f"MCP/{TEST_DCTL}", TEST_DCTL):
                is_error, data, _ = v.call("read_dctl", {"path": path})
                if not is_error:
                    got = (path, text_of(data))
                    break
            expect(got is not None, f"read_dctl found neither MCP/{TEST_DCTL} nor {TEST_DCTL}: {str(data)[:200]}")
            expect(squash(PASS_DCTL) in squash(got[1]), f"read_dctl {got[0]} returned other source: {got[1][:200]}")
            call_ok(delete_tool, {"path": TEST_DCTL})
            expect(not listed(TEST_DCTL), f"{TEST_DCTL} still listed after {delete_tool}")
            return f"written, read back as {got[0]}, deleted with {delete_tool}"

        def dctl_local_return():
            need("write_dctl", "list_luts")
            if delete_tool is None:
                raise Skip("server has neither delete_lut_file nor delete_lut")
            is_error, data, _ = v.call("write_dctl", {"path": LOCAL_DCTL, "content": PASS_DCTL_LOCAL}, timeout=120)
            if is_error:
                msg = str(data)[:300]
                raise Warn("write_dctl (Resolve.ValidateDCTL) refuses a pass-through that returns a float3 local "
                           f"(`float3 o = make_float3(...); return o;`): {msg}")
            expect(listed(LOCAL_DCTL), f"{LOCAL_DCTL} not listed after write_dctl")
            call_ok(delete_tool, {"path": LOCAL_DCTL})
            expect(not listed(LOCAL_DCTL), f"{LOCAL_DCTL} still listed after {delete_tool}")
            return "a float3 local as the return value is accepted"

        def dctl_rejects_garbage():
            need("write_dctl", "list_luts")
            is_error, data, _ = v.call("write_dctl", {"path": BAD_DCTL, "content": "this is not DCTL {"}, timeout=120)
            expect(is_error, f"write_dctl accepted source that cannot compile, so it does not validate and the DCTL "
                             f"round trips above prove nothing: {str(data)[:200]}")
            expect(not listed(BAD_DCTL), f"rejected, but {BAD_DCTL} was left in the LUT directory")
            return f"refused: {str(data)[:100]}"

        # ------------------------------------------------------------ extras, sandbox

        def extras():
            need("list_extras_packages")
            is_error, data, _ = v.call("list_extras_packages", {}, timeout=120)
            if is_error:
                if "Extras" in str(data) and re.search(r"does not (expose|support|have)|not available", str(data)):
                    raise Skip(f"this Resolve has no Extras packages API: {str(data)[:160]}")
                raise AssertionError(f"list_extras_packages failed: {str(data)[:300]}")
            pkgs = data.get("extras_packages") if isinstance(data, dict) else None
            if pkgs is None:
                hit = find_key({"root": data}, lambda k, val: isinstance(val, list) and all(isinstance(x, dict) for x in val))
                pkgs = hit[1] if hit else None
            expect(isinstance(pkgs, list), f"no package list in {str(data)[:200]}")
            if not pkgs:
                raise Warn("0 Extras packages listed (the download manager normally lists several)")
            counts, missing = {}, []
            for p in pkgs:
                name = p.get("name") or p.get("title") or "?"
                status = str(p.get("status", p.get("state", "unknown"))).strip()
                counts[status] = counts.get(status, 0) + 1
                if status != "installed":
                    missing.append(f"{name} ({status})")
            summary = f"{len(pkgs)} packages: " + ", ".join(f"{k} {c}" for k, c in sorted(counts.items()))
            if missing:
                raise Warn(f"{summary}; not installed: {', '.join(missing)[:200]}")
            return summary

        def sandbox():
            need("run_script")
            control = rlib.run_script(v, "result = 6 * 7")
            expect(control == 42, f"plain script returned {control!r}")
            probes = {
                "import os": ("import os\nresult = os.getcwd()", lambda r: isinstance(r, str) and r != ""),
                "open()": ("result = len(open('/etc/os-release').read())", lambda r: isinstance(r, int) and r > 0),
                # io is importable, so this reaches the file audit hook rather than the missing open builtin
                "io.open()": ("import io\nresult = len(io.open('/etc/os-release').read())", lambda r: isinstance(r, int) and r > 0),
                "__import__": ("result = __import__('subprocess').run(['true']).returncode", lambda r: r == 0),
                "import socket": ("import socket\nresult = str(socket.socket())", lambda r: isinstance(r, str)),
                "import ctypes": ("import ctypes\nresult = 1", lambda r: r == 1),
            }
            escaped, odd, how = [], [], []
            for label, (script, succeeded) in probes.items():
                is_error, data, _ = v.call("run_script", {"script": script, "timeout": 30}, timeout=60)
                result = data.get("result") if isinstance(data, dict) else None
                if not is_error and succeeded(result):
                    escaped.append(f"{label} -> {result!r}")
                elif not SANDBOX_REFUSAL.search(data.get("error") or "" if isinstance(data, dict) else str(data)):
                    # a crash or timeout is not proof of a refusal
                    odd.append(f"{label}: {str(data)[-160:]}")
                else:
                    how.append(label)
            expect(not escaped, f"sandbox let through: {'; '.join(escaped)}")
            expect(not odd, f"failed, but not with the sandbox's refusal: {'; '.join(odd)}")
            return f"control ran; refused {', '.join(how)}"

        # ------------------------------------------------------------ run

        try:
            suite.check("get_resolve_status says running", resolve_status)
            suite.check("search_scripting_api finds AddEffect", search_api)
            suite.check("get_scripting_api serves the installed stub, names the running version", scripting_api)
            suite.check("get_whats_new returns the changelog", whats_new)
            suite.check("get_scripting_docs returns README and DCTL docs", scripting_docs)
            suite.check("scratch media timeline", media_timeline)
            suite.check("get_image same timecode -> identical bytes", image_repeatable)
            suite.check("get_image follows timecode and frame_offset", image_follows_timecode)
            suite.check("get_image format and default size", image_format)
            suite.check("get_thumbnails count=4 -> one sheet", thumbnails)
            suite.check("describe_grade timeline: right timeline, Resolve's node count", grade_timeline)
            suite.check("describe_grade timeline_item reads back label and CDL", grade_item)
            suite.check("LUT generate/list/delete", lut_roundtrip)
            suite.check("DCTL write/read/delete", dctl_roundtrip)
            suite.check("write_dctl accepts a float3 local as the return value", dctl_local_return)
            suite.check("write_dctl rejects source that cannot compile", dctl_rejects_garbage)
            suite.check("list_extras_packages (read-only)", extras)
            suite.check("run_script sandbox blocks os, files, processes", sandbox)
        finally:
            def cleanup():
                """Files first (they need no Resolve), then the scratch timeline, then the imported clip; every
                step runs even when an earlier one fails, and all errors are reported together."""
                notes, errors = [], []
                names = (TEST_LUT, TEST_DCTL, LOCAL_DCTL, BAD_DCTL)
                luts_dir = None
                try:
                    if "list_luts" in catalog:
                        luts_dir, files = lut_listing()
                        if delete_tool:
                            for f in files:
                                if f.rsplit("/", 1)[-1] in names:
                                    v.call(delete_tool, {"path": f.rsplit("/", 1)[-1]})
                                    notes.append(f"{f} deleted")
                except Exception as exc:
                    errors.append(f"tool clean-up: {type(exc).__name__}: {exc}")
                for d in {Path(luts_dir) if luts_dir else DEFAULT_LUT_DIR, DEFAULT_LUT_DIR}:
                    for name in names:
                        for f in (d / name, d / "MCP" / name):
                            try:
                                if f.exists():
                                    f.unlink()
                                    notes.append(f"{f.name} removed from disk")
                            except OSError as exc:
                                errors.append(f"{f}: {exc}")
                DRX_FILE.unlink(missing_ok=True)
                try:
                    r = rlib.drop_scratch(v)
                    if r["ok"]:
                        notes.append(f"{r['deleted']} scratch timelines deleted")
                    else:
                        errors.append(f"drop_scratch: {r}")
                    r = rlib.drop_scratch_clips(v)
                    if r["ok"]:
                        notes.append(f"{len(r['deleted'])} scratch clips deleted")
                    else:
                        errors.append(f"drop_scratch_clips: {r}")
                except Exception as exc:
                    errors.append(f"fixture clean-up: {type(exc).__name__}: {exc}")
                try:
                    left = [n for n in names if "list_luts" in catalog and listed(n)]
                    if left:
                        errors.append(f"still listed: {left}")
                except Exception as exc:
                    errors.append(f"final listing: {type(exc).__name__}: {exc}")
                expect(not errors, "; ".join(errors) + (f" (done: {', '.join(notes)})" if notes else ""))
                return ", ".join(notes) or "nothing to remove"
            suite.check("cleanup", cleanup)
    raise SystemExit(suite.finish())


if __name__ == "__main__":
    main()
