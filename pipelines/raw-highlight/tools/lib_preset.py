"""Loads presets (tools/presets/<category>/*.json) and builds the corresponding
ffmpeg filter for the "look" (color) category. Three preset categories, meant to
combine with each other:

  look/   color: contrast/saturation (eq), shadow/mid/highlight split-tone
          (colorbalance), vignette, and grain. Used in 04_grade_export and 05.
  pace/   pacing: how long each segment/transition lasts in the final video
          (faster = tighter cuts, slower = more contemplative).
          Sketch - wired up in 05_build_highlight.
  motion/ per-photo motion effects (ken burns, whip pan, etc).
          Sketch - wired up in 05_build_highlight.
  film/   film damage (grain, halation, scratches, gate weave, light leaks,
          chromatic aberration). Final pass over the already-assembled video -
          see build_film_chain(). Used in 05 and in 06_film_damage.

Meant to be reused both when grading single photos (04_grade_export.sh) and
when assembling the final video (05_build_highlight.sh), so the same look
gets applied consistently across the whole pipeline.
"""
import json
import math
from pathlib import Path

PRESETS_DIR = Path(__file__).resolve().parent / "presets"


def load_preset(name, category="look"):
    path = PRESETS_DIR / category / f"{name}.json"
    if not path.exists():
        available = [p.stem for p in (PRESETS_DIR / category).glob("*.json")]
        raise SystemExit(f"Preset '{name}' not found at {path}. Available ({category}): {available}")
    return json.loads(path.read_text())


def build_filter_chain(preset):
    """Returns the string for ffmpeg's -vf."""
    parts = []

    eq = preset.get("eq")
    if eq:
        eq_args = ":".join(f"{k}={v}" for k, v in eq.items())
        parts.append(f"eq={eq_args}")

    cb = preset.get("colorbalance")
    if cb:
        cb_args = ":".join(f"{k}={v}" for k, v in cb.items())
        parts.append(f"colorbalance={cb_args}")

    vig = preset.get("vignette")
    if vig:
        vig_args = ":".join(f"{k}={v}" for k, v in vig.items())
        parts.append(f"vignette={vig_args}" if vig_args else "vignette")

    grain = preset.get("grain")
    if grain and grain.get("strength", 0) > 0:
        s = grain["strength"]
        parts.append(f"noise=alls={s}:allf=t+u")

    return ",".join(parts)


def list_presets(category="look"):
    return sorted(p.stem for p in (PRESETS_DIR / category).glob("*.json"))


# ---------------------------------------------------------------------------
# Film damage (film/ category)
# ---------------------------------------------------------------------------
# Applied as a pass over the ALREADY-ASSEMBLED video, not photo by photo, for
# three reasons:
#   1. It doesn't invalidate the segment cache (96 FFV1 files, 3.7 GB) - the
#      effect's intensity can be iterated on without re-rendering anything.
#   2. Grain/scratches evolve continuously across the whole piece. Applied per
#      segment they'd reset on every cut and the pattern would show.
#   3. Gate weave also moves the transitions (xfade), not just the inside of
#      each photo, which is what makes it feel like a single strip of film
#      instead of 96 clips each shaking on their own.
#
# The whole chain works in planar RGB (gbrp). The "screen" and "overlay" blend
# modes are defined over color channels: applying them on YUV planes breaks
# the color (screen(128,128) on chroma gives 192, i.e. a strong shift).
# Converting to gbrp once at the start and back to yuv420p at the end is
# cheaper than fighting that.
#
# WATCH OUT: a single format=gbrp at the start is NOT enough. The scale filter
# negotiates its own output format freely, and if what follows accepts YUV
# (crop, blend, gblur accept anything) it picks yuv420p and the entire
# downstream chain runs in YUV without warning. The symptom is an overall
# magenta shift, not an error. That's why format=gbrp is repeated AFTER every
# scale. That same scale also touches the SAR when it changes the pixel aspect
# ratio (1920x1280 -> 1932x1292 leaves sar=323/322, i.e. the final video comes
# out stretched), so setsar=1 also goes right after.


def _pin(*filters):
    """Pins format and SAR at the end of a chain segment (see note above)."""
    return ",".join([*filters, "format=gbrp", "setsar=1"])


def _weave_expr(amp, freq_a, freq_b, weight_a, phase_a, phase_b, offset, speed):
    """Smooth drift as the sum of two non-harmonic sine waves.

    With a single sine wave the motion reads as mechanical, predictable sway;
    with two frequencies that aren't multiples of each other the pattern takes
    a very long time to repeat and reads as organic instability, which is what
    a real projector does. Pure frame-to-frame noise doesn't work either:
    that reads as a shaky camera, not as gate weave."""
    wa = weight_a
    wb = 1.0 - weight_a
    ka = 2 * math.pi * freq_a * speed
    kb = 2 * math.pi * freq_b * speed
    return (f"{offset}+{amp:.3f}*({wa:.2f}*sin({ka:.4f}*t+{phase_a:.2f})"
            f"+{wb:.2f}*sin({kb:.4f}*t+{phase_b:.2f}))")


def build_film_chain(preset, width, height, fps, in_label="0:v",
                     out_label="vfilm", scratch_index=None):
    """Builds the -filter_complex for film damage.

    Returns the filter_complex string; the output ends up in [out_label]
    already in yuv420p, ready to map to the encoder.

    scratch_index is the index of the ffmpeg input carrying the scratch
    texture (see lib_film_texture). If it's None, that effect is skipped even
    if the preset defines it.
    """
    parts = []
    cur = "film_in"
    parts.append(f"[{in_label}]format=gbrp,setsar=1[{cur}]")

    # --- gate weave: the whole frame drifts and rotates slightly -------------
    # The image is enlarged and a smaller window that moves inside it is
    # cropped out. The margin has to cover the drift PLUS what the rotation
    # eats away at the corners, or black wedges show up at the edges.
    weave = preset.get("weave")
    if weave and weave.get("amplitude_px", 0) > 0:
        amp = float(weave["amplitude_px"])
        rot = float(weave.get("rotation_deg", 0.0)) * math.pi / 180.0
        speed = float(weave.get("speed", 1.0))
        # wedge the rotation eats into the edge ~ (dimension/2)*sin(angle)
        rot_bleed = math.sin(abs(rot)) * max(width, height) / 2.0
        margin = int(math.ceil(amp + rot_bleed + 2))
        zw, zh = width + 2 * margin, height + 2 * margin
        xe = _weave_expr(amp, 0.83, 1.97, 0.62, 0.0, 1.10, margin, speed)
        ye = _weave_expr(amp, 0.71, 1.53, 0.55, 2.30, 0.40, margin, speed)
        chain = f"scale={zw}:{zh},format=gbrp,setsar=1"
        if rot != 0:
            # rotation even slower than the drift: a film reel doesn't shake,
            # it slowly tilts.
            chain += f",rotate=a='{rot:.6f}*sin({2 * math.pi * 0.37 * speed:.4f}*t+0.8)':c=black:bilinear=1"
        # crop re-evaluates x/y on every frame on its own (unlike scale or
        # zoompan, it needs no eval=frame option; in fact it doesn't accept
        # one), so the expressions can depend on t.
        chain = _pin(chain, f"crop=w={width}:h={height}:x='{xe}':y='{ye}'")
        parts.append(f"[{cur}]{chain}[weave]")
        cur = "weave"

    # --- chromatic aberration: channel shift ----------------------------------
    ca = int(preset.get("chroma_shift_px", 0))
    if ca:
        parts.append(f"[{cur}]rgbashift=rh=-{ca}:bh={ca}[ca]")
        cur = "ca"

    # --- grain -----------------------------------------------------------------
    # A noise layer generated separately and blended in overlay mode, instead
    # of the noise filter applied directly on the image. Two reasons: the
    # grain is generated at lower resolution and scaled up (comes out chunky
    # like real grain, not single-pixel noise), and overlay against neutral
    # gray 128 modulates contrast without shifting mean exposure.
    #
    # The noise is generated on a SINGLE plane and then replicated to all
    # three (extractplanes + format). The noise filter doesn't support gray,
    # so ffmpeg converts to gbrp behind the scenes and "alls" ends up
    # injecting INDEPENDENT noise per channel: that's not film grain but
    # digital chroma noise, and on top of that each plane ends up with a
    # different mean (128.4/126.8/127.8 measured), which tints the whole
    # video green.
    grain = preset.get("grain")
    if grain and grain.get("opacity", 0) > 0:
        gs = int(grain.get("strength", 48))
        gop = float(grain["opacity"])
        scale = float(grain.get("coarseness", 0.5))  # 0.5 = ~2px grain
        gw = max(2, int(width * scale) // 2 * 2)
        gh = max(2, int(height * scale) // 2 * 2)
        parts.append(
            _pin(f"color=c=gray:s={gw}x{gh}:r={fps}", "format=gbrp",
                 f"noise=c0s={gs}:c0f=t+u", "extractplanes=g", "format=gbrp",
                 f"scale={width}:{height}:flags=bilinear") + "[grain]"
        )
        parts.append(
            f"[{cur}][grain]blend=all_mode=overlay:all_opacity={gop}:shortest=1[grained]"
        )
        cur = "grained"

    # --- halation: blown-out highlights bleed warm ----------------------------
    hal = preset.get("halation")
    if hal and hal.get("opacity", 0) > 0:
        thr = int(hal.get("threshold", 200))
        sigma = float(hal.get("sigma", 8))
        hop = float(hal["opacity"])
        tint = hal.get("tint", {"rr": 1.0, "gg": 0.45, "bb": 0.20})
        # the blur runs at 1/4 resolution: the result is the same diffuse
        # blob, and it comes out ~16x cheaper than blurring at full size.
        qw, qh = max(2, width // 4), max(2, height // 4)
        lut = (f"lutrgb=r='if(gt(val,{thr}),val,0)':"
               f"g='if(gt(val,{thr}),val,0)':b='if(gt(val,{thr}),val,0)'")
        mixer = ":".join(f"{k}={v}" for k, v in tint.items())
        parts.append(f"[{cur}]split[hbase][hsrc]")
        parts.append(
            "[hsrc]" + _pin(f"scale={qw}:{qh}", "format=gbrp", lut,
                            f"gblur=sigma={sigma}", f"colorchannelmixer={mixer}",
                            f"scale={width}:{height}") + "[glow]"
        )
        parts.append(f"[hbase][glow]blend=all_mode=screen:all_opacity={hop}[haloed]")
        cur = "haloed"

    # --- scratches / dust ------------------------------------------------------
    scr = preset.get("scratches")
    if scr and scr.get("opacity", 0) > 0 and scratch_index is not None:
        sop = float(scr["opacity"])
        parts.append(
            f"[{scratch_index}:v]" + _pin(f"scale={width}:{height}") + "[scr]"
        )
        parts.append(
            f"[{cur}][scr]blend=all_mode=screen:all_opacity={sop}:shortest=1[scratched]"
        )
        cur = "scratched"

    # --- light leaks -------------------------------------------------------------
    # A warm glow that enters from one edge every so many seconds. Blend's
    # opacity doesn't accept time expressions, so the pulse lives inside the
    # layer itself: it's generated black (screen with black = no-op) and only
    # "lights up" during the pulse moments. The sine's high exponent gives
    # short, spaced-out flashes instead of a constant pulse.
    leak = preset.get("light_leak")
    if leak and leak.get("intensity", 0) > 0:
        inten = float(leak["intensity"])
        p_left = float(leak.get("period_left", 23.0))
        p_right = float(leak.get("period_right", 31.0))
        sharp = int(leak.get("sharpness", 14))
        falloff = float(leak.get("falloff", 0.22))
        tint = leak.get("tint", {"rr": 1.0, "gg": 0.42, "bb": 0.16})
        # smooth gradient: generating it at 192px wide and scaling it up is
        # indistinguishable from generating it at 1920 and costs 100x fewer
        # geq evaluations (geq is a per-pixel, per-frame interpreter).
        lw, lh = 192, max(2, int(192 * height / width) // 2 * 2)
        pulse_l = f"pow(max(0,sin({2 * math.pi / p_left:.5f}*T)),{sharp})"
        pulse_r = f"pow(max(0,sin({2 * math.pi / p_right:.5f}*T+1.7)),{sharp})"
        grad_l = f"exp(-X/(W*{falloff}))"
        grad_r = f"exp(-(W-X)/(W*{falloff}))"
        mixer = ":".join(f"{k}={v}" for k, v in tint.items())
        parts.append(
            _pin(f"color=c=black:s={lw}x{lh}:r={fps}", "format=gray",
                 f"geq=lum='255*{inten}*({pulse_l}*{grad_l}+{pulse_r}*{grad_r})'",
                 "format=gbrp", f"colorchannelmixer={mixer}",
                 f"scale={width}:{height}", "gblur=sigma=6") + "[leak]"
        )
        parts.append(
            f"[{cur}][leak]blend=all_mode=screen:all_opacity=1:shortest=1[leaked]"
        )
        cur = "leaked"

    parts.append(f"[{cur}]format=yuv420p[{out_label}]")
    return ";".join(parts)


def film_scratch_params(preset):
    """Scratch-texture parameters requested by this preset (for
    lib_film_texture.ensure_scratch_texture). Returns None if the preset
    doesn't use scratches."""
    scr = preset.get("scratches")
    if not scr or scr.get("opacity", 0) <= 0:
        return None
    return {
        "seconds": float(scr.get("loop_seconds", 6.0)),
        "seed": int(scr.get("seed", 7)),
        "scratch_rate": float(scr.get("scratch_rate", 3.5)),
        "hair_rate": float(scr.get("hair_rate", 0.35)),
        "dust_density": float(scr.get("dust_density", 1.0)),
        "brightness": int(scr.get("brightness", 170)),
    }
