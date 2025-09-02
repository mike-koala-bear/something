#!/usr/bin/env python3
# read_poly.py
# Usage: python read_poly.py draw.bin

import sys
import chess
import chess.polyglot

if len(sys.argv) != 2:
    print("Usage: python read_poly.py draw.bin")
    sys.exit(1)

book_file = sys.argv[1]

with chess.polyglot.open_reader(book_file) as reader:
    for entry in reader:
        # entry.move is already a chess.Move object
        move = entry.move
        # print uci (move.uci()) so you see 'e2e4' style
        print(f"Move: {move.uci()}, Weight: {entry.weight}, Learn: {entry.learn}")
