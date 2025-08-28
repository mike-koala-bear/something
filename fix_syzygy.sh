#!/usr/bin/env bash
set -euo pipefail

# Adjust this if your TacticalBot folder is elsewhere
ROOT="$(cd "$(dirname "$0")" && pwd)"
SYZ_DIR="$ROOT/syzygy"
BASE_URL="http://tablebase.sesse.net/syzygy/3-4-5"

mkdir -p "$SYZ_DIR"
cd "$SYZ_DIR"

echo "Working directory: $PWD"
echo

# 1) Remove obviously-broken tiny files (< 1 KB)
echo "Removing tiny files (< 1 KB) which are likely error pages..."
find . -maxdepth 1 -type f \( -name "*.rtbw" -o -name "*.rtbz" \) -size -1000c -print -delete || true
echo "Done."
echo

# 2) Build the list of endgame basenames (3-4-5 pieces)
# This is the canonical list used for the 3-4-5 Syzygy set
endgames=(
  # 3-piece
  KPvK KNvK KBvK KRvK KQvK

  # 4-piece
  KPvKP KNvKP KNvKN KBvKP KBvKN KBvKB KRvKP KRvKN KRvKB KRvKR KQvKP KQvKN KQvKB KQvKR KQvKQ

  # 5-piece - no pawns
  KNNvKN KNNvKB KNNvKR KNNvKQ
  KBNvKN KBNvKB KBNvKR KBNvKQ
  KBBvKN KBBvKB KBBvKR KBBvKQ
  KRNvKN KRNvKB KRNvKR KRNvKQ
  KRBvKN KRBvKB KRBvKR KRBvKQ
  KRRvKN KRRvKB KRRvKR KRRvKQ
  KQNvKN KQNvKB KQNvKR KQNvKQ
  KQBvKN KQBvKB KQBvKR KQBvKQ
  KQRvKN KQRvKB KQRvKR KQRvKQ
  KQQvKN KQQvKB KQQvKR KQQvKQ
  KNNNvK KBNNvK KBBNvK KBBBvK KRNNvK KRBNvK KRBBvK KRRNvK KRRBvK KRRRvK KQNNvK KQBNvK KQBBvK KQRNvK KQRBvK KQRRvK KQQNvK KQQBvK KQQRvK KQQQvK

  # 5-piece - with pawns (4 pawns)
  KPPPvKNP KPPPvKBP KPPPvKRP KPPPvKQP
  KNPPvKPP KBPPvKPP KRPPvKPP KQPPvKPP
  KPPPPvKN KPPPPvKB KPPPPvKR KPPPPvKQ
  KNPPPvKP KBPPPvKP KRPPPvKP KQPPPvKP
  KNPPPPvK KBPPPPvK KRPPPPvK KQPPPPvK

  # 5-piece - with pawns (5 pawns)
  KPPPvKPP KPPPPvKP KPPPPPvK
)

# 3) Build urls.txt
URLS_FILE="$SYZ_DIR/urls_3-4-5.txt"
echo "Generating URL list at $URLS_FILE ..."
: > "$URLS_FILE"
for eg in "${endgames[@]}"; do
  for ext in rtbw rtbz; do
    echo "$BASE_URL/${eg}.${ext}" >> "$URLS_FILE"
  done
done
echo "URL list generated with $(wc -l < "$URLS_FILE") entries."
echo

# 4) Download with curl in parallel, resumable, and fail on HTTP errors.
#    We'll use xargs -P to parallelize; adjust -P (jobs) if you want fewer/more.
JOBS=6   # parallel download workers; lower if your network/CPU is limited
echo "Starting downloads (parallel=$JOBS). This may take a while..."
# Only download files that do not exist yet, or that are below a sanity size (1 KB).
# We will iterate through urls and skip those already present and > 1KB.
cat "$URLS_FILE" | while read -r url; do
  fname="${url##*/}"
  if [ -f "$fname" ] && [ "$(stat -f%z "$fname")" -ge 1200 ]; then
    echo "Skipping existing OK file: $fname"
  else
    echo "$url"
  fi
done | xargs -n1 -P"$JOBS" -I{} bash -c 'url="{}"; fname="${url##*/}"; echo "curl -f -L -C - -o \"$fname\" \"$url\""; curl -f -L -C - -o "$fname" "$url" || ( echo "FAILED $url" >&2; exit 0 )'

echo
echo "Download loop finished. Now validating downloads..."

# 5) Validate: report any files still small or missing
echo "Files present (< 1 KB or missing) after download:"
shopt -s nullglob
bad_count=0
for f in *.rtbw *.rtbz; do
  size=$(stat -f%z "$f")
  if [ "$size" -lt 1200 ]; then
    echo "  BAD: $f ($size bytes)"
    bad_count=$((bad_count+1))
  fi
done

# Also check expected list items missing entirely
while read -r url; do
  fname="${url##*/}"
  if [ ! -f "$fname" ]; then
    echo "MISSING: $fname"
    bad_count=$((bad_count+1))
  fi
done < "$URLS_FILE"

if [ "$bad_count" -eq 0 ]; then
  echo "All files downloaded and look OK (size > 1 KB)."
else
  echo "There are $bad_count problematic files. Re-run the script later or try an alternate mirror."
fi

echo
echo "If some files still failed, try again later or change BASE_URL to another mirror (e.g. Sesse)."
echo "Done."