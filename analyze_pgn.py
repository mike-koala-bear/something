#!/usr/bin/env python3
import chess.pgn
import chess.engine
import sys

if len(sys.argv) < 2:
    print("Usage: analyze_pgn.py input.pgn > output.pgn")
    sys.exit(1)

input_file = sys.argv[1]

engine_path = "stockfish"  # adjust if needed
depth = 30                 # engine depth

engine = chess.engine.SimpleEngine.popen_uci(engine_path)

with open(input_file) as pgn:
    game_no = 0
    while True:
        game = chess.pgn.read_game(pgn)
        if game is None:
            break
        game_no += 1

        board = game.board()
        new_game = chess.pgn.Game()
        new_game.headers = game.headers.copy()

        node = new_game
        for move in game.mainline_moves():
            # Query engine for best move
            result = engine.analyse(board, chess.engine.Limit(depth=depth))
            best = result["pv"][0]
            if best not in board.legal_moves:
                break  # safety
            board.push(best)
            node = node.add_variation(best)

        print(new_game, file=sys.stdout, end="\n\n")

engine.quit()
