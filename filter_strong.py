#!/usr/bin/env python3
# filter_strong.py <min_elo> <require_both>
# Example: python3 filter_strong.py 2400 true
import sys, re

min_elo = int(sys.argv[1]) if len(sys.argv) > 1 else 2400
require_both = (sys.argv[2].lower() == 'true') if len(sys.argv) > 2 else True

elo_re = re.compile(r'^\[(WhiteElo|BlackElo) "(\d+)"\]')
buff = []
white_elo = None
black_elo = None

for line in sys.stdin:
    buff.append(line)
    m = elo_re.match(line)
    if m:
        if m.group(1) == "WhiteElo":
            white_elo = int(m.group(2))
        else:
            black_elo = int(m.group(2))
    if line.strip() == "":
        we = white_elo or 0
        be = black_elo or 0
        keep = (we >= min_elo and be >= min_elo) if require_both else (we >= min_elo or be >= min_elo)
        if keep:
            sys.stdout.write(''.join(buff))
            sys.stdout.write("\n")  # preserve empty line between games
        buff = []
        white_elo = None
        black_elo = None
