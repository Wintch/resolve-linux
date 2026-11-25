#!/usr/bin/env python3
"""Checks for prepare_for_resolve.py: the per-edition decisions without ffmpeg, and one real run when ffmpeg is installed. Run: python3 test_prepare_for_resolve.py"""
import argparse, importlib.util, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("pfr", os.path.join(HERE, "prepare_for_resolve.py"))
pfr = importlib.util.module_from_spec(spec); sys.modules["pfr"] = pfr; spec.loader.exec_module(pfr)
fails = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  got={got!r} want={want!r}"))
    if not ok:
        fails.append(name)


def info(vcodec="h264", acodec="aac", r="30/1", avg="30/1", field="progressive"):
    return {"streams": [{"codec_type": "video", "codec_name": vcodec, "r_frame_rate": r, "avg_frame_rate": avg, "field_order": field},
                        {"codec_type": "audio", "codec_name": acodec}]}


def args(**kw):
    base = dict(edition="studio", framerate=None, no_deinterlace=False, audio_codec="pcm_s24le", video_codec="h264_nvenc", container="mkv", dnxhr_profile="dnxhr_hq", extra_args="")
    base.update(kw)
    return argparse.Namespace(**base)


d = pfr.decide(info(), args())
check("studio: H.264 with AAC keeps its video and only the audio is transcoded", (d.transcode_video, d.transcode_audio), (False, True))
d = pfr.decide(info(), args(edition="free"))
check("free: the same file needs its video re-encoded (no H.264 decode on Linux) and says so", (d.transcode_video, d.transcode_audio, any("free edition" in x for x in d.reason)), (True, True, True))
for codec in ("prores", "dnxhd"):
    check(f"free: {codec} with PCM is left alone", (lambda x: (x.transcode_video, x.transcode_audio, x.needs_ffmpeg))(pfr.decide(info(codec, "pcm_s24le"), args(edition="free"))), (False, False, False))
check("free: hevc and av1 are re-encoded; studio leaves them", ([pfr.decide(info(c, "pcm_s24le"), args(edition="free")).transcode_video for c in ("hevc", "av1")], [pfr.decide(info(c, "pcm_s24le"), args()).transcode_video for c in ("hevc", "av1")]), ([True, True], [False, False]))
d = pfr.decide(info("prores", "pcm_s24le", r="60/1", avg="2400/81"), args(edition="free"))
check("a variable-rate file snaps to the nearest standard rate", (d.transcode_video, d.target_framerate), (True, 29.97))
d = pfr.decide(info("prores", "pcm_s24le", field="tt"), args(edition="free"))
check("interlaced footage is deinterlaced", d.deinterlace, True)
cmd = pfr.build_ffmpeg_cmd(pfr.Path("a.mp4"), pfr.Path("a.mov"), pfr.decide(info(), args(edition="free", video_codec="dnxhd", container="mov")), args(edition="free", video_codec="dnxhd", container="mov", dnxhr_profile="dnxhr_lb"))
check("the DNxHR command carries the profile, 4:2:2 and PCM", ("dnxhd" in cmd, "dnxhr_lb" in cmd, "yuv422p" in cmd, "pcm_s24le" in cmd), (True, True, True, True))
cmd = pfr.build_ffmpeg_cmd(pfr.Path("a.mp4"), pfr.Path("a.mov"), pfr.decide(info(), args(edition="free", video_codec="dnxhd")), args(edition="free", video_codec="dnxhd", dnxhr_profile="dnxhr_hqx"))
check("hqx is 10-bit", "yuv422p10le" in cmd, True)

if shutil.which("ffmpeg") and shutil.which("ffprobe"):
    tmp = tempfile.mkdtemp(prefix="pfr-")
    try:
        src, out = os.path.join(tmp, "in"), os.path.join(tmp, "out"); os.makedirs(src)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=2", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", os.path.join(src, "a.mp4")], check=True)
        rc = pfr.main([src, "--edition", "free", "--output-dir", out, "--workers", "1"])
        files = os.listdir(out) if os.path.isdir(out) else []
        codecs = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", os.path.join(out, files[0])], capture_output=True, text=True).stdout.split() if files else []
        check("end to end, free edition: an H.264 + AAC file becomes DNxHR + PCM in a .mov", (rc, files, codecs), (0, ["a_va.mov"], ["dnxhd", "pcm_s24le"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
else:
    print("skip end-to-end: no ffmpeg here")
print("\nall passed" if not fails else "\nFAILED: " + ", ".join(fails))
sys.exit(1 if fails else 0)
