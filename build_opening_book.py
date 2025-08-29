#!/usr/bin/env python3
"""
build_opening_book.py

Create a high-quality opening book from PGN files.

Outputs:
 - book.json  : full opening tree + stats
 - book.csv   : one-line per position with best move and stats
 - book.epd   : EPD-like file with 'bm' token for selected best move (convertible to polyglot)

Usage example:
  python3 build_opening_book.py \
     --pgn lichess_db_standard_rated_2024-12.pgn \
     --min-elo 2500 --min-moves 20 --max-ply 24 \
     --min-games-per-pos 6 \
     --engine ./engines/stockfish_17.1_macos_m1 --engine-depth 18 --engine-threshold 100 \
     --out-dir ./books/mybook --verbose
"""

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from typing import Dict, List, Optional

import chess
import chess.pgn

try:
    import chess.engine
except Exception:
    # if engine verification is not used, the script still works
    chess = chess

def parse_args():
    p = argparse.ArgumentParser(description="Build opening book from PGNs (with optional Stockfish verification).")
    p.add_argument("--pgn", nargs="+", required=True, help="One or more PGN files (space separated).")
    p.add_argument("--min-elo", type=int, default=2500, help="Minimum Elo (both players) to include a game.")
    p.add_argument("--min-moves", type=int, default=20, help="Minimum half-moves in a game to include.")
    p.add_argument("--max-ply", type=int, default=24, help="Maximum ply depth to record openings (half-moves).")
    p.add_argument("--min-games-per-pos", type=int, default=6, help="Minimum games at a position to keep it in the book.")
    p.add_argument("--out-dir", default="./books", help="Output directory for book.json / book.csv / book.epd")
    p.add_argument("--engine", default=None, help="Path to Stockfish binary for verification (optional).")
    p.add_argument("--engine-depth", type=int, default=18, help="Depth for engine verification (if engine enabled).")
    p.add_argument("--engine-threshold", type=int, default=100, help="Centipawn threshold to reject moves (if engine better by > threshold).")
    p.add_argument("--min-frequency", type=int, default=3, help="Minimum frequency of a candidate move at a position to be considered.")
    p.add_argument("--prune-by-score", type=float, default=0.0, help="Require candidate move to have winrate >= this fraction (0-1).")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()

class PosStats:
    def __init__(self):
        self.total = 0
        self.moves = defaultdict(lambda: {"count": 0, "white_win": 0, "black_win": 0, "draw": 0})

    def record(self, move_uci: str, result: str):
        self.total += 1
        m = self.moves[move_uci]
        m["count"] += 1
        if result == "1-0":
            m["white_win"] += 1
        elif result == "0-1":
            m["black_win"] += 1
        else:
            m["draw"] += 1

def read_games(pgn_paths: List[str], min_elo: int, min_moves: int, verbose: bool = False):
    for path in pgn_paths:
        if verbose:
            print(f"[INFO] Reading PGN: {path}", file=sys.stderr)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            while True:
                game = chess.pgn.read_game(f)
                if game is None:
                    break
                try:
                    white_elo = int(game.headers.get("WhiteElo", "0"))
                    black_elo = int(game.headers.get("BlackElo", "0"))
                except Exception:
                    white_elo = black_elo = 0
                if white_elo < min_elo or black_elo < min_elo:
                    continue
                if game.headers.get("Variant", "Standard") != "Standard":
                    continue
                moves = 0
                node = game
                while not node.is_end():
                    node = node.variation(0)
                    moves += 1
                if moves < min_moves:
                    continue
                yield game

def build_tree_from_pgns(pgn_paths: List[str], min_elo: int, min_moves: int, max_ply: int, verbose: bool):
    positions: Dict[str, PosStats] = {}
    games_read = 0
    for game in read_games(pgn_paths, min_elo, min_moves, verbose):
        games_read += 1
        result = game.headers.get("Result", "*")
        board = game.board()
        ply = 0
        node = game
        while not node.is_end() and ply < max_ply:
            node = node.variation(0)
            move = node.move
            fen = board.fen()
            uci = move.uci()
            if fen not in positions:
                positions[fen] = PosStats()
            positions[fen].record(uci, result)
            board.push(move)
            ply += 1
        if verbose and games_read % 5000 == 0:
            print(f"[INFO] processed {games_read} games, positions collected {len(positions)}", file=sys.stderr)
    if verbose:
        print(f"[INFO] Finished reading PGNs: {games_read} games processed, {len(positions)} unique positions", file=sys.stderr)
    return positions

def choose_best_moves(positions: Dict[str, PosStats], min_games_per_pos: int, min_frequency: int, prune_by_score: float, verbose: bool):
    out = {}
    for fen, stats in positions.items():
        if stats.total < min_games_per_pos:
            continue
        candidates = []
        for move, mstats in stats.moves.items():
            if mstats["count"] < min_frequency:
                continue
            wins = mstats["white_win"]
            losses = mstats["black_win"]
            draws = mstats["draw"]
            games = mstats["count"]
            board = chess.Board(fen)
            if board.turn == chess.WHITE:
                perf = (wins + 0.5 * draws) / games
            else:
                perf = (losses + 0.5 * draws) / games
            weight = perf * math.log(1 + games)
            candidates.append((move, games, perf, weight, mstats))
        if not candidates:
            continue
        candidates.sort(key=lambda x: (x[3], x[1]), reverse=True)
        best = candidates[0]
        if best[2] < prune_by_score:
            continue
        out[fen] = {
            "best_move": best[0],
            "games": best[1],
            "perf": best[2],
            "weight": best[3],
            "raw": best[4]
        }
    if verbose:
        print(f"[INFO] Selected best moves for {len(out)} positions (after min_games filter)", file=sys.stderr)
    return out

def verify_with_engine(engine_path: str, positions: Dict[str, dict], depth: int, threshold_cp: int, verbose: bool) -> Dict[str, dict]:
    try:
        engine = chess.engine.SimpleEngine.popen_uci(engine_path)
    except Exception as e:
        print(f"[ERROR] Could not launch engine at {engine_path}: {e}", file=sys.stderr)
        return positions

    verified = {}
    total = len(positions)
    idx = 0
    for fen, data in positions.items():
        idx += 1
        board = chess.Board(fen)
        candidate = data["best_move"]
        try:
            info = engine.analyse(board, chess.engine.Limit(depth=depth))
            best_pv = info.get("pv") if isinstance(info, dict) else None
            engine_best = best_pv[0] if best_pv else None
            engine_score = info.get("score")
        except Exception as e:
            if verbose:
                print(f"[WARN] engine analyse failed at pos {idx}/{total}: {e}", file=sys.stderr)
            verified[fen] = {**data, "verified": True, "engine_best": None, "engine_score": None}
            continue

        score_cp = None
        try:
            if engine_score is not None:
                if engine_score.is_mate():
                    score_cp = 100000 if engine_score.white().mate() > 0 else -100000
                else:
                    score_cp = engine_score.white().score(mate_score=100000)
        except Exception:
            score_cp = None

        keep = True
        if engine_best and candidate != engine_best.uci():
            if score_cp is not None and abs(score_cp) > threshold_cp:
                keep = False

        verified[fen] = {**data, "verified": keep, "engine_best": engine_best.uci() if engine_best else None, "engine_score": score_cp}
        if verbose and idx % 200 == 0:
            print(f"[VERIFY] processed {idx}/{total} positions", file=sys.stderr)

    try:
        engine.quit()
    except Exception:
        pass
    return verified

def write_outputs(selected: Dict[str, dict], out_dir: str, verbose: bool):
    os.makedirs(out_dir, exist_ok=True)
    jpath = os.path.join(out_dir, "book.json")
    cpath = os.path.join(out_dir, "book.csv")
    epdpath = os.path.join(out_dir, "book.epd")

    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(selected, f, indent=2)
    if verbose:
        print(f"[OUT] wrote {jpath}", file=sys.stderr)

    import csv
    with open(cpath, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["fen", "best_move", "games", "perf", "weight", "verified", "engine_best", "engine_score"])
        for fen, data in selected.items():
            writer.writerow([fen, data.get("best_move"), data.get("games"), data.get("perf"), data.get("weight"),
                             data.get("verified"), data.get("engine_best"), data.get("engine_score")])
    if verbose:
        print(f"[OUT] wrote {cpath}", file=sys.stderr)

    with open(epdpath, "w", encoding="utf-8") as f:
        for fen, data in selected.items():
            move = data.get("best_move")
            meta = f"g={data.get('games',0)};p={data.get('perf',0):.3f};v={data.get('verified', False)}"
            line = f"{fen} bm {move}; id \"{meta}\";\n"
            f.write(line)
    if verbose:
        print(f"[OUT] wrote {epdpath}", file=sys.stderr)

    print(f"[DONE] outputs: {jpath}, {cpath}, {epdpath}")

def main():
    args = parse_args()
    if args.verbose:
        print("[INFO] Starting build_opening_book", file=sys.stderr)
        print(f"[INFO] args: {args}", file=sys.stderr)

    positions = build_tree_from_pgns(args.pgn, args.min_elo, args.min_moves, args.max_ply, args.verbose)
    selected = choose_best_moves(positions, args.min_games_per_pos, args.min_frequency, args.prune_by_score, args.verbose)

    if args.engine:
        if args.verbose:
            print(f"[INFO] Verifying {len(selected)} positions with engine {args.engine} (depth {args.engine_depth})", file=sys.stderr)
        selected = verify_with_engine(args.engine, selected, args.engine_depth, args.engine_threshold, args.verbose)
        # Optionally drop unverified positions:
        # selected = {fen:data for fen,data in selected.items() if data.get("verified",False)}

    write_outputs(selected, args.out_dir, args.verbose)

if __name__ == "__main__":
    main()
