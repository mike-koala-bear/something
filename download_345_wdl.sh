#!/usr/bin/env bash
set -euo pipefail

SYZ_DIR="./syzygy"
BASE="https://tablebase.lichess.ovh/tables/standard/3-4-5-wdl"
mkdir -p "$SYZ_DIR"
cd "$SYZ_DIR"

echo "Generating URL list for 3-4-5 WDL Syzygy..."
# Download the remote file list
curl -s "$BASE/" | grep -Eo '3-4-5/[^"]+\.(rtbw|rtbz)' | awk "{print \"${BASE}/\" \$1}" > urls.txt
echo "Found $(wc -l < urls.txt) files to download."

echo "Downloading missing files..."
xargs -n1 -P4 curl -C - -O < urls.txt

echo "Download complete. Verifying file sizes..."
bad=0
for f in *.rtbw *.rtbz; do
  if [ "$(stat -f%z "$f")" -lt 1200 ]; then
    echo "BAD or missing: $f"
    bad=$((bad+1))
  fi
done

if [ "$bad" -eq 0 ]; then
  echo "All files downloaded successfully!"
else
  echo "$bad files are bad or missing. Re-run script to re-download them."
fi
