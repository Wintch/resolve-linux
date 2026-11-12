#!/usr/bin/env python3
"""Live checks of the community MCP server (davinci-resolve-mcp) against the running Resolve.

The vendor ResolveMCP (through `run_script`) sets up and inspects the fixture independently, so every
assertion compares what the community server says or does with what Resolve itself reports.

A capability the server does not document (rlib.COMMUNITY_CAPS: timeline pinning, timeline_item_id, the
duplicate's make_current, the .drx timeline-grade check, the interchange round trip) is a SKIP only when
baselines/community_tools.json never had it; once accepted there, losing it is a FAIL.

Touches only the MCP-Benchmark fixture through SCRATCH_PREFIX timelines (and the synthetic clip, imported
into its Media Pool for the .drx check and deleted again), plus one disposable project (zz_reg_render)
that is deleted again. Two probes write outside ~/Videos on purpose, into zz_reg_ temp
directories under the system temp dir that are removed again: the FCPXML round trip (the server only
accepts its own temp dir as output_dir) and the import of a file outside every Media Storage volume.
Cleanup runs in a finally block.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import rlib
from rlib import Skip, Warn, expect, ca, run_script, FIXTURE_PRELUDE, FIXTURE_PROJECT, FIXTURE_TIMELINE, SCRATCH, SCRATCH_PREFIX

RENDER_PROJECT = SCRATCH_PREFIX + "render"
RENDER_TIMELINE = SCRATCH_PREFIX + "render_tl"
RENDER_DIR = SCRATCH / "render"
RENDER_NAME = SCRATCH_PREFIX + "out"
DRX_PATHS = {k: SCRATCH / f"{SCRATCH_PREFIX}t20_{k}.drx" for k in ("present", "empty", "missing")}
UNKNOWN_ID = "00000000-0000-0000-0000-000000000000"

suite = rlib.Suite("t20_community")
state = {}          # shared between checks: render project status, temp paths
cleanup_paths = []  # files/dirs this module created (never the cached synth media)


# ── helpers ──────────────────────────────────────────────────────────────────

def raw_call(client, tool, action, params=None, timeout=None):
    """Like rlib.ca, but keeps the protocol-level isError flag apart from the body.
    -> (isError flag, parsed body)."""
    args = {"action": action}
    if params is not None:
        args["params"] = params
    r = client.rpc("tools/call", {"name": tool, "arguments": args}, timeout=timeout)
    if "error" in r:
        return True, r["error"]
    res = r.get("result", {})
    text = "".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
    try:
        data = json.loads(text)
    except ValueError:
        data = text
    if isinstance(data, dict):
        data.pop("_operation", None)
    return bool(res.get("isError")), data


_descriptions = {}


def descriptions(c):
    if not _descriptions:
        for t in c.rpc("tools/list")["result"]["tools"]:
            _descriptions[t["name"]] = t.get("description") or ""
    return _descriptions


def baseline_caps():
    try:
        return set(json.loads((rlib.BASELINES / "community_tools.json").read_text()).get("capabilities") or [])
    except (OSError, ValueError):
        return set()


def needs(c, tool):
    """Skip unless the server exposes `tool` (t00's baseline FAILs a tool that disappeared)."""
    if tool not in descriptions(c):
        raise Skip(f"server has no {tool} tool")


def needs_cap(c, *caps):
    """A capability from rlib.COMMUNITY_CAPS must be documented by the server. Absent: SKIP when the accepted
    baseline never had it (an older server), FAIL when it did (an upgrade removed or undocumented it)."""
    for cap in caps:
        tool, marker = rlib.COMMUNITY_CAPS[cap]
        if marker in descriptions(c).get(tool, ""):
            continue
        if cap in baseline_caps():
            raise AssertionError(f"capability {cap} ({tool} documents {marker!r}) is gone, but "
                                 "baselines/community_tools.json has it: removed by an upgrade?")
        raise Skip(f"{tool} does not document {marker!r} on this server (capability {cap})")


def err_code(data):
    err = data.get("error") if isinstance(data, dict) else None
    return err.get("code") if isinstance(err, dict) else None


def ok(result, what):
    """Unwrap a community (is_error, data, images) triple, raising on an error."""
    is_error, data, _ = result
    if is_error:
        raise AssertionError(f"{what}: {json.dumps(data)[:300]}")
    return data


def fixture_state(v):
    """Timeline names, current timeline and bench_tl's video track 1, read straight from Resolve."""
    return run_script(v, FIXTURE_PRELUDE + f"""
names = [proj.GetTimelineByIndex(i).GetName() for i in range(1, proj.GetTimelineCount() + 1)]
cur = proj.GetCurrentTimeline()
base = timeline({FIXTURE_TIMELINE!r})
items = (base.GetItemListInTrack("video", 1) or []) if base is not None else []
result = {{
    "project": proj.GetName(),
    "timelines": names,
    "current": cur.GetName() if cur else None,
    "bench_id": base.GetUniqueId() if base is not None else None,
    "bench_end": base.GetEndFrame() if base is not None else None,
    "bench_items": [{{"id": it.GetUniqueId(), "start": it.GetStart(), "end": it.GetEnd(), "name": it.GetName()}} for it in items],
}}
""")


def current_timeline(v):
    return fixture_state(v)["current"]


def clip_scratch_timeline(v, suffix):
    """A scratch timeline holding the synthetic clip (a timeline of generators only cannot take a timeline
    grade). The clip is imported into the fixture's Media Pool once; cleanup() deletes it again."""
    name = SCRATCH_PREFIX + suffix
    path = str(rlib.media_clip())
    r = run_script(v, FIXTURE_PRELUDE + f"""
old = timeline({name!r})
if old is not None:
    mp.DeleteTimelines([old])
clip = None
for c in mp.GetRootFolder().GetClipList() or []:
    if c.GetClipProperty("File Path") == {path!r}:
        clip = c
if clip is None:
    clip = (resolve.GetMediaStorage().AddItemListToMediaPool([{path!r}]) or [None])[0]
if clip is None:
    raise RuntimeError("could not import the synthetic clip")
tl = mp.CreateTimelineFromClips({name!r}, [clip])
if tl is None:
    raise RuntimeError("CreateTimelineFromClips failed")
proj.SetCurrentTimeline(tl)
result = proj.GetCurrentTimeline().GetName()
""")
    expect(r == name, f"clip timeline not current ({r!r})")
    return name


def timeline_graph_fingerprint(v, name):
    """Node count, labels and LUTs of a fixture timeline's own node graph (None when unreadable)."""
    return run_script(v, FIXTURE_PRELUDE + f"""
t = timeline({name!r})
if t is None:
    raise RuntimeError("timeline {name} not found")
g = t.GetNodeGraph() if hasattr(t, "GetNodeGraph") else None
if g is None:
    result = None
else:
    n = int(g.GetNumNodes() or 0)
    result = {{"nodes": n,
               "labels": [g.GetNodeLabel(i) for i in range(1, n + 1)],
               "luts": [g.GetLUT(i) for i in range(1, n + 1)]}}
""")


def render_project_state(v):
    """Inside the disposable render project (never the fixture prelude: it would switch projects)."""
    return run_script(v, f"""
pm = resolve.GetProjectManager()
proj = pm.GetCurrentProject()
if proj is None or proj.GetName() != {RENDER_PROJECT!r}:
    raise RuntimeError("current project is not {RENDER_PROJECT}: " + (proj.GetName() if proj else "none"))
root = proj.GetMediaPool().GetRootFolder()
clips = root.GetClipList() or []
cur = proj.GetCurrentTimeline()
result = {{
    "clips": [{{"name": c.GetName(), "id": c.GetUniqueId()}} for c in clips],
    "timeline": cur.GetName() if cur else None,
    "video_items": len(cur.GetItemListInTrack("video", 1) or []) if cur else 0,
    "audio_items": len(cur.GetItemListInTrack("audio", 1) or []) if cur else 0,
}}
""")


def current_project(v):
    return run_script(v, "p = resolve.GetProjectManager().GetCurrentProject()\nresult = p.GetName() if p else None")


def project_names(c):
    data = ok(ca(c, "project_manager", "list", {}, timeout=60), "project_manager list")
    return [x if isinstance(x, str) else (x or {}).get("name") for x in (data.get("projects") or [])]


def load_fixture(c):
    ok(ca(c, "project_manager", "load", {"name": FIXTURE_PROJECT}, timeout=120), "load fixture")


def ffprobe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames",
                          "-show_entries", "stream=codec_type,codec_name,duration,r_frame_rate,nb_read_frames:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, timeout=120, check=True).stdout
    return json.loads(out)


def max_volume(path):
    """Peak level of the first audio stream in dBFS (ffmpeg volumedetect)."""
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0", "-af", "volumedetect",
                          "-f", "null", "-"], capture_output=True, text=True, timeout=120).stderr
    m = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", err)
    return float(m.group(1)) if m else None


def timeline_v1(v, name):
    """[(start, end, name)] of a fixture timeline's video track 1, read through Resolve."""
    return [tuple(x) for x in run_script(v, FIXTURE_PRELUDE + f"""
t = timeline({name!r})
if t is None:
    raise RuntimeError("timeline {name} not found")
result = [[i.GetStart(), i.GetEnd(), i.GetName()] for i in t.GetItemListInTrack("video", 1) or []]
""")]


def fcpxml_spine_clips(path):
    """Clip elements on the main spine of an exported FCPXML file or bundle (gaps excluded)."""
    p = Path(path)
    if p.is_dir():
        p = p / "Info.fcpxml"
    spine = ET.parse(p).getroot().find(".//sequence/spine")
    expect(spine is not None, f"no sequence/spine in {p.name}")
    return [el for el in spine if el.tag != "gap" and el.get("duration")]


# ── checks ───────────────────────────────────────────────────────────────────

def check_version(c, v):
    needs(c, "resolve_control")
    data = ok(ca(c, "resolve_control", "get_version"), "get_version")
    vendor_string = run_script(v, "result = resolve.GetVersionString()")
    vendor_list = run_script(v, "result = list(resolve.GetVersion())")
    expect(data.get("version_string") == vendor_string,
           f"community says {data.get('version_string')!r}, Resolve says {vendor_string!r}")
    expect(json.loads(json.dumps(data.get("version"))) == json.loads(json.dumps(vendor_list)),
           f"version list {data.get('version')!r} != {vendor_list!r}")
    expect("Resolve" in str(data.get("product")), f"product {data.get('product')!r}")
    mcp_version = (data.get("mcp") or {}).get("version")
    expect(isinstance(mcp_version, str) and mcp_version, f"no mcp.version in {list(data)}")
    return f"Resolve {vendor_string}, server {mcp_version}"


def check_load_fixture(c, v):
    # The fixture is usually current already, so this proves load and get_current agree with Resolve; a real
    # project switch through load is exercised by the render-project checks below.
    ok(ca(c, "project_manager", "load", {"name": FIXTURE_PROJECT}), "project_manager load")
    cur = ok(ca(c, "project_manager", "get_current"), "project_manager get_current")
    expect(cur.get("name") == FIXTURE_PROJECT, f"community get_current says {cur.get('name')!r}")
    expect(current_project(v) == FIXTURE_PROJECT, "Resolve's current project is not the fixture")
    st = fixture_state(v)
    expect(st["bench_id"], f"fixture has no {FIXTURE_TIMELINE}")
    expect(len(st["bench_items"]) >= 2, f"{FIXTURE_TIMELINE} has {len(st['bench_items'])} items on V1")
    return f"{FIXTURE_PROJECT} loaded, {len(st['timelines'])} timelines, {FIXTURE_TIMELINE} V1 {len(st['bench_items'])} items"


def check_pinning(c, v):
    needs(c, "timeline")
    needs_cap(c, "timeline_pin", "timeline_pin_id")
    name = SCRATCH_PREFIX + "pin"
    st0 = fixture_state(v)
    bench_ids = [it["id"] for it in st0["bench_items"]]
    expect(len(bench_ids) >= 3, f"{FIXTURE_TIMELINE} needs >= 3 items, has {len(bench_ids)}")
    made = run_script(v, FIXTURE_PRELUDE + f"""
old = timeline({name!r})
if old is not None:
    mp.DeleteTimelines([old])
t = mp.CreateEmptyTimeline({name!r})
if t is None:
    raise RuntimeError("CreateEmptyTimeline failed")
proj.SetCurrentTimeline(t)
result = proj.GetCurrentTimeline().GetName()
""")
    expect(made == name, f"scratch timeline not current ({made!r})")
    try:
        # Unpinned: the server sees the empty scratch timeline.
        un = ok(ca(c, "timeline", "get_items", {"track_type": "video", "index": 1}), "unpinned get_items")
        expect(un.get("items") == [], f"unpinned read is not the empty scratch timeline: {len(un.get('items') or [])} items")
        # Pinned by name: bench_tl's items, and the scratch timeline stays current.
        pinned = ok(ca(c, "timeline", "get_items", {"track_type": "video", "index": 1, "timeline_name": FIXTURE_TIMELINE}),
                    "get_items pinned by timeline_name")
        got = [it.get("id") for it in pinned.get("items") or []]
        expect(got == bench_ids, f"pinned get_items returned {len(got)} items, not {FIXTURE_TIMELINE}'s {len(bench_ids)}")
        expect(current_timeline(v) == name, "the name pin did not restore the current timeline")
        # Pinned by id.
        end = ok(ca(c, "timeline", "get_end_frame", {"timeline_id": st0["bench_id"]}), "get_end_frame pinned by timeline_id")
        expect(end.get("frame") == st0["bench_end"], f"pinned end frame {end.get('frame')} != {st0['bench_end']}")
        expect(current_timeline(v) == name, "the id pin did not restore the current timeline")
        # An item lookup follows the pin; without it the id is not on the current timeline.
        target = st0["bench_items"][2]
        start = ok(ca(c, "timeline_item", "get_start", {"timeline_item_id": target["id"], "timeline_name": FIXTURE_TIMELINE}),
                   "timeline_item get_start pinned")
        expect(start.get("start") == target["start"], f"pinned item start {start.get('start')} != {target['start']}")
        flag, data = raw_call(c, "timeline_item", "get_start", {"timeline_item_id": target["id"]})
        expect(flag and err_code(data) == "ITEM_NOT_FOUND",
               f"unpinned lookup of a {FIXTURE_TIMELINE} item on the scratch timeline: isError={flag} code={err_code(data)}")
        # A pin to a timeline that does not exist is refused, not silently ignored.
        flag, data = raw_call(c, "timeline", "get_end_frame", {"timeline_name": SCRATCH_PREFIX + "no_such_timeline"})
        expect(flag and err_code(data) == "TIMELINE_NOT_FOUND", f"missing pin target: isError={flag} code={err_code(data)}")
        expect(current_timeline(v) == name, "the current timeline moved during the pinned calls")
        return f"name and id pins read {FIXTURE_TIMELINE} ({len(bench_ids)} items, end {st0['bench_end']}), current stayed {name}"
    finally:
        rlib.drop_scratch(v)


def check_item_id(c, v):
    needs(c, "timeline_item")
    needs_cap(c, "timeline_item_id")
    ok(ca(c, "timeline", "set_current", {"name": FIXTURE_TIMELINE}), "timeline set_current")
    st = fixture_state(v)
    expect(st["current"] == FIXTURE_TIMELINE, f"set_current left {st['current']!r} current")
    items = st["bench_items"]
    for it in items:   # every item has the same name, so starts and ends are what tells them apart
        got = ok(ca(c, "timeline_item", "get_start", {"timeline_item_id": it["id"]}), f"get_start {it['id']}")
        expect(got.get("start") == it["start"], f"item {it['id']}: start {got.get('start')} != {it['start']}")
        end = ok(ca(c, "timeline_item", "get_end", {"timeline_item_id": it["id"]}), f"get_end {it['id']}")
        expect(end.get("end") == it["end"], f"item {it['id']}: end {end.get('end')} != {it['end']} ({json.dumps(end)[:120]})")
    flag, data = raw_call(c, "timeline_item", "get_name", {"timeline_item_id": UNKNOWN_ID})
    expect(flag, f"unknown id came back without isError: {str(data)[:200]}")
    expect(err_code(data) == "ITEM_NOT_FOUND", f"unknown id: code {err_code(data)!r}")
    return f"{len(items)} ids resolve to the right start and end frames; unknown id -> ITEM_NOT_FOUND + isError"


def check_ignored_params(c, v):
    # ignored_params came with timeline pinning (one call-scope layer): a server that documents the pin must
    # also report unread keys.
    needs(c, "timeline")
    needs_cap(c, "timeline_pin")
    key = SCRATCH_PREFIX + "unknown_key"
    ok(ca(c, "timeline", "set_current", {"name": FIXTURE_TIMELINE}), "timeline set_current")
    st = fixture_state(v)
    data = ok(ca(c, "timeline", "get_end_frame", {key: 1}), "get_end_frame with an unknown key")
    expect(data.get("frame") == st["bench_end"], f"end frame {data.get('frame')} != {st['bench_end']}")
    expect(data.get("ignored_params") == [key], f"ignored_params {data.get('ignored_params')!r}, want [{key!r}]")
    item = st["bench_items"][0]
    data = ok(ca(c, "timeline_item", "get_start", {"timeline_item_id": item["id"], key: 1}), "get_start with an unknown key")
    expect(data.get("start") == item["start"], f"start {data.get('start')} != {item['start']}")
    ignored = data.get("ignored_params") or []
    expect("timeline_item_id" not in ignored, f"a key the action read is reported as ignored: {ignored}")
    expect(key in ignored, f"unknown key missing from ignored_params {ignored}")
    return f"unknown key reported in ignored_params, read keys not"


def check_drx_refusal(c, v):
    """A .drx whose timeline grade is absent or empty must be refused for a timeline apply (issue #318: Resolve
    would replace the timeline graph with an empty one and still return True); one with a timeline grade must
    not be refused for that reason (positive control)."""
    needs(c, "graph")
    needs_cap(c, "drx_track_grade_check")
    SCRATCH.mkdir(parents=True, exist_ok=True)
    for k, path in DRX_PATHS.items():
        rlib.write_drx(path, k)
        cleanup_paths.append(path)
    tl = clip_scratch_timeline(v, "drx")
    try:
        # Give the timeline graph a node first (straight through Resolve), so a wrongful apply, which empties
        # it, would show. Resolve refuses a timeline grade on a timeline of generators only, hence the clip.
        seeded = run_script(v, FIXTURE_PRELUDE + f"""
g = timeline({tl!r}).GetNodeGraph()
result = None if g is None else bool(g.ApplyGradeFromDRX({str(DRX_PATHS["present"])!r}, 0))
""")
        before = timeline_graph_fingerprint(v, tl)
        if before is None:
            raise Skip("Timeline.GetNodeGraph() is not available: the timeline graph cannot be inspected")
        flag, data = raw_call(c, "graph", "apply_grade_from_drx",
                              {"path": str(DRX_PATHS["present"]), "source": "timeline", "timeline_name": tl})
        expect(err_code(data) != "DRX_NO_TIMELINE_GRADE",
               f"a .drx WITH a timeline grade was refused as having none: {str(data)[:200]}")
        control = "confirmation requested" if err_code(data) == "CONFIRMATION_REQUIRED" else f"isError={flag} code={err_code(data)}"
        seen = timeline_graph_fingerprint(v, tl)   # what the control call left (a server that applies directly may change it)
        states = []
        for kind in ("missing", "empty"):
            flag, data = raw_call(c, "graph", "apply_grade_from_drx",
                                  {"path": str(DRX_PATHS[kind]), "source": "timeline", "timeline_name": tl})
            expect(flag, f"{kind} timeline grade applied to the timeline without isError: {str(data)[:200]}")
            expect(err_code(data) == "DRX_NO_TIMELINE_GRADE", f"{kind} timeline grade: code {err_code(data)!r}")
            after = timeline_graph_fingerprint(v, tl)
            expect(after == seen, f"{kind} timeline grade: timeline graph changed although refused: {seen} -> {after}")
            states.append(kind)
        expect(current_timeline(v) == tl, "the pinned calls moved the current timeline")
        graph = (f"timeline graph kept its {seen['nodes']} node(s)" if seen["nodes"] else
                 "timeline graph unchanged, but it has 0 nodes, so an emptying apply would not show "
                 f"(seeding it with ApplyGradeFromDRX returned {seeded!r})")
        return f"{' and '.join(states)} pTrackVer -> DRX_NO_TIMELINE_GRADE; present -> not refused ({control}); {graph}"
    finally:
        rlib.drop_scratch(v)


def check_pinned_write(c, v):
    """Issue #320: a write pinned to a timeline must land there, not on the current one, and leave the current
    timeline as it was."""
    needs(c, "timeline_markers")
    needs_cap(c, "timeline_pin")
    target = rlib.scratch_timeline(v, "pin_target")
    other = rlib.scratch_timeline(v, "pin_other")   # current now
    try:
        start = run_script(v, FIXTURE_PRELUDE + f"result = timeline({target!r}).GetStartFrame()")
        frame = start + 100
        data = ok(ca(c, "timeline_markers", "add", {"frame": frame, "color": "Blue", "name": SCRATCH_PREFIX + "marker",
                                                     "timeline_name": target}), "pinned timeline_markers add")
        st = run_script(v, FIXTURE_PRELUDE + f"""
cur = proj.GetCurrentTimeline()
result = {{"target": {{str(k): m.get("name") for k, m in (timeline({target!r}).GetMarkers() or {{}}).items()}},
          "other": {{str(k): m.get("name") for k, m in (timeline({other!r}).GetMarkers() or {{}}).items()}},
          "current": cur.GetName() if cur else None}}
""")
        names = list(st["target"].values())
        expect(SCRATCH_PREFIX + "marker" in names, f"marker not on the pinned timeline {target}: {st['target']} ({json.dumps(data)[:120]})")
        expect(SCRATCH_PREFIX + "marker" not in st["other"].values(), f"marker landed on the current timeline {other}: {st['other']}")
        expect(st["current"] == other, f"current timeline moved from {other} to {st['current']!r}")
        return f"marker on {target} at {list(st['target'])}, none on the current {other}, which stayed current"
    finally:
        rlib.drop_scratch(v)


def check_duplicate(c, v):
    needs(c, "timeline")
    needs_cap(c, "duplicate_make_current")
    src = rlib.scratch_timeline(v, "dup_src")
    copy = SCRATCH_PREFIX + "dup_copy"
    try:
        n_src = len(fixture_state(v)["bench_items"])
        data = ok(ca(c, "timeline", "duplicate", {"name": copy}), "timeline duplicate")
        expect(data.get("name") == copy, f"duplicate named {data.get('name')!r}")
        expect(data.get("current") == src, f"duplicate reports {data.get('current')!r} current, want {src!r}")
        st = run_script(v, FIXTURE_PRELUDE + f"""
dup = timeline({copy!r})
cur = proj.GetCurrentTimeline()
result = {{"current": cur.GetName() if cur else None,
           "copy_items": len(dup.GetItemListInTrack("video", 1) or []) if dup else None}}
if dup is not None:
    result["deleted"] = bool(mp.DeleteTimelines([dup]))
result["left"] = timeline({copy!r}) is not None
""")
        expect(st["current"] == src, f"Resolve's current timeline is {st['current']!r}, want {src!r}")
        expect(st["copy_items"] == n_src, f"copy has {st['copy_items']} V1 items, source {n_src}")
        expect(st.get("deleted") and not st["left"], "could not delete the duplicate")
        return f"copy made with {n_src} items, original stayed current, copy deleted"
    finally:
        rlib.drop_scratch(v)


def check_roundtrip(c, v):
    """Export the current timeline to FCPXML, import it back, compare, clean up. The default import name
    ("<source> fcpxml Roundtrip") is used, so the leftover check covers it."""
    needs(c, "timeline")
    needs_cap(c, "interchange_roundtrip")
    src = rlib.scratch_timeline(v, "rt_src")   # the import is named after it, so it is zz_reg_ too
    # Another timeline after it, so the source is not the last in the list: Resolve falls back to the last
    # timeline when the current one is deleted, which would hide a probe that does not restore the current one.
    tail = rlib.scratch_timeline(v, "rt_tail")
    try:
        ok(ca(c, "timeline", "set_current", {"name": src}), "timeline set_current")
        before = fixture_state(v)
        expect(before["timelines"][-1] == tail, f"{tail} is not the last timeline: {before['timelines'][-3:]}")
        expect(before["current"] == src, f"{src} is not current ({before['current']!r})")
        src_v1 = timeline_v1(v, src)
        out_dir = tempfile.mkdtemp(prefix=SCRATCH_PREFIX + "rt_")
        cleanup_paths.append(Path(out_dir))
        data = ok(ca(c, "timeline", "probe_interchange_roundtrip",
                     {"format": "fcpxml", "cleanup_imported": True, "output_dir": out_dir}, timeout=300),
                  "probe_interchange_roundtrip")
        after = fixture_state(v)
        state["rt"] = {"src": src, "current": after["current"]}
        new = sorted(set(after["timelines"]) - set(before["timelines"]))
        expect(not new, f"timelines left behind: {new}")
        expect(len(after["timelines"]) == len(before["timelines"]), f"timeline count {len(before['timelines'])} -> {len(after['timelines'])}")
        expect(not [n for n in after["timelines"] if "Roundtrip" in n], "a Roundtrip timeline is left")
        expect(data.get("success") is True, f"success={data.get('success')} stage={data.get('stage')}")
        imp = data.get("import") or {}
        expect(imp.get("id"), f"no imported timeline id: {json.dumps(imp)[:200]}")
        comp = data.get("comparison")
        expect(isinstance(comp, dict), f"no comparison in the result: keys {list(data)}")
        expect(comp.get("match") is True, f"FCPXML round trip differs: {comp.get('difference_count')} differences, "
                                          f"first {json.dumps((comp.get('differences') or [None])[0])[:200]}")
        expect((data.get("cleanup") or {}).get("success") is True, f"cleanup {data.get('cleanup')}")
        exported = data.get("path") or (data.get("export") or {}).get("path")
        expect(exported and Path(exported).exists() and str(Path(exported)).startswith(out_dir),
               f"export {exported!r} is not in {out_dir}")
        clips = fcpxml_spine_clips(exported)
        expect(len(clips) == len(src_v1), f"the exported FCPXML spine has {len(clips)} clips, {src} has {len(src_v1)} on V1")
        return (f"comparison.match true, {len(clips)} clips in the exported file as on {src}, "
                f"imported '{imp.get('name')}' removed ({len(after['timelines'])} timelines before and after)")
    finally:
        rlib.drop_scratch(v)


def check_roundtrip_restores_current(c, v):
    rt = state.get("rt")
    if not rt:
        raise Skip("the round trip did not get far enough to tell")
    expect(rt["current"] == rt["src"],
           f"current timeline moved from {rt['src']} to {rt['current']!r} after the probe (the imported copy became "
           "current on import and was deleted without the original being made current again)")
    return f"{rt['src']} is current again"


def check_roundtrip_import(c, v):
    """The imported timeline itself, read through Resolve (not the server's own comparison): V1 starts, ends and
    names must equal the source's."""
    needs(c, "timeline")
    needs_cap(c, "interchange_roundtrip")
    src = rlib.scratch_timeline(v, "rt_src2")
    imported = SCRATCH_PREFIX + "rt_import"
    try:
        src_v1 = timeline_v1(v, src)
        out_dir = tempfile.mkdtemp(prefix=SCRATCH_PREFIX + "rt_")
        cleanup_paths.append(Path(out_dir))
        data = ok(ca(c, "timeline", "probe_interchange_roundtrip",
                     {"format": "fcpxml", "cleanup_imported": False, "output_dir": out_dir,
                      "imported_timeline_name": imported}, timeout=300), "probe_interchange_roundtrip (kept)")
        names = fixture_state(v)["timelines"]
        expect(imported in names, f"imported timeline {imported} not found (import {json.dumps(data.get('import'))[:200]})")
        got = timeline_v1(v, imported)
        expect(got == src_v1, f"imported V1 differs from {src}: {got[:3]}... vs {src_v1[:3]}... "
                              f"({len(got)} vs {len(src_v1)} items)")
        return f"{imported}: {len(got)} V1 items with the same starts, ends and names as {src}"
    finally:
        rlib.drop_scratch(v)


def check_render_project(c, v):
    needs(c, "project_manager")
    load_fixture(c)
    if RENDER_PROJECT in project_names(c):
        ok(ca(c, "project_manager", "delete", {"name": RENDER_PROJECT}, timeout=120), "delete leftover render project")
    data = ok(ca(c, "project_manager", "create", {"name": RENDER_PROJECT}, timeout=120), "project_manager create")
    expect(data.get("name") == RENDER_PROJECT, f"created {data.get('name')!r}")
    expect(current_project(v) == RENDER_PROJECT, "the new project is not current")
    st = render_project_state(v)
    expect(st["clips"] == [], f"new project already has {len(st['clips'])} clips")
    state["render_project"] = True
    return f"{RENDER_PROJECT} created and current, empty media pool"


def check_import_outside_storage(c, v):
    """A file outside every Media Storage volume. MediaStorage.AddItemListToMediaPool refuses it (t10 checks
    that); import_media goes through MediaPool.ImportMedia. Whatever happens, the server's answer must match
    the media pool: a refusal carries isError and leaves the pool alone, a success reports what landed."""
    if not state.get("render_project"):
        raise Skip("render project was not created")
    outside = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX + "outside_"))
    cleanup_paths.append(outside)
    path = outside / (SCRATCH_PREFIX + "outside.mov")
    shutil.copyfile(rlib.media_clip(), path)
    before = render_project_state(v)["clips"]
    flag, data = raw_call(c, "media_pool", "import_media", {"paths": [str(path)]}, timeout=90)
    after = render_project_state(v)["clips"]
    grew = len(after) - len(before)
    imported = data.get("imported") if isinstance(data, dict) else None
    if flag:
        expect(grew == 0, f"refused with isError, but the media pool grew by {grew}")
        return f"refused with isError ({err_code(data) or str(data)[:80]}), media pool unchanged"
    expect(imported == grew, f"reported imported={imported!r}, the media pool grew by {grew}")
    if grew == 0:
        raise Warn("not imported, but reported as a plain success {imported: 0} without isError")
    raise Warn(f"a file outside every Media Storage volume was imported (imported={imported}, pool +{grew}): "
               "MediaPool.ImportMedia does not enforce Media Storage and import_media adds no guard of its own "
               "(the clip stays in the throwaway project, which is deleted)")


def check_render(c, v):
    if not state.get("render_project"):
        raise Skip("render project was not created")
    needs(c, "render")
    needs(c, "folder")
    expect(current_project(v) == RENDER_PROJECT, "render project is not current")
    clip = rlib.media_clip()
    data = ok(ca(c, "media_pool", "import_media", {"paths": [str(clip)]}, timeout=120), "import_media")
    expect(data.get("imported") == 1, f"imported {data.get('imported')!r}")
    clips = ok(ca(c, "folder", "get_clips", {}), "folder get_clips").get("clips") or []
    ids = [x["id"] for x in clips if x.get("name") == clip.name]
    expect(len(ids) == 1, f"{clip.name} not found once in the media pool: {[x.get('name') for x in clips]}")
    ok(ca(c, "media_pool", "create_timeline_from_clips", {"name": RENDER_TIMELINE, "clip_ids": ids[:1]}, timeout=120),
       "create_timeline_from_clips")
    st = render_project_state(v)
    expect(st["timeline"] == RENDER_TIMELINE, f"current timeline {st['timeline']!r}")
    expect(st["video_items"] == 1 and st["audio_items"] >= 1, f"timeline V1 {st['video_items']} A1 {st['audio_items']} items")

    ca(c, "resolve_control", "open_page", {"page": "deliver"})   # no pages under -nogui: result not asserted
    data = ok(ca(c, "render", "load_preset", {"name": "H.264 Master"}), "load_preset")
    expect(data.get("success") is True, "load_preset H.264 Master returned False")
    RENDER_DIR.mkdir(parents=True, exist_ok=True)   # a missing TargetDir blocks -nogui on a dialog
    for old in glob.glob(str(RENDER_DIR / (RENDER_NAME + "*"))):
        os.remove(old)
    data = ok(ca(c, "render", "set_settings", {"settings": {"TargetDir": str(RENDER_DIR), "CustomName": RENDER_NAME,
                                                            "SelectAllFrames": True}}), "set_settings")
    expect(data.get("success") is True and not data.get("ignored_settings"), f"set_settings {json.dumps(data)[:200]}")
    job = ok(ca(c, "render", "add_job"), "add_job").get("job_id")
    expect(job, "add_job gave no job id")
    state["render_started"] = True
    data = ok(ca(c, "render", "start"), "render start")
    expect(data.get("success") is True, "StartRendering returned False")
    t0, status = time.monotonic(), {}
    while time.monotonic() - t0 < 180:
        rendering = ok(ca(c, "render", "is_rendering"), "is_rendering").get("rendering")
        status = ok(ca(c, "render", "get_job_status", {"job_id": job}), "get_job_status")
        if not rendering and status.get("JobStatus") in ("Complete", "Failed", "Cancelled"):
            break
        time.sleep(1)
    else:
        ca(c, "render", "stop")   # never leave a render running into a project switch
        raise AssertionError(f"render still running after 180 s: {status}")
    seconds = round(time.monotonic() - t0, 1)
    expect(status.get("JobStatus") == "Complete", f"job {status.get('JobStatus')}: {status.get('Error', '')}")
    jobs = ok(ca(c, "render", "list_jobs"), "list_jobs").get("jobs") or []
    expect(any(isinstance(j, dict) and j.get("JobId") == job for j in jobs), f"job {job} not in list_jobs")
    files = sorted(glob.glob(str(RENDER_DIR / (RENDER_NAME + "*"))))
    cleanup_paths.extend(Path(f) for f in files)
    expect(len(files) == 1, f"rendered files: {[Path(f).name for f in files]}")
    info = ffprobe(files[0])
    streams = info.get("streams") or []
    vs = [s for s in streams if s.get("codec_type") == "video"]
    aus = [s for s in streams if s.get("codec_type") == "audio"]
    expect([s.get("codec_name") for s in vs] == ["h264"], f"video streams {[s.get('codec_name') for s in vs]}")
    expect(aus, "no audio stream")
    num, _, den = str(vs[0].get("r_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1)
    frames = int(vs[0].get("nb_read_frames") or 0)
    want = round(6.0 * fps)   # the 6 s clip, whatever rate the new project's timeline has
    expect(abs(frames - want) <= 1, f"{frames} video frames at {fps:g} fps, want {want} (6 s)")
    adur = float(aus[0].get("duration") or (info.get("format") or {}).get("duration") or 0)
    expect(abs(adur - 6.0) <= 1.0 / max(fps, 1), f"audio lasts {adur:.3f} s, want 6 s")
    peak = max_volume(files[0])
    expect(peak is not None and peak > -30, f"audio peak {peak} dBFS: the 1 kHz tone (-18 dBFS) is missing")
    return (f"{Path(files[0]).name}: h264 {frames} frames at {fps:g} fps + {aus[0].get('codec_name')} {adur:.2f} s "
            f"peaking {peak} dBFS, rendered in {seconds} s")


def check_render_cleanup(c, v):
    if not state.get("render_project"):
        raise Skip("render project was not created")
    load_fixture(c)
    expect(current_project(v) == FIXTURE_PROJECT, "fixture is not current after load")
    data = ok(ca(c, "project_manager", "delete", {"name": RENDER_PROJECT}, timeout=120), "project_manager delete")
    expect(data.get("success") is True, f"delete {json.dumps(data)[:200]}")
    expect(RENDER_PROJECT not in project_names(c), f"{RENDER_PROJECT} still listed")
    state["render_project"] = False
    removed = 0
    for f in glob.glob(str(RENDER_DIR / (RENDER_NAME + "*"))):
        os.remove(f)
        removed += 1
    expect(not glob.glob(str(RENDER_DIR / (RENDER_NAME + "*"))), "rendered file still present")
    return f"{RENDER_PROJECT} deleted, fixture current, {removed} rendered file(s) removed"


# ── run ──────────────────────────────────────────────────────────────────────

def stop_render_if_current(c, v):
    if state.get("render_started") and current_project(v) == RENDER_PROJECT:
        ca(c, "render", "stop", timeout=60)


def cleanup(c, v):
    for step in (
        lambda: stop_render_if_current(c, v),
        lambda: load_fixture(c),
        lambda: (RENDER_PROJECT in project_names(c)) and ca(c, "project_manager", "delete", {"name": RENDER_PROJECT}, timeout=120),
        lambda: rlib.drop_scratch(v),
        lambda: rlib.drop_scratch_clips(v),
        lambda: load_fixture(c),
    ):
        try:
            step()
        except Exception as exc:
            print(f"  cleanup: {type(exc).__name__}: {exc}", flush=True)
    for p in cleanup_paths + [Path(f) for f in glob.glob(str(RENDER_DIR / (RENDER_NAME + "*")))]:
        try:
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
        except OSError as exc:
            print(f"  cleanup: {exc}", flush=True)


def main():
    rlib.main_guard()
    with rlib.vendor() as v, rlib.community() as c:
        try:
            suite.check("get_version matches Resolve's GetVersionString", lambda: check_version(c, v))
            suite.check("project_manager load/get_current report the fixture", lambda: check_load_fixture(c, v))
            suite.check("timeline pinning by timeline_name / timeline_id", lambda: check_pinning(c, v))
            suite.check("timeline_item_id lookup and ITEM_NOT_FOUND", lambda: check_item_id(c, v))
            suite.check("unknown param keys come back in ignored_params", lambda: check_ignored_params(c, v))
            suite.check("pinned write lands on the pinned timeline", lambda: check_pinned_write(c, v))
            suite.check(".drx without a timeline grade -> DRX_NO_TIMELINE_GRADE", lambda: check_drx_refusal(c, v))
            suite.check("timeline duplicate keeps the original current", lambda: check_duplicate(c, v))
            suite.check("FCPXML round trip matches, leaves nothing", lambda: check_roundtrip(c, v))
            suite.check("round-trip probe restores the current timeline", lambda: check_roundtrip_restores_current(c, v))
            suite.check("FCPXML import reproduces V1 (read through Resolve)", lambda: check_roundtrip_import(c, v))
            suite.check("render project: create " + RENDER_PROJECT, lambda: check_render_project(c, v))
            suite.check("import_media outside Media Storage: answer matches the pool", lambda: check_import_outside_storage(c, v))
            suite.check("render flow: H.264 Master of an imported clip", lambda: check_render(c, v))
            suite.check("render project: delete " + RENDER_PROJECT, lambda: check_render_cleanup(c, v))
        finally:
            cleanup(c, v)
    raise SystemExit(suite.finish())


if __name__ == "__main__":
    main()
