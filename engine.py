# pylint: disable=unused-variable, unused-argument
# pylint: disable=broad-exception-caught
# engine.py
# Stronger BotLi engine wrapper with tactical boosting and deeper fallback.
# - auto-tunes Threads/Hash (if auto_tune enabled)
# - detects tactical positions (captures/promotions) and increases think time
# - if engine returns shallow depth, performs a short deeper analyse and uses that move if better
# - logs engine info to stderr for diagnostics
#
# Tested against python-chess UCI API.

import asyncio
import os
import subprocess
import sys
import traceback
from typing import Optional, Tuple

import chess
import chess.engine

from configs import Engine_Config, Limit_Config, Syzygy_Config


def _detect_system_resources() -> dict:
    cpu = os.cpu_count() or None
    ram_mb = None
    try:
        if sys.platform == "darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], stderr=subprocess.DEVNULL)
            ram_mb = int(out.strip()) // (1024 * 1024)
        else:
            if os.path.exists("/proc/meminfo"):
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            ram_kb = int(line.split()[1])
                            ram_mb = ram_kb // 1024
                            break
    except Exception:
        ram_mb = None
    return {"cpu_count": cpu, "total_ram_mb": ram_mb}


def _recommend_threads_and_hash(role: str = "standard", cpu_count: Optional[int] = None, total_ram_mb: Optional[int] = None) -> tuple[int, int]:  # pylint: disable=line-too-long
    cpu = cpu_count or os.cpu_count() or 4
    ram = total_ram_mb or 8192
    usable = max(1, cpu - 1)
    if role == "bullet":
        threads = min(usable, 4)
        hash_mb = 256 if ram <= 4096 else (512 if ram <= 8192 else 1024)
    else:
        threads = min(usable, 7)
        if ram <= 4096:
            hash_mb = 1024
        elif ram <= 8192:
            hash_mb = 3072
        else:
            hash_mb = 4096
    return int(threads), int(hash_mb)


class Engine:
    def __init__(self,
                 transport: Optional[asyncio.SubprocessTransport],
                 engine: Optional[chess.engine.UciProtocol],
                 ponder: bool,
                 opponent: Optional[chess.engine.Opponent],
                 limit_config: Limit_Config,
                 move_overhead_multiplier: float = 1.0,
                 auto_tune: bool = True) -> None:
        self.transport = transport
        self.engine = engine
        self.ponder = ponder
        self.opponent = opponent
        self.limit_config = limit_config
        self.move_overhead_multiplier = move_overhead_multiplier
        self.auto_tune = auto_tune

        self._ponder_handle: Optional[chess.engine.AnalysisResult] = None
        self._ponder_task: Optional[asyncio.Task] = None

        self._resources = _detect_system_resources()

    @classmethod
    async def from_config(cls,
                          engine_config: Engine_Config,
                          syzygy_config: Syzygy_Config,
                          opponent: chess.engine.Opponent) -> "Engine":
        stderr = subprocess.DEVNULL if engine_config.silence_stderr else None
        transport, engine = await chess.engine.popen_uci(engine_config.path, stderr=stderr)
        inst = cls(transport, engine, engine_config.ponder, opponent, engine_config.limits,
                   getattr(engine_config, "move_overhead_multiplier", 1.0),
                   getattr(engine_config, "auto_tune", True))
        await inst._configure_engine(engine, engine_config, syzygy_config)
        try:
            await engine.send_opponent_information(opponent=opponent)
        except Exception:
            pass
        return inst

    @classmethod
    async def from_dual_config(cls,
                               bullet_config: Engine_Config,
                               standard_config: Engine_Config,
                               syzygy_config: Syzygy_Config,
                               opponent: chess.engine.Opponent,
                               base_seconds: float,
                               inc_seconds: float) -> "Engine":
        dummy = cls(None, None, False, opponent, standard_config.limits,
                    getattr(standard_config, "move_overhead_multiplier", 1.0),
                    getattr(standard_config, "auto_tune", True))
        tc_cat = dummy.classify_tc(base_seconds, inc_seconds)
        chosen = bullet_config if tc_cat in ("bullet", "hyperbullet") else standard_config
        stderr = subprocess.DEVNULL if chosen.silence_stderr else None
        transport, engine = await chess.engine.popen_uci(chosen.path, stderr=stderr)
        inst = cls(transport, engine, chosen.ponder, opponent, chosen.limits,
                   getattr(chosen, "move_overhead_multiplier", 1.0),
                   getattr(chosen, "auto_tune", True))
        await inst._configure_engine(engine, chosen, syzygy_config)
        try:
            await engine.send_opponent_information(opponent=opponent)
        except Exception:
            pass
        return inst

    @classmethod
    async def test(cls, engine_config: Engine_Config) -> None:
        stderr = subprocess.DEVNULL if engine_config.silence_stderr else None
        transport, engine = await chess.engine.popen_uci(engine_config.path, stderr=stderr)
        temp = cls(transport, engine, False, None, engine_config.limits,
                   getattr(engine_config, "move_overhead_multiplier", 1.0),
                   getattr(engine_config, "auto_tune", True))
        dummy_syzygy = Syzygy_Config(False, [], 0, False)
        await temp._configure_engine(engine, engine_config, dummy_syzygy)
        result = await engine.play(chess.Board(), chess.engine.Limit(time=0.1))
        if not result.move:
            raise RuntimeError("Engine couldn't make a move")
        try:
            await engine.quit()
        finally:
            transport.close()

    async def _configure_engine(self, engine: chess.engine.UciProtocol,
                                engine_config: Engine_Config,
                                syzygy_config: Syzygy_Config) -> None:
        cpu = self._resources.get("cpu_count")
        ram = self._resources.get("total_ram_mb")
        role = "bullet" if self.classify_tc(self.limit_config.time or 0, 0) in ("bullet", "hyperbullet") else "standard"  # pylint: disable=line-too-long

        threads_provided = any(k.lower() == "threads" for k in engine_config.uci_options.keys())
        hash_provided = any(k.lower() == "hash" for k in engine_config.uci_options.keys())

        if self.auto_tune and (not threads_provided or not hash_provided):
            rec_threads, rec_hash = _recommend_threads_and_hash(role, cpu, ram)
            if not threads_provided:
                engine_config.uci_options.setdefault("Threads", rec_threads)
            if not hash_provided:
                engine_config.uci_options.setdefault("Hash", rec_hash)

        if "MultiPV" not in engine_config.uci_options and "MultiPV" in engine.options:
            engine_config.uci_options.setdefault("MultiPV", 1)

        for name, value in engine_config.uci_options.items():
            if name.lower() in chess.engine.MANAGED_OPTIONS:
                continue
            if name in engine.options:
                try:
                    await engine.configure({name: value})
                except Exception:
                    print(f"[Engine configure] couldn't set {name} -> {value}", file=sys.stderr)
            else:
                print(f"[Engine configure] option {name} not supported by engine", file=sys.stderr)

        if syzygy_config.enabled:
            if "SyzygyPath" in engine.options and "SyzygyPath" not in engine_config.uci_options:
                delim = ";" if os.name == "nt" else ":"
                try:
                    await engine.configure({"SyzygyPath": delim.join(syzygy_config.paths)})
                except Exception:
                    print("Failed to set SyzygyPath", file=sys.stderr)
            if "SyzygyProbeDepth" in engine.options and "SyzygyProbeDepth" not in engine_config.uci_options:
                try:
                    await engine.configure({"SyzygyProbeDepth": getattr(syzygy_config, "probe_depth", 1)})
                except Exception:
                    pass
            if "SyzygyProbeLimit" in engine.options and "SyzygyProbeLimit" not in engine_config.uci_options:
                try:
                    await engine.configure({"SyzygyProbeLimit": syzygy_config.max_pieces})
                except Exception:
                    pass

    @property
    def name(self) -> str:
        return self.engine.id['name'] if self.engine else "UninitializedEngine"

    def classify_tc(self, base: float, inc: float) -> str:
        if base <= 60:
            return "hyperbullet"
        elif base <= 120 or (base <= 180 and inc <= 1):
            return "bullet"
        elif base <= 600 or (base <= 900 and inc <= 2):
            return "blitz"
        elif base <= 3600:
            return "rapid"
        else:
            return "classical"

    async def make_move(self,
                        board: chess.Board,
                        white_time: float,
                        black_time: float,
                        increment: float
                        ) -> Tuple[chess.Move, chess.engine.InfoDict]:
        assert self.engine is not None
        my_time = white_time if board.turn == chess.WHITE else black_time
        opp_time = black_time if board.turn == chess.WHITE else white_time
        tc = self.classify_tc(max(white_time, black_time), increment)

        # Base allocation (more aggressive on longer time controls)
        if tc == "hyperbullet":
            base_frac = 0.006; cap = 0.05
        elif tc == "bullet":
            base_frac = 0.015; cap = 0.12
        elif tc == "blitz":
            base_frac = 0.06; cap = 1.2
        elif tc == "rapid":
            base_frac = 0.12; cap = 4.0
        else:
            base_frac = 0.20; cap = 12.0

        think_time = max(0.01, my_time * base_frac + min(cap, increment * 2.0))

        # Tactical detection: count captures & promotions among legal moves
        try:
            captures = sum(1 for m in board.legal_moves if board.is_capture(m))
            promotions = sum(1 for m in board.legal_moves if m.promotion is not None)
            tactical_score = captures + promotions
        except Exception:
            captures = 0
            promotions = 0
            tactical_score = 0

        # If the position is tactical (many captures/promotions) or in-check, boost thinking time
        if tactical_score >= 3 or board.is_check():
            # boost factor depends on control; cap the boost to avoid mega delays
            boost = 1.8 if tc not in ("bullet", "hyperbullet") else 1.4
            think_time = min(think_time * boost, max(think_time * boost, 10.0) if tc not in ("bullet", "hyperbullet") else think_time * boost)  # pylint: disable=line-too-long

        # apply overhead multiplier
        try:
            if self.move_overhead_multiplier and self.move_overhead_multiplier != 1.0:
                think_time = think_time / float(self.move_overhead_multiplier)
        except Exception:
            pass

        if self.limit_config.time:
            think_time = min(think_time, self.limit_config.time)

        # bullet safety caps
        if tc in ("bullet", "hyperbullet"):
            if my_time > 30:
                think_time = max(0.03, min(think_time, 0.35))
            elif my_time > 3:
                think_time = max(0.02, min(think_time, 0.12))
            else:
                think_time = 0.01

        limit = chess.engine.Limit(
            white_clock=white_time, white_inc=increment,
            black_clock=black_time, black_inc=increment,
            time=think_time,
            depth=self.limit_config.depth, nodes=self.limit_config.nodes
        )

        try:
            result = await self.engine.play(board, limit, info=chess.engine.INFO_ALL, ponder=self.ponder)
        except Exception as e:
            print("Engine.play failed:", e, file=sys.stderr)
            traceback.print_exc()
            legal = list(board.legal_moves)
            if legal:
                return legal[0], {}
            raise

        if not result.move:
            raise RuntimeError("Engine returned no move")

        # log engine info for diagnostics
        try:
            info = getattr(result, "info", {}) or {}
            depth = info.get("depth")
            score = info.get("score")
            nodes = info.get("nodes")
            nps = info.get("nps")
            t = info.get("time")
            sc = None
            if score:
                try:
                    if score.is_mate():
                        sc = f"mate{score.white().mate() if hasattr(score, 'white') else score}"
                    else:
                        sc = f"{score.white().score()}"
                except Exception:
                    sc = str(score)
            print(f"[ENGINE INFO] move={result.move} depth={depth} score={sc} nodes={nodes} nps={nps} time={t}", file=sys.stderr)  # pylint: disable=line-too-long
        except Exception:
            pass

        # If engine search depth is shallow in a complex position, do a short deeper analysis and adopt it if better
        try:
            # Determine shallow threshold per control
            shallow_threshold = 10 if tc in ("bullet", "hyperbullet", "blitz") else 18
            result_depth = (getattr(result, "info", {}) or {}).get("depth") or 0
            # Use fallback when depth is shallow & position looks tactical or the score is close
            if result_depth < shallow_threshold and (tactical_score >= 2 or board.is_check()):
                # spend up to extra_time (bounded) for a deeper analyze
                extra_time = min(max(think_time * 2.5, 1.0), 12.0) if tc not in ("bullet", "hyperbullet") else think_time * 1.2  # pylint: disable=line-too-long
                analyze_limit = chess.engine.Limit(time=extra_time)
                infos = await self.engine.analyse(board, analyze_limit, multipv=1)
                # results may be a single dict (python-chess differs by version)
                if isinstance(infos, list):
                    best_info = infos[0] if infos else {}
                else:
                    best_info = infos if isinstance(infos, dict) else {}
                pv = best_info.get("pv", [])
                if pv and len(pv) > 0:
                    cand = pv[0]
                    # adopt candidate if different from initial move
                    if cand != result.move:
                        # optionally we could prefer the candidate if it"s evaluated better.
                        # Compare scores if available
                        orig_score = (getattr(result, "info", {}) or {}).get("score")
                        new_score = best_info.get("score")
                        # Prefer candidate when new_score is better for us (i.e. higher for side to move"s perspective)
                        prefer = False
                        try:
                            if orig_score is None:
                                prefer = True
                            elif new_score is not None:
                                # both are Score objects; compare centipawn (white perspective)
                                # For side to move == white -> higher better; if black to move -> lower better.
                                if board.turn == chess.WHITE:
                                    ws_orig = orig_score.white().score(mate_score=100000) if not orig_score.is_mate() else 1000000  # pylint: disable=line-too-long
                                    ws_new = new_score.white().score(mate_score=100000) if not new_score.is_mate() else 1000000  # pylint: disable=line-too-long
                                    prefer = ws_new >= ws_orig
                                else:
                                    # black to move: smaller (more negative for white) -> better for black
                                    ws_orig = orig_score.white().score(mate_score=100000) if not orig_score.is_mate() else -1000000  # pylint: disable=line-too-long
                                    ws_new = new_score.white().score(mate_score=100000) if not new_score.is_mate() else -1000000  # pylint: disable=line-too-long
                                    prefer = ws_new <= ws_orig
                        except Exception:
                            # If comparing fails, default to adopt candidate
                            prefer = True
                        if prefer:
                            print(f"[FALLBACK] adopting deeper candidate {cand} over {result.move}", file=sys.stderr)
                            # Ensure we return a dict even if best_info is not a dict
                            return cand, best_info if isinstance(best_info, dict) else {}
        except Exception:
            # ignore fallback errors
            pass

        # Ensure we always return a dict for the info
        return result.move, getattr(result, "info", {}) if isinstance(getattr(result, "info", {}), dict) else {}

    def _causes_repetition(self, board: chess.Board, move: chess.Move) -> bool:
        board.push(move)
        try:
            rep = board.is_repetition(3) or board.can_claim_threefold_repetition()
        finally:
            board.pop()
        return rep

    async def start_pondering(self, board: chess.Board) -> None:
        if not self.ponder or not self.engine:
            return
        await self.stop_pondering(board)
        try:
            self._ponder_handle = await self.engine.analysis(board)
        except TypeError:
            self._ponder_handle = await self.engine.analysis(board)
        async def _consume(handle: chess.engine.AnalysisResult):
            try:
                async for _ in handle:
                    await asyncio.sleep(0)
            except asyncio.CancelledError:
                return
            except Exception:
                return
        self._ponder_task = asyncio.create_task(_consume(self._ponder_handle))

    async def stop_pondering(self, board: chess.Board) -> None:
        if self._ponder_handle:
            try:
                self._ponder_handle.stop()
            except Exception:
                pass
        if self._ponder_task:
            try:
                await asyncio.wait_for(self._ponder_task, timeout=1.0)
            except asyncio.TimeoutError:
                self._ponder_task.cancel()
                try:
                    await self._ponder_task
                except Exception:
                    pass
        self._ponder_handle = None
        self._ponder_task = None

    async def close(self) -> None:
        if not self.engine:
            return
        try:
            await asyncio.wait_for(self.engine.quit(), 5.0)
        except asyncio.TimeoutError:
            print("Engine didn't quit cleanly", file=sys.stderr)
        except Exception:
            traceback.print_exc()
        if self.transport:
            try:
                self.transport.close()
            except Exception:
                pass
