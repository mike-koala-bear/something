import asyncio
from itertools import islice
from typing import Any

from api import API
from botli_dataclasses import Game_Information
from chatter import Chatter

from config import Config
from lichess_game import Lichess_Game


class Game:
    def __init__(self, api: API, config: Config, username: str, game_id: str, rematch_manager=None) -> None:
        self.api = api
        self.config = config
        self.username = username
        self.game_id = game_id
        self.rematch_manager = rematch_manager

        self.takeback_count = 0
        self.was_aborted = False
        self.ejected_tournament: str | None = None

        self.move_task: asyncio.Task[None] | None = None
        self.bot_offered_draw = False
        self.last_fen = None
        self.last_move = None

    async def run(self) -> None:
        game_stream_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        asyncio.create_task(self.api.get_game_stream(self.game_id, game_stream_queue))

        info = Game_Information.from_gameFull_event(await game_stream_queue.get())
        lichess_game = await Lichess_Game.acreate(self.api, self.config, self.username, info)
        chatter = Chatter(self.api, self.config, self.username, info, lichess_game)

        self._print_game_information(info)

        if info.state['status'] != 'started':
            self._print_result_message(info.state, lichess_game, info)
            await chatter.send_goodbyes()
            await lichess_game.close()
            return

        asyncio.create_task(chatter.send_greetings())

        if not lichess_game.is_our_turn:
            asyncio.create_task(lichess_game.start_pondering())
        else:
            self.move_task = asyncio.create_task(self._make_move(lichess_game, chatter))

        opponent_is_bot = info.white_title == 'BOT' and info.black_title == 'BOT'
        abortion_seconds = 30 if opponent_is_bot else 60
        abortion_task = asyncio.create_task(self._abortion_task(lichess_game, chatter, abortion_seconds))
        max_takebacks = 0 if opponent_is_bot else self.config.challenge.max_takebacks

        while True:
            try:
                event = await game_stream_queue.get()
            except asyncio.CancelledError:
                break

            # Handle chat asynchronously
            if event['type'] == 'chatLine':
                asyncio.create_task(chatter.handle_chat_message(event))
                continue

            # Handle opponent gone
            if event['type'] == 'opponentGone':
                if event.get('claimWinInSeconds') == 0:
                    asyncio.create_task(self.api.claim_victory(self.game_id))
                continue

            if event['type'] == 'gameFull':
                event = event['state']

            # Draw offer
            if event.get('wdraw') or event.get('bdraw'):
                await self._handle_draw_offer(event, lichess_game)
            else:
                self.bot_offered_draw = False

            # Takebacks
            if event.get('wtakeback') or event.get('btakeback'):
                await self._handle_takeback(event, lichess_game, max_takebacks)

            # Update board
            has_updated = lichess_game.update(event)

            # Game finished
            if event['status'] != 'started':
                if self.move_task:
                    self.move_task.cancel()
                self._print_result_message(event, lichess_game, info)
                asyncio.create_task(chatter.send_goodbyes())
                if self.rematch_manager and not self.was_aborted:
                    await self._handle_rematch(event, info)
                break

            # Make move if updated
            if has_updated and (self.move_task is None or self.move_task.done()):
                self.move_task = asyncio.create_task(self._make_move(lichess_game, chatter))

        abortion_task.cancel()
        await lichess_game.close()

    async def _handle_draw_offer(self, event, lichess_game: Lichess_Game):
        is_0_5_0_game = hasattr(lichess_game.game_info, 'tc_str') and lichess_game.game_info.tc_str == '0.5+0'
        is_opponent_draw = (lichess_game.is_white and event.get('bdraw')) or (not lichess_game.is_white and event.get('wdraw'))

        if not is_opponent_draw or self.bot_offered_draw:
            self.bot_offered_draw = False
            return

        should_accept = False
        if is_0_5_0_game and self.config.offer_draw.accept_30_second_draws:
            should_accept = self._should_accept_draw(lichess_game)
        elif not is_0_5_0_game:
            should_accept = self._should_accept_draw(lichess_game)

        if should_accept:
            asyncio.create_task(self.api.accept_draw(self.game_id))
        elif not is_0_5_0_game:
            asyncio.create_task(self.api.decline_draw(self.game_id))

        self.bot_offered_draw = False

    async def _handle_takeback(self, event, lichess_game: Lichess_Game, max_takebacks: int):
        if self.takeback_count >= max_takebacks:
            asyncio.create_task(self.api.handle_takeback(self.game_id, False))
            return

        if await self.api.handle_takeback(self.game_id, True):
            if self.move_task:
                self.move_task.cancel()
                self.move_task = None
            await lichess_game.takeback()
            self.takeback_count += 1

    async def _make_move(self, lichess_game: Lichess_Game, chatter: Chatter):
        # Reuse last move if board hasn't changed
        if lichess_game.board.fen() == self.last_fen and self.last_move is not None:
            lichess_move = self.last_move
        else:
            lichess_move = await lichess_game.make_move()
            self.last_fen = lichess_game.board.fen()
            self.last_move = lichess_move

        if lichess_move.resign:
            asyncio.create_task(self.api.resign_game(self.game_id))
        else:
            self.bot_offered_draw = lichess_move.offer_draw
            asyncio.create_task(self.api.send_move(self.game_id, lichess_move.uci_move, lichess_move.offer_draw))
            asyncio.create_task(chatter.print_eval())

        self.move_task = None

    async def _abortion_task(self, lichess_game: Lichess_Game, chatter: Chatter, abortion_seconds: int):
        await asyncio.sleep(abortion_seconds)
        if not lichess_game.is_our_turn and lichess_game.is_abortable:
            print('Aborting game ...')
            await self.api.abort_game(self.game_id)
            await chatter.send_abortion_message()

    # Original printing and rematch functions kept
    def _print_game_information(self, info: Game_Information) -> None:
        opponents_str = f'{info.white_str}   -   {info.black_str}'
        message = (5 * ' ').join([info.id_str, opponents_str, info.tc_format,
                                  info.rated_str, info.variant_str])
        print(f'\n{message}\n{128 * "-"}')

    def _print_result_message(self,
                              game_state: dict[str, Any],
                              lichess_game: Lichess_Game,
                              info: Game_Information) -> None:
        if winner := game_state.get('winner'):
            if winner == 'white':
                message = f'{info.white_name} won'
                loser = info.black_name
                white_result = '1'
                black_result = '0'
            else:
                message = f'{info.black_name} won'
                loser = info.white_name
                white_result = '0'
                black_result = '1'

            match game_state['status']:
                case 'mate':
                    message += ' by checkmate!'
                case 'outoftime':
                    message += f'! {loser} ran out of time.'
                case 'resign':
                    message += f'! {loser} resigned.'
                case 'variantEnd':
                    message += ' by variant rules!'
                case 'timeout':
                    message += f'! {loser} timed out.'
                case 'noStart':
                    if loser == self.username:
                        self.ejected_tournament = info.tournament_id
                    message += f'! {loser} has not started the game.'
        else:
            white_result = '1/2'
            black_result = '1/2'

            match game_state['status']:
                case 'draw':
                    if lichess_game.board.is_fifty_moves():
                        message = 'Game drawn by 50-move rule.'
                    elif lichess_game.board.is_repetition():
                        message = 'Game drawn by threefold repetition.'
                    elif lichess_game.board.is_insufficient_material():
                        message = 'Game drawn due to insufficient material.'
                    elif lichess_game.board.is_variant_draw():
                        message = 'Game drawn by variant rules.'
                    else:
                        message = 'Game drawn by agreement.'
                case 'stalemate':
                    message = 'Game drawn by stalemate.'
                case 'outoftime':
                    out_of_time_player = info.black_name if game_state['wtime'] else info.white_name
                    message = f'Game drawn. {out_of_time_player} ran out of time.'
                case 'insufficientMaterialClaim':
                    message = 'Game drawn due to insufficient material claim.'
                case _:
                    self.was_aborted = True
                    message = 'Game aborted.'

                    white_result = 'X'
                    black_result = 'X'

        opponents_str = f'{info.white_str} {white_result} - {black_result} {info.black_str}'
        message = (5 * ' ').join([info.id_str, opponents_str, message])
        print(f'{message}\n{128 * "-"}')

    async def _handle_rematch(self, game_state: dict[str, Any], info: Game_Information) -> None:
        try:
            winner = game_state.get('winner')
            game_result = game_state.get('status', 'unknown')

            if self.rematch_manager.should_offer_rematch(info, game_result, winner):
                await self.rematch_manager.offer_rematch(info)
            else:
                opponent_name = self.rematch_manager._get_opponent_name(info)
                if opponent_name:
                    self.rematch_manager.on_game_finished(opponent_name)
        except Exception as e:
            print(f'Error handling rematch: {e}')
