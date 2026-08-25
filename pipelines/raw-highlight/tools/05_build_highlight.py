#!/usr/bin/env python3
"""Assembles the highlight video from graded/, in chronological order (burst_id).

Single photos -> clip with a Ken Burns effect (alternating zoom in / zoom out).
Bursts marked as "sequence" -> mini flipbook with all their frames.
All segments are joined with crossfade (xfade). No music by default;
pass --music to mix in an audio track (looped/trimmed to the video's duration).

Usage: tools/05_build_highlight.py [--pace medium] [--music file.mp3] [--out output/highlight.mp4]
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_film_texture import ensure_scratch_texture  # noqa: E402
from lib_preset import build_film_chain, film_scratch_params, list_presets, load_preset  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_DIR = ROOT / "analysis"
GRADED_DIR = ROOT / "graded"
PROXY_DIR = ROOT / "proxies_avif"

# Configurable via --source: "graded" (default, already-processed TIFF, for
# the real cut) or "proxies" (lightweight AVIF, for reviewing discards that
# never went through grading because they weren't approved).
SOURCE_DIR = GRADED_DIR
SOURCE_EXT = ".tif"
OUTPUT_DIR = ROOT / "output"
SEGMENTS_DIR = OUTPUT_DIR / "_segments"
TEXTURE_DIR = OUTPUT_DIR / "_textures"

WIDTH, HEIGHT, FPS = 1920, 1280, 25  # native 3:2 (same as the CR2), no top/bottom crop
FILL_VF = f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT}"
# NVENC (GPU, available GTX 960) instead of software libx264: cuts CPU/RAM
# usage a lot during segment rendering and the xfade merge. Fast, decent
# quality for iterating/previewing (lossy).
NVENC_ARGS = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc:v", "vbr", "-cq:v", "19", "-b:v", "0"]
# FFV1: real lossless (not an approximation via low CRF). For the final
# quality pass, every intermediate step (segment -> xfade batches) stays with
# zero accumulated loss versus graded/*.tif; only the final delivery encode
# (AV1) is lossy. FFV1 isn't valid in an mp4 container, so intermediates in
# this mode use .mkv.
HQ_ARGS = ["-c:v", "ffv1", "-level", "3", "-g", "1", "-slices", "4", "-slicecrc", "1"]
# Final encode in AV1 (libsvtav1): much better quality/size than h264 at the
# same bitrate, meant for the final delivery, not for iterating (it's
# slower). Target bitrate (not a loose CRF) so the final size is predictable:
# the goal is a lightweight file that's quick to view on a phone, not the
# maximum possible quality out of the lossless intermediate. No film grain
# synthesis (more demanding to decode on a phone).
AV1_ARGS = ["-c:v", "libsvtav1", "-preset", "4", "-b:v", "3M"]
# H264 as a delivery alternative: worse quality/size than AV1 at the same
# bitrate, but decodes on absolutely anything (old phones, WhatsApp, etc.)
# without depending on hardware AV1 support.
H264_ARGS = ["-c:v", "libx264", "-preset", "medium", "-b:v", "3M", "-maxrate", "3.5M", "-bufsize", "6M"]


def seg_ext(hq):
    return ".mkv" if hq else ".mp4"


def graded_path(cr2_name):
    return SOURCE_DIR / f"{Path(cr2_name).stem}{SOURCE_EXT}"


def _label_filter(label):
    if not label:
        return None
    safe = label.replace("'", "").replace(":", "")
    return (
        f"drawtext=text='{safe}':x=20:y=h-th-20:fontsize=36:fontcolor=white:"
        f"box=1:boxcolor=black@0.6:boxborderw=10"
    )


def render_single(cr2_name, duration, motion, out_path, label=None, encoder_args=None):
    if out_path.exists():
        return out_path, None
    encoder_args = encoder_args or NVENC_ARGS
    src = graded_path(cr2_name)
    tmp_png = None
    if src.suffix.lower() == ".avif":
        # the avif demuxer doesn't accept -loop (needed for Ken Burns over a
        # still image); decode once to PNG and loop that instead.
        tmp_png = Path(tempfile.mktemp(suffix=".png"))
        conv = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), str(tmp_png)],
            capture_output=True, text=True,
        )
        if conv.returncode != 0 or not tmp_png.exists():
            return None, f"render_single failed on {cr2_name} (avif->png conversion): {conv.stderr[-300:]}"
        src = tmp_png
    n_frames = max(1, int(duration * FPS))
    z0, z1 = motion.get("zoom_start", 1.0), motion.get("zoom_end", 1.0)
    zoom_expr = f"'{z0}+({z1}-{z0})*on/{n_frames}'"
    vf = (
        f"scale={WIDTH*2}:{HEIGHT*2}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH*2}:{HEIGHT*2},"
        f"zoompan=z={zoom_expr}:d={n_frames}:s={WIDTH}x{HEIGHT}:fps={FPS}"
    )
    label_f = _label_filter(label)
    if label_f:
        vf += f",{label_f}"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(src),
        "-vf", vf, "-t", str(duration), "-r", str(FPS),
        "-pix_fmt", "yuv420p", *encoder_args, str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if tmp_png:
        tmp_png.unlink(missing_ok=True)
    if proc.returncode != 0 or not out_path.exists():
        return None, f"render_single failed on {cr2_name}: {proc.stderr[-300:]}"
    return out_path, None


def render_sequence(files, frame_duration, out_path, label=None, encoder_args=None):
    if out_path.exists():
        return out_path, None
    encoder_args = encoder_args or NVENC_ARGS
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for fn in files:
            f.write(f"file '{graded_path(fn)}'\nduration {frame_duration}\n")
        f.write(f"file '{graded_path(files[-1])}'\n")  # required by the concat demuxer
        listfile = f.name
    vf = FILL_VF
    label_f = _label_filter(label)
    if label_f:
        vf += f",{label_f}"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listfile,
        "-vf", vf, "-r", str(FPS), "-pix_fmt", "yuv420p", *encoder_args, str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    Path(listfile).unlink()
    if proc.returncode != 0 or not out_path.exists():
        return None, f"render_sequence failed on {files[0]}: {proc.stderr[-300:]}"
    return out_path, None


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def _xfade_chain(clips, durations, transition, out_path, encoder_args=None):
    """Chains a small batch of clips with xfade in a single filter_complex."""
    encoder_args = encoder_args or NVENC_ARGS
    n = len(clips)
    if n == 1:
        shutil.copy(clips[0], out_path)
        return durations[0]

    inputs = []
    for c in clips:
        inputs += ["-i", str(c)]

    filter_parts = []
    running = durations[0]
    prev_label = "0:v"
    for i in range(1, n):
        offset = max(0.0, running - transition)
        out_label = f"v{i}" if i < n - 1 else "vout"
        filter_parts.append(
            f"[{prev_label}][{i}:v]xfade=transition=fade:duration={transition}:offset={offset:.3f}[{out_label}]"
        )
        running = running + durations[i] - transition
        prev_label = out_label

    filter_complex = ";".join(filter_parts)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", *inputs,
        "-filter_complex", filter_complex, "-map", f"[{prev_label}]",
        "-r", str(FPS), "-pix_fmt", "yuv420p", *encoder_args,
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    return running


def concat_with_xfade(clips, durations, transition, out_path, batch_size=8, encoder_args=None):
    """Chains clips with xfade in small batches (batch_size concurrent inputs
    at most), so memory doesn't spike when there are many segments: a single
    filter_complex with ~70 inputs open at once can end up using several GB
    of RAM. It merges in batches, then merges the batches with each other,
    down to a single file.

    Iterative, not recursive: the previous level is deleted as soon as the
    next one has consumed it (not at the very end). With cleanup deferred to
    "after all the recursion," every intermediate level stays alive at the
    same time as all the others -- with lossless FFV1 and several levels
    that can add up to several times the video's total size in temp files.
    With immediate cleanup the peak stays bounded to at most 2 levels alive
    at once, no matter how many total levels are needed or what batch_size
    is chosen.

    Each level uses its own temp directory (tempfile.mkdtemp) so filenames
    don't collide across levels."""
    encoder_args = encoder_args or NVENC_ARGS
    cur_clips, cur_durs = list(clips), list(durations)
    prev_tmp_dir = None
    try:
        while len(cur_clips) > batch_size:
            tmp_dir = Path(tempfile.mkdtemp(prefix="xfade_lvl_", dir=str(out_path.parent)))
            batch_clips, batch_durations = [], []
            for i in range(0, len(cur_clips), batch_size):
                chunk = cur_clips[i:i + batch_size]
                chunk_durs = cur_durs[i:i + batch_size]
                batch_out = tmp_dir / f"batch_{i // batch_size:03d}{out_path.suffix}"
                d = _xfade_chain(chunk, chunk_durs, transition, batch_out, encoder_args)
                batch_clips.append(batch_out)
                batch_durations.append(d)
            if prev_tmp_dir is not None:
                shutil.rmtree(prev_tmp_dir, ignore_errors=True)
            prev_tmp_dir = tmp_dir
            cur_clips, cur_durs = batch_clips, batch_durations
        return _xfade_chain(cur_clips, cur_durs, transition, out_path, encoder_args)
    finally:
        if prev_tmp_dir is not None:
            shutil.rmtree(prev_tmp_dir, ignore_errors=True)


def mux_music(video_path, music_path, out_path):
    duration = probe_duration(video_path)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(video_path),
        "-stream_loop", "-1", "-i", str(music_path),
        "-filter_complex",
        f"[1:a]atrim=0:{duration},afade=t=in:st=0:d=1.5,afade=t=out:st={duration - 1.5}:d=1.5[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-shortest", str(out_path),
    ]
    subprocess.run(cmd, check=True)


def has_audio(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    return bool(out)


def cap_bitrate(in_path, out_path, bitrate):
    """Re-encodes the video to a target bitrate (e.g. '1.5M'), preserving audio as-is if present."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(in_path),
        *NVENC_ARGS[:2], "-rc:v", "vbr", "-b:v", bitrate, "-maxrate", bitrate,
        "-bufsize", bitrate,
    ]
    cmd += ["-c:a", "copy"] if has_audio(in_path) else []
    cmd += [str(out_path)]
    subprocess.run(cmd, check=True)


def film_setup(preset):
    """Prepares the film-damage pass: returns (extra_inputs, chain).

    The damage is applied to the already-assembled video, not photo by photo
    - see the long note in lib_preset.build_film_chain. It hangs off the
    delivery encode in the same ffmpeg run so it doesn't add an extra lossy
    generation between the lossless intermediate and the final file."""
    inputs = []
    scratch_index = None
    params = film_scratch_params(preset)
    if params:
        tex = ensure_scratch_texture(TEXTURE_DIR, WIDTH, HEIGHT, fps=FPS, **params)
        inputs += ["-stream_loop", "-1", "-i", str(tex)]
        scratch_index = 1
    return inputs, build_film_chain(preset, WIDTH, HEIGHT, FPS, scratch_index=scratch_index)


def _film_map_args(in_path, film):
    """Inserts the damage filter_complex and the explicit stream mapping.

    Watch out: using filter_complex turns off ffmpeg's automatic mapping. If
    audio isn't mapped by hand, the final file comes out silent with no
    error at all."""
    if not film:
        return [], []
    extra_inputs, chain = film
    maps = ["-filter_complex", chain, "-map", "[vfilm]"]
    if has_audio(in_path):
        maps += ["-map", "0:a:0"]
    return extra_inputs, maps


def encode_final_av1(in_path, out_path, film=None):
    """Final delivery encode in AV1 (the only step with real lossy compression
    in the whole pipeline when running --quality final). Re-encodes audio to
    Opus (AV1+Opus is the native/most compatible combination in modern
    mp4/mkv)."""
    extra_inputs, maps = _film_map_args(in_path, film)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(in_path), *extra_inputs,
        *maps, *AV1_ARGS,
    ]
    cmd += ["-c:a", "libopus", "-b:a", "192k"] if has_audio(in_path) else []
    cmd += [str(out_path)]
    subprocess.run(cmd, check=True)


def encode_final_h264(in_path, out_path, film=None):
    """Delivery alternative in H264+AAC, same logic as encode_final_av1 but
    with the most compatible codec (old phones, WhatsApp, etc)."""
    extra_inputs, maps = _film_map_args(in_path, film)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(in_path), *extra_inputs,
        *maps, *H264_ARGS, "-pix_fmt", "yuv420p",
    ]
    cmd += ["-c:a", "aac", "-b:a", "192k"] if has_audio(in_path) else []
    cmd += [str(out_path)]
    subprocess.run(cmd, check=True)


def apply_film_fast(in_path, out_path, film):
    """Damage pass in --quality fast mode: there's no separate delivery
    encode to hang it off of there, so it runs as its own step with NVENC."""
    extra_inputs, maps = _film_map_args(in_path, film)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(in_path), *extra_inputs,
           *maps, *NVENC_ARGS, "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "copy"] if has_audio(in_path) else []
    cmd += [str(out_path)]
    subprocess.run(cmd, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pace", default="medium", help="pace preset in tools/presets/pace/")
    parser.add_argument("--shortlist", default=str(ANALYSIS_DIR / "shortlist.json"))
    parser.add_argument("--music", default=None, help="optional audio file to mix in")
    parser.add_argument("--out", default=str(OUTPUT_DIR / "highlight.mp4"))
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--types", default="single,sequence",
                         help="group types to include, comma-separated: single,sequence")
    parser.add_argument("--orientation", default="all", choices=["all", "horizontal", "vertical"],
                         help="horizontal = rotation==0, vertical = groups we rotated (photos shot in portrait)")
    parser.add_argument("--order", default="chrono", choices=["chrono", "alternate"],
                         help="chrono = chronological order by burst_id. alternate = interleaves sequence/single (moves, freezes, moves...), starting with a sequence")
    parser.add_argument("--bitrate", default=None,
                         help="if passed (e.g. 1.5M), caps the final file's bitrate with -maxrate/-bufsize")
    parser.add_argument("--labels", action="store_true",
                         help="burns 'bXXX type' into each segment, so exact groups can be referenced when giving feedback")
    parser.add_argument("--quality", default="fast", choices=["fast", "final"],
                         help="fast = NVENC h264, for iterating quickly. final = lossless FFV1 on every intermediate + AV1 delivery (slower, no accumulated loss)")
    parser.add_argument("--source", default="graded", choices=["graded", "proxies"],
                         help="graded = already-processed TIFF (the real cut, approved only). proxies = lightweight AVIF (for reviewing discards, which never get graded)")
    parser.add_argument("--film", default=None,
                         help=f"film-damage preset from tools/presets/film/ "
                              f"(grain, halation, scratches, gate weave, light leaks). "
                              f"Available: {list_presets('film')}. Applied at the end, "
                              f"on the already-assembled video - doesn't invalidate the segment cache")
    parser.add_argument("--delivery-codec", default="av1", choices=["av1", "h264"],
                         help="codec for the final encode in --quality final. av1 = better quality/size, needs a modern decoder. h264 = more compatible (old phones, WhatsApp)")
    args = parser.parse_args()

    global SOURCE_DIR, SOURCE_EXT
    if args.source == "proxies":
        SOURCE_DIR, SOURCE_EXT = PROXY_DIR, ".avif"
    else:
        SOURCE_DIR, SOURCE_EXT = GRADED_DIR, ".tif"

    encoder_args = HQ_ARGS if args.quality == "final" else NVENC_ARGS
    hq = args.quality == "final"

    # the preset name is validated before starting: a typo in --film
    # shouldn't be discovered only after half an hour of rendering.
    film_preset = load_preset(args.film, category="film") if args.film else None

    wanted_types = set(args.types.split(","))

    shortlist = json.loads(Path(args.shortlist).read_text())
    groups = [
        g for g in shortlist["groups"]
        if g.get("approved", g["suggested_keep"]) and g["type"] in wanted_types
    ]
    if args.orientation == "horizontal":
        groups = [g for g in groups if g.get("rotation", 0) == 0]
    elif args.orientation == "vertical":
        groups = [g for g in groups if g.get("rotation", 0) != 0]
    groups.sort(key=lambda g: g["burst_id"])
    if not groups:
        raise SystemExit("No approved groups match --types.")

    if args.order == "alternate":
        seqs = [g for g in groups if g["type"] == "sequence"]
        singles = [g for g in groups if g["type"] != "sequence"]
        interleaved = []
        for a, b in zip(seqs, singles):
            interleaved += [a, b]
        interleaved += seqs[len(singles):] + singles[len(seqs):]
        groups = interleaved

    pace = load_preset(args.pace, category="pace")
    kb_in = load_preset("kenburns_in", category="motion")
    kb_out = load_preset("kenburns_out", category="motion")

    SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Pace: {pace['name']} - {pace['description']}")
    print(f"Groups: {len(groups)}")

    jobs = []
    for i, g in enumerate(groups):
        # stable name by burst_id (not by this run's index) so the segment
        # cache can be reused across runs with a different --types/order.
        # the quality suffix avoids mixing lossless (hq) cache with NVENC (fast).
        suffix = "_lbl" if args.labels else ""
        qsuffix = "_hq" if hq else ""
        seg_out = SEGMENTS_DIR / f"seg_b{g['burst_id']:03d}_{pace['name']}{suffix}{qsuffix}{seg_ext(hq)}"
        label = f"b{g['burst_id']} {g['type']} x{g['count']}" if args.labels else None
        if g["type"] == "sequence":
            jobs.append(("sequence", g["files"], pace["sequence_frame_duration"], seg_out, label))
        else:
            motion = kb_in if i % 2 == 0 else kb_out
            jobs.append(("single", g["representative"], pace["photo_duration"], motion, seg_out, label))

    print("Rendering segments (Ken Burns / flipbook)...")
    results = {}
    errors = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {}
        for job in jobs:
            if job[0] == "sequence":
                _, files, dur, seg_out, label = job
                fut = ex.submit(render_sequence, files, dur, seg_out, label, encoder_args)
            else:
                _, rep, dur, motion, seg_out, label = job
                fut = ex.submit(render_single, rep, dur, motion, seg_out, label, encoder_args)
            futures[fut] = seg_out
        for fut in futures:
            out, err = fut.result()
            if err:
                errors.append(err)
            else:
                results[futures[fut]] = out

    print(f"  ok: {len(results)}/{len(jobs)}")
    for e in errors:
        print(f"  ERROR: {e}", file=sys.stderr)
    if errors:
        raise SystemExit(f"{len(errors)} segments failed, not continuing with the final assembly.")

    clip_paths = [j[-2] for j in jobs]  # same order as groups (job = (..., seg_out, label))
    durations = [probe_duration(p) for p in clip_paths]

    print("Joining segments with crossfade...")
    final_out = Path(args.out)
    ext = seg_ext(hq)
    silent_out = OUTPUT_DIR / f"_silent_{final_out.stem}{ext}"
    total = concat_with_xfade(clip_paths, durations, pace["transition_duration"], silent_out,
                               encoder_args=encoder_args)
    print(f"  final duration: {total:.1f}s")

    # in hq mode, silent_out/working stays in an intermediate container (mkv,
    # lossless FFV1 video) until the final AV1 encode; in fast mode working
    # already has the delivery video (mp4, NVENC h264) and just needs moving.
    if args.music:
        print(f"Mixing music: {args.music}")
        working = OUTPUT_DIR / f"_muxed_{final_out.stem}{ext}"
        mux_music(silent_out, args.music, working)
        silent_out.unlink()
    else:
        working = silent_out

    film = film_setup(film_preset) if film_preset else None
    if film:
        print(f"Film damage: {film_preset['name']}")

    if hq:
        print(f"Final encode in {args.delivery_codec.upper()} (this takes a while)...")
        if args.delivery_codec == "h264":
            encode_final_h264(working, final_out, film=film)
        else:
            encode_final_av1(working, final_out, film=film)
        working.unlink()
    elif film:
        apply_film_fast(working, final_out, film)
        working.unlink()
    else:
        shutil.move(str(working), str(final_out))

    if args.bitrate and not hq:
        print(f"Capping bitrate to {args.bitrate}...")
        capped = OUTPUT_DIR / f"_capped_{final_out.stem}.mp4"
        cap_bitrate(final_out, capped, args.bitrate)
        shutil.move(str(capped), str(final_out))

    print(f"-> {final_out}")


if __name__ == "__main__":
    main()
