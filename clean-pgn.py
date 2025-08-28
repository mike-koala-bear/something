import chess.pgn
import sys

input_file = "archive.pgn"
output_file = "clean_tcec.pgn"

with open(input_file) as pgn, open(output_file, "w") as out:
    while True:
        game = chess.pgn.read_game(pgn)
        if game is None:
            break
        game.accept(chess.pgn.StringExporter(headers=True, variations=False, comments=False))
        out.write(str(game) + "\n\n")

print("Clean PGN written to", output_file)
