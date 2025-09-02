import chess
import chess.pgn
import json

# Input PGN file
pgn_file = "staygame.pgn"

# Output draw book JSON
book_file = "draw_book.json"

# Dictionary to store the book
# key = FEN (board position), value = list of UCI moves that lead to draws
draw_book = {}

with open(pgn_file) as pgn:
    while True:
        game = chess.pgn.read_game(pgn)
        if game is None:
            break
        if game.headers["Result"] != "1/2-1/2":
            continue  # only include draws

        board = game.board()
        for move in game.mainline_moves():
            fen = board.fen()
            uci = move.uci()
            if fen not in draw_book:
                draw_book[fen] = []
            if uci not in draw_book[fen]:
                draw_book[fen].append(uci)
            board.push(move)

# Save the draw book as JSON
with open(book_file, "w") as f:
    json.dump(draw_book, f, indent=2)

print(f"Draw book saved to {book_file}")
