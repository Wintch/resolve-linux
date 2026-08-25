#!/usr/bin/env bash
# Generates lightweight AVIF proxies for all CR2 files in the parent folder.
# Usage: ./01_make_proxies_avif.sh [JOBS]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT_DIR/proxies_avif"
JOBS="${1:-$(nproc)}"

mkdir -p "$OUT_DIR"

export OUT_DIR

process_one() {
  local cr2="$1"
  local base out cfgdir
  base="$(basename "${cr2%.CR2}")"
  out="$OUT_DIR/${base}.avif"

  if [[ -f "$out" ]]; then
    echo "skip  $base (already exists)"
    return 0
  fi

  # Each worker uses its own --configdir: running several darktable-cli
  # instances against a shared ~/.config/darktable causes clashes in
  # library.db/data.db (SQLite) and crashes the process (Trace/breakpoint trap).
  cfgdir="$(mktemp -d "${TMPDIR:-/tmp}/dt_cfg_XXXXXX")"
  trap 'rm -rf "$cfgdir"' RETURN

  if ! darktable-cli "$cr2" "$out" \
      --core \
      --configdir "$cfgdir" \
      --conf plugins/imageio/format/avif/compression_type=1 \
      --conf plugins/imageio/format/avif/quality=80 \
      --conf plugins/imageio/format/avif/bpp=8 \
      >/dev/null 2>&1; then
    echo "FAIL  $base (darktable-cli failed)" >&2
    return 1
  fi

  if [[ ! -f "$out" ]]; then
    echo "FAIL  $base (output file was not generated)" >&2
    return 1
  fi

  if ! exiftool -q -q -TagsFromFile "$cr2" -all:all -overwrite_original "$out"; then
    echo "FAIL  $base (exiftool could not copy metadata)" >&2
    return 1
  fi

  echo "ok    $base"
}
export -f process_one

find "$ROOT_DIR" -maxdepth 1 -iname '*.CR2' -print0 \
  | xargs -0 -P "$JOBS" -I{} bash -c 'process_one "$@"' _ {} || true

n_total=$(find "$ROOT_DIR" -maxdepth 1 -iname '*.CR2' | wc -l)
n_done=$(find "$OUT_DIR" -maxdepth 1 -iname '*.avif' | wc -l)
echo "Done. $n_done/$n_total proxies in: $OUT_DIR"
if [[ "$n_done" -lt "$n_total" ]]; then
  echo "$((n_total - n_done)) files missing. Re-run the script to retry the missing ones (already-existing ones are skipped)." >&2
fi
