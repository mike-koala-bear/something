# pylint: disable=unused-variable, unused-argument
#!/usr/bin/env python3
import chess
import chess.engine
import chess.pgn
import time
import sys

ENGINE_PATH = "stockfish"   # adjust if needed
DEPTH = 30
MAX_PLIES = 40
BRANCHING = 1  # future extension

def format_score(score):
    if score.is_mate():
        return f"# {score.mate()}"
    else:
        return f"{score.score()/100:.2f}"

def main():
    board = chess.Board()
    start_time = time.time()
    ply = 0

    with chess.engine.SimpleEngine.popen_uci(ENGINE_PATH) as engine:
        while ply < MAX_PLIES and not board.is_game_over():
            result = engine.analyse(board, chess.engine.Limit(depth=DEPTH))
            best_move = result["pv"][0]
            score = format_score(result["score"].pov(board.turn))

            # Get SAN *before* making the move
            san = board.san(best_move)

            board.push(best_move)
            ply += 1

            elapsed = time.time() - start_time
            print(f"[ply {ply}/{MAX_PLIES}] {san} ({best_move.uci()}) "
                  f"eval={score} elapsed={elapsed:.1f}s", file=sys.stderr)

        # dump PGN for the whole game at once
        game = chess.pgn.Game.from_board(board)
        print(game, flush=True)

if __name__ == "__main__":
    main()
