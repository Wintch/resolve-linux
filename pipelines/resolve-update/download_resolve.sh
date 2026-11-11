#!/usr/bin/env bash
# Downloads the DaVinci Resolve (Free or Studio) Linux installer from Blackmagic's support site,
# through the same "download only" request the site's own button makes: no account and no
# registration data are sent. Runs as any user; nothing here needs root.
#
# Usage: ./download_resolve.sh [--studio|--free] [--version X.Y[.Z]] [--list] [--dry-run] [DEST_DIR]
#   --studio      (default) DaVinci Resolve Studio; --free for the free edition
#   --version     a specific release (e.g. 21.1.2); default: the newest Linux release listed
#   --list        print the Linux releases the site lists and exit
#   --dry-run     resolve the release, request the link and print the file and its size; no download
#   DEST_DIR      where the .zip goes (default: current directory). The .zip is ~11 GB and the
#                 .run inside it about the same: pick a disk with room for both, plus the
#                 makeresolvedeb build (see install_resolve.sh).
#
# The download resumes (curl -C -) if interrupted, as long as the signed link is still valid;
# rerun the script for a fresh link. The .zip holds the .run and Blackmagic's install notes.
set -euo pipefail

PRODUCT="davinci-resolve-studio"
VERSION=""
LIST=0
DRY=0
DEST="."
while [[ $# -gt 0 ]]; do
    case "$1" in
        --studio) PRODUCT="davinci-resolve-studio" ;;
        --free) PRODUCT="davinci-resolve" ;;
        --version) VERSION="${2:?--version needs a value}"; shift ;;
        --list) LIST=1 ;;
        --dry-run) DRY=1 ;;
        -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
        -*) echo "unknown option $1" >&2; exit 1 ;;
        *) DEST="$1" ;;
    esac
    shift
done

UA="Mozilla/5.0 (X11; Linux x86_64)"
SITE="https://www.blackmagicdesign.com"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

curl -fsS -A "$UA" -o "$TMP/downloads.json" "$SITE/api/support/us/downloads.json"

# one line per Linux release of the chosen product: "<major.minor.release>\t<downloadId>\t<name>", newest first
python3 -I - "$TMP/downloads.json" "$PRODUCT" > "$TMP/releases.tsv" <<'EOF'
import json, sys
data, product = json.load(open(sys.argv[1])), sys.argv[2]
rows = {}
for d in data.get("downloads", []):
    for u in (d.get("urls") or {}).get("Linux") or []:
        if u.get("product") != product or not u.get("downloadId"):
            continue
        ver = (int(u.get("major") or 0), int(u.get("minor") or 0), int(u.get("releaseNum") or 0))
        if "beta" in (d.get("name") or "").lower():
            continue
        rows.setdefault(ver, (u["downloadId"], d.get("name", "")))
for ver in sorted(rows, reverse=True):
    print("%d.%d.%d\t%s\t%s" % (ver + rows[ver]))
EOF

if [[ ! -s "$TMP/releases.tsv" ]]; then
    echo "No Linux release of $PRODUCT found in the support listing." >&2
    exit 1
fi
if [[ "$LIST" == 1 ]]; then
    cut -f1,3 "$TMP/releases.tsv"
    exit 0
fi

if [[ -z "$VERSION" ]]; then
    LINE="$(head -1 "$TMP/releases.tsv")"
else
    # 21.2 matches 21.2.0; 21.1.2 matches exactly
    WANT="$VERSION"; [[ "$WANT" =~ ^[0-9]+\.[0-9]+$ ]] && WANT="$WANT.0"
    LINE="$(awk -F'\t' -v v="$WANT" '$1 == v' "$TMP/releases.tsv" | head -1)"
    [[ -n "$LINE" ]] || { echo "No Linux $PRODUCT $VERSION listed. Available:" >&2; cut -f1 "$TMP/releases.tsv" >&2; exit 1; }
fi
REL="$(cut -f1 <<<"$LINE")"; ID="$(cut -f2 <<<"$LINE")"; NAME="$(cut -f3 <<<"$LINE")"
echo "Release: $NAME ($REL), download id $ID"

URL="$(curl -fsS -A "$UA" -X POST \
    -H "Content-Type: application/json;charset=UTF-8" -H "Accept: application/json, text/plain, */*" \
    -H "Origin: $SITE" -H "Referer: $SITE/support/family/davinci-resolve-and-fusion" \
    -d '{"platform":"Linux","policy":true,"downloadOnly":true,"origin":"www.blackmagicdesign.com","country":"us"}' \
    "$SITE/api/register/us/download/$ID")"
if [[ ! "$URL" =~ ^https://sw\.blackmagicdesign\.com/ ]]; then
    echo "Unexpected answer to the download request: ${URL:0:200}" >&2
    exit 1
fi

FILE="$(basename "${URL%%\?*}")"
mkdir -p "$DEST"
SIZE="$(curl -fsSI -A "$UA" "$URL" | awk 'tolower($1) == "content-length:" {print $2+0}')"
AVAIL="$(df --output=avail -B1 "$DEST" | tail -1)"
echo "File: $FILE, $(numfmt --to=iec "${SIZE:-0}"); free in $DEST: $(numfmt --to=iec "$AVAIL")"
[[ "$DRY" == 1 ]] && exit 0
if [[ -n "$SIZE" ]] && (( SIZE * 2 > AVAIL )); then
    echo "Not enough room for the .zip and the extracted .run (about twice the size)." >&2
    exit 1
fi

curl -f -C - -A "$UA" -o "$DEST/$FILE" "$URL"
GOT="$(stat -c %s "$DEST/$FILE")"
if [[ -n "$SIZE" && "$GOT" != "$SIZE" ]]; then
    echo "Size mismatch: got $GOT bytes, expected $SIZE. Rerun to resume." >&2
    exit 1
fi
echo "Downloaded $DEST/$FILE ($GOT bytes). Next: unzip it, then install_resolve.sh with the .run."
