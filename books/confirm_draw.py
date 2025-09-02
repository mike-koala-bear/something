import json
import chess

# Load your JSON book
with open("draw_book.json") as f:
    book = json.load(f)

for fen, moves in book.items():  # fen is the key, moves is a list
    board = chess.Board(fen)
    print("Position:\n", board)
    print("Draw-safe moves:", moves)
    print("-" * 30)
