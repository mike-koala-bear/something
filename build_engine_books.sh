#!/usr/bin/env bash
set -euo pipefail

# Paths - adjust as needed
TCEC_PGN="archive.pgn"                     # your TCEC engine matches (plain PGN)
LICHESS_ZST="lichess_elite_2020-07.pgn"    # if .zst, supply full path
OUTDIR="./books"
mkdir -p "$OUTDIR"

# Temporary file names
CLEAN_PGN="$OUTDIR/clean_combined.pgn"     # small-ish intermediate; streaming minimizes size
STREAM_USE_PGN=true                         # if set true, we stream directly to polyglot; else create clean pgn

# Filter params
MIN_ELO=2200   # require both players >= this (engine theory uses strong games)
REQUIRE_BOTH_HIGH=true   # true => both players must be >=MIN_ELO; false => at least one

# Helper: python streaming cleaner (writes to stdout)
cleaner_py=$(mktemp)
cat > "$cleaner_py" <<'PY'
#!/usr/bin/env python3
import sys, re

min_elo = int(sys.argv[1]) if len(sys.argv)>1 else 2200
require_both = (sys.argv[2].lower()=='true') if len(sys.argv)>2 else True

elo_re = re.compile(r'^\[(WhiteElo|BlackElo) "(\d+)"\]')
event_re = re.compile(r'^\[Event "(.*)"\]')

buff = []
white_elo = None
black_elo = None
for line in sys.stdin:
    buff.append(line)
    m = elo_re.match(line)
    if m:
        if m.group(1) == 'WhiteElo':
            white_elo = int(m.group(2))
        else:
            black_elo = int(m.group(2))
    if line.strip() == "":
        keep = False
        if white_elo is None: white_elo = 0
        if black_elo is None: black_elo = 0
        if require_both:
            if white_elo >= min_elo and black_elo >= min_elo:
                keep = True
        else:
            if white_elo >= min_elo or black_elo >= min_elo:
                keep = True
        if keep:
            # export buffered game but stripped of comments/variations/NAGs using simple heuristics:
            # (we rely on chess.pgn export to remove variations/comments is expensive; we do naive strip)
            out = []
            for L in buff:
                # naive remove {} comments and NAGs
                L2 = re.sub(r'\{[^}]*\}', '', L)
                L2 = re.sub(r'\$\d+', '', L2)
                # remove parentheses (variations) content approximately
                while '(' in L2:
                    L2 = re.sub(r'\([^()]*\)', '', L2)
                out.append(L2)
            sys.stdout.write(''.join(out))
            sys.stdout.write("\n")
        # reset
        buff = []
        white_elo = None
        black_elo = None
PY

chmod +x "$cleaner_py"

# Function to stream-decompress input (handles both .zst and plain pgn)
stream_file() {
  local f="$1"
  if [[ "$f" == *.zst ]] || [[ "$f" == *.zstd ]]; then
    zstdcat "$f"
  else
    cat "$f"
  fi
}

echo "=== Step A: create cleaned stream and (optionally) small clean file ==="

# Combine streams: TCEC (engines) first, then lichess elite (human+engine prep)
# We will produce a cleaned stream file to use for polyglot.
if [[ -n "${TCEC_PGN:-}" && -f "$TCEC_PGN" ]]; then
  stream_file "$TCEC_PGN" | python3 "$cleaner_py" "$MIN_ELO" "$REQUIRE_BOTH_HIGH" >> "$CLEAN_PGN"
fi

if [[ -n "${LICHESS_ZST:-}" && -f "$LICHESS_ZST" ]]; then
  stream_file "$LICHESS_ZST" | python3 "$cleaner_py" "$MIN_ELO" "$REQUIRE_BOTH_HIGH" >> "$CLEAN_PGN"
fi

echo "cleaned PGN written to $CLEAN_PGN"

echo "=== Step B: Build Polyglot Books (this may take time) ==="

# 1) Engine theory (deep)
polyglot make-book -pgn "$CLEAN_PGN" -bin "$OUTDIR/engine_theory.bin" -max-ply 32 -min-game 3 || echo "polyglot engine_theory failed"

# 2) Big blitz
polyglot make-book -pgn "$CLEAN_PGN" -bin "$OUTDIR/big_blitz.bin" -max-ply 16 -min-game 8 || echo "polyglot big_blitz failed"

# 3) Bullet
polyglot make-book -pgn "$CLEAN_PGN" -bin "$OUTDIR/bullet.bin" -max-ply 8 -min-game 20 || echo "polyglot bullet failed"

echo "=== Done. Books in $OUTDIR ==="
