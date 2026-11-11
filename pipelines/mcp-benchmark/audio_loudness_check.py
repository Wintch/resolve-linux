#!/usr/bin/env python3
"""
Validate an exported audio/video file against commonly-cited YouTube loudness
guidance: integrated loudness around -14 LUFS, true peak at or below -1 dBTP
(headroom against YouTube's own loudness normalization and to avoid
inter-sample clipping after their transcode), and a sane loudness range (LRA)
so the mix isn't wildly over/under-compressed either.

Why this isn't a Resolve scripting call: searched the full scripting API
surface (see README's MCP section) for a loudness/peak *measurement* getter --
there isn't one. Resolve can SET a normalization *target*
(Timeline.NormalizeAudioLevel + NormalizeAudioOptions.targetLoudness) but
exposes no way to read back the MEASURED loudness or true peak of a mix, on
either MCP server (both proxy the same underlying scripting API, so this
isn't a native-vs-community gap). Measuring the actual exported file with
ffmpeg's loudnorm filter is the correct place for this check anyway -- it
validates the real deliverable, not Resolve's internal state, which is
exactly what "usarlo de validacion al exportar" calls for.

Run: python3 audio_loudness_check.py <path-to-audio-or-video-file>
Self-test (no Resolve, no real project needed): python3 audio_loudness_check.py --selftest
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

TARGET_LUFS = -14.0
LUFS_TOLERANCE = 1.0  # +/-1 LU is the tolerance commonly cited alongside the -14 LUFS target
MAX_TRUE_PEAK_DBTP = -1.0
MAX_LRA = 20.0  # sanity ceiling: above this the mix is inconsistent, not just "unnormalized"


def measure(path: str) -> dict:
    cmd = [
        "ffmpeg", "-i", path, "-af",
        f"loudnorm=I={TARGET_LUFS}:TP={MAX_TRUE_PEAK_DBTP}:LRA=11:print_format=json",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    json_start = proc.stderr.rfind("{")
    if json_start == -1:
        raise RuntimeError(f"ffmpeg produced no loudnorm measurement:\n{proc.stderr[-2000:]}")
    # ffmpeg prints more stderr (muxing summary, timing) after the JSON block,
    # so parse just the one object instead of assuming it runs to end-of-string.
    obj, _ = json.JSONDecoder().raw_decode(proc.stderr[json_start:])
    return obj


def evaluate(measurement: dict) -> dict:
    integrated = float(measurement["input_i"])
    true_peak = float(measurement["input_tp"])
    lra = float(measurement["input_lra"])
    checks = {
        "integrated_lufs": integrated,
        "true_peak_dbtp": true_peak,
        "loudness_range_lu": lra,
        "integrated_ok": abs(integrated - TARGET_LUFS) <= LUFS_TOLERANCE,
        "true_peak_ok": true_peak <= MAX_TRUE_PEAK_DBTP,
        "lra_ok": lra <= MAX_LRA,
    }
    checks["pass"] = checks["integrated_ok"] and checks["true_peak_ok"] and checks["lra_ok"]
    return checks


def check_file(path: str) -> dict:
    return evaluate(measure(path))


def _make_test_tone(path: Path, target_lufs: float, clip: bool = False) -> None:
    if clip:
        # deliberately hot: pushed well over the -14 LUFS target (limiter keeps
        # true peak in range, so this specifically exercises the loudness
        # check failing on its own, independent of the peak check)
        filt = "volume=12dB,alimiter=limit=0.99:attack=1:release=1"
    else:
        filt = f"loudnorm=I={target_lufs}:TP=-1.5:LRA=7"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=1000:duration=5",
            "-af", filt, str(path),
        ],
        check=True, capture_output=True,
    )


def selftest() -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        good = Path(tmp) / "good.wav"
        bad = Path(tmp) / "bad.wav"
        _make_test_tone(good, TARGET_LUFS, clip=False)
        _make_test_tone(bad, TARGET_LUFS, clip=True)

        good_result = check_file(str(good))
        bad_result = check_file(str(bad))

        print("good.wav (normalized to -14 LUFS):")
        print(json.dumps(good_result, indent=2))
        print("\nbad.wav (hot + hard-limited):")
        print(json.dumps(bad_result, indent=2))

        ok = good_result["pass"] and not bad_result["pass"]
        print(f"\nSELFTEST {'PASSED' if ok else 'FAILED'} "
              f"(expected good.wav to pass and bad.wav to fail)")
        return ok


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    if sys.argv[1] == "--selftest":
        sys.exit(0 if selftest() else 1)

    result = check_file(sys.argv[1])
    print(json.dumps(result, indent=2))
    if not result["pass"]:
        print("FAIL -- outside YouTube-recommended range", file=sys.stderr)
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
