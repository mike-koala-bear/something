#!/usr/bin/env python3
# json_to_bin.py
# Usage: python json_to_bin.py draw_book.json draw.bin

import json
import struct
import sys
import chess
import chess.polyglot

PROMO_MAP = {'q': 1, 'r': 2, 'b': 3, 'n': 4}

def uci_to_polyglot(move_uci):
    """
    Build polyglot 16-bit move from a UCI string, using chess.Move to get
    from/to squares (0..63). We place the squares in the order that the
    python-chess polyglot reader expects, so the reader prints the correct move.
    """
    move = chess.Move.from_uci(move_uci)
    from_sq = move.from_square   # 0..63
    to_sq = move.to_square       # 0..63

    # Promotion encoding (0 if not promotion)
    promo = 0
    if move.promotion:
        # chess.PieceType: 1=Pawn,2=Knight,3=Bishop,4=Rook,5=Queen,6=King
        if move.promotion == chess.QUEEN:
            promo = PROMO_MAP['q']
        elif move.promotion == chess.ROOK:
            promo = PROMO_MAP['r']
        elif move.promotion == chess.BISHOP:
            promo = PROMO_MAP['b']
        elif move.promotion == chess.KNIGHT:
            promo = PROMO_MAP['n']

    # IMPORTANT: encode using raw square indices but place them so the reader
    # reconstructs the correct move. Many readers expect lower 6 bits = to_sq,
    # next 6 bits = from_sq. So we use (to | (from<<6) | (promo<<12)).
    poly_move = (to_sq) | (from_sq << 6) | (promo << 12)
    return poly_move

def main():
    if len(sys.argv) != 3:
        print("Usage: python json_to_bin.py input.json output.bin")
        sys.exit(1)

    json_file = sys.argv[1]
    bin_file = sys.argv[2]

    with open(json_file, 'r', encoding='utf-8') as f:
        book = json.load(f)

    entries = []
    for fen, moves in book.items():
        board = chess.Board(fen)
        key = chess.polyglot.zobrist_hash(board)
        for uci in moves:
            # sanity-check the move is legal in this position
            try:
                mv = board.parse_uci(uci)
            except Exception:
                # still produce an entry but warn
                print(f"Warning: move {uci} may be invalid in FEN: {fen}")
            poly_move = uci_to_polyglot(uci)
            weight = 1
            learn = 0
            entries.append((key, poly_move, weight, learn))

    # write big-endian as Polyglot expects
    with open(bin_file, 'wb') as f:
        for key, move, weight, learn in entries:
            f.write(struct.pack('>QHHI', key, move, weight, learn))

    print(f"Polyglot book written to {bin_file}")

if __name__ == "__main__":
    main()
