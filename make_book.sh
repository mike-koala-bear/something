#!/usr/bin/env bash
set -euo pipefail

EPD="$1"    # e.g. ./books/mybook/book.epd
OUT="$2"    # e.g. ./books/mybook/book.bin

if [ -z "$EPD" ] || [ -z "$OUT" ]; then
  echo "Usage: $0 <book.epd> <out.bin>"
  exit 1
fi

# Option A: using polyglot (if installed)
if command -v polyglot >/dev/null 2>&1; then
  echo "[make_book] Using polyglot to make book"
  polyglot make-book -o "$OUT" "$EPD"
  echo "[make_book] wrote $OUT"
  exit 0
fi

# Option B: using pgn-extract -> polyglot route (if you have pgn-extract and polyglot-make)
if command -v pgn-extract >/dev/null 2>&1 && command -v polyglot >/dev/null 2>&1; then
  echo "[make_book] Using pgn-extract + polyglot"
  TMPPGN="$(mktemp /tmp/book.XXXX.pgn)"
  # Convert epd->pgn line-by-line (some tools can do direct conversions); this is a fallback sketch:
  awk -F' bm ' '{ print $1 }' "$EPD" > "$TMPPGN"
  polyglot make-book -o "$OUT" "$TMPPGN"
  rm -f "$TMPPGN"
  echo "[make_book] wrote $OUT"
  exit 0
fi

echo "No polyglot or pgn-extract found. Install a book compiler (polyglot) and re-run."
exit 2
