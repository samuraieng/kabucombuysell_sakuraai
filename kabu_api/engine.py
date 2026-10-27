"""周期処理エンジン: 買い / 売り / 返済 / 売らない の4事象を独立に処理する。"""

import time
from datetime import datetime, time as dtime

from .client import KabuApiClient
from .config import KabuApiConfig
from .conditions import (
    buy_condition,
    is_condition6_1_status,
    is_condition6_2_status,
    sell_condition,
    settle_action,
    settle_signal,
    settle_status_condition5,
    settle_targets,
)
from .instruction_files import BuyList, NoSellList, RetList, SellList
from .logger import get_logger
from .orders import CASHMARGIN_SETTLE, KabuApiOrders
from .positions import KabuApiPositions
from .symbol_info import KabuApiSymbol, round_to_tick, tick_size
from .trading import CASHMARGIN_CASH, CASHMARGIN_SETTLE, KabuApiTrade

logger = get_logger(__name__)


class MarketHours:
    """市場時間判定 (9:05-11:30, 12:30-15:24)。"""

    MORNING_START = dtime(9, 5)
    MORNING_END = dtime(11, 30)
    AFTERNOON_START = dtime(12, 30)
    AFTERNOON_END = dtime(15, 24)

    @classmethod
    def is_market_hours(cls, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        t = now.time()
        return (
            cls.MORNING_START <= t <= cls.MORNING_END
            or cls.AFTERNOON_START <= t <= cls.AFTERNOON_END
        )

    @classmethod
    def seconds_until_next_open(cls, now: datetime | None = None) -> float:
        """次に市場が開くまでの秒数 (開場中は 0)。"""
        now = now or datetime.now()
        if cls.is_market_hours(now):
            return 0.0
        today = now.date()

        def _dt(d, t):
            return datetime.combine(d, t)

        candidates = []
        for day_offset in (0, 1):
            d = today.fromordinal(today.toordinal() + day_offset)
            candidates += [
                _dt(d, cls.MORNING_START),
                _dt(d, cls.AFTERNOON_START),
            ]
        future = [c for c in candidates if c > now]
        if not future:
            return 0.0
        return (min(future) - now).total_seconds()


class TradingEngine:
    """指示ファイル4種に従い、周期処理で売買を行うエンジン。

    買い / 売り / 返済 は独立事象として毎サイクル個別に評価する。
    """

    # 返済注文で永続的と判断できるエラーが発生した場合、同一建玉の再試行を
    # 指定サイクル数抑制する。
    SETTLE_ERROR_COOLDOWN_CYCLES = 5

    def __init__(
        self,
        config: KabuApiConfig | None = None,
        mode: str = "prod",
        cycle: int = 9999,
        market_hours: bool = False,
        buylist_path: str = "./list_buy.txt",
        selllist_path: str = "./list_sell.txt",
        retlist_path: str = "./list_ret.txt",
        sellnolist_path: str = "./list_no_sell.txt",
    ):
        self.config = config or KabuApiConfig()
        self.client = KabuApiClient(self.config)
        self.positions_api = KabuApiPositions(self.client)
        self.orders_api = KabuApiOrders(self.client)
        self.symbol_api = KabuApiSymbol(self.client)
        self.trade = KabuApiTrade(self.client)

        self.mode = mode
        self.cycle = cycle
        self.market_hours = market_hours

        self.buylist_path = buylist_path
        self.selllist_path = selllist_path
        self.retlist_path = retlist_path
        self.sellnolist_path = sellnolist_path

        # 起動時に読み込み、以降は変数で管理するリスト
        self.sell_list = SellList(selllist_path).load()
        self.ret_list = RetList(retlist_path).load()

        # 毎サイクル再読み込みするリスト
        self.buy_list: BuyList = BuyList(buylist_path).load()
        self.no_sell: NoSellList = NoSellList(sellnolist_path).load()

        # 状態
        self.holdings = None
        self.pending_buys: dict[str, dict] = {}   # code -> {"order_id", "qty"}
        # code:hold_id -> order_id: 自プロセスが発注した未約定返済注文
        self.pending_settlements: dict[str, str] = {}
        self.settle_error_cooldown: dict[str, int] = {}    # hold_id -> 残りクールダウンサイクル数
        # ExecutionID(HoldID) -> {"status": str, "counter": int, "order_id": str}
        self.settlement_states: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # 起動時処理
    # ------------------------------------------------------------------
    def setup(self) -> None:
        """トークン取得と retlist の事前情報 (売買単位・呼値) を確認する。"""
        env = self.order_env()
        self.client.get_token("prod")   # データ取得は常に本番
        if env:
            self.client.get_token(env)  # 注文環境のトークンも取得

        # retlist: 売買最小単位 (売買単位) と呼値グループを API で確認
        for row in self.ret_list.rows:
            try:
                unit, group = self.symbol_api.unit_and_group(row.code)
                sm, smm = row.normalized_spreads()
                logger.info(f"  [retlist] {row.code}: 売買単位={unit}, 呼値グループ={group}, "
                            f"spread_plus={row.spread_plus or 1}, "
                            f"spread_minus={sm}, "
                            f"spread_minus_max={smm}, "
                            f"count_max={row.effective_count_max()}")
            except Exception as e:
                logger.warning(f"  [retlist警告] {row.code}: 銘柄情報取得失敗 ({e})")

    def order_env(self) -> str | None:
        """注文発行先の環境。dev モードは発注しないため None。"""
        return "prod" if self.mode == "prod" else None

    def _settle_cooldown_key(self, code: str, hold_id: str) -> str:
        """建玉ごとのクールダウン管理用キー。"""
        return f"{code}:{hold_id}"

    def _is_settle_cooldown(self, code: str, hold_id: str) -> bool:
        """指定建玉が前回の永続エラーによりクールダウン中かどうか。"""
        key = self._settle_cooldown_key(code, hold_id)
        remaining = self.settle_error_cooldown.get(key, 0)
        if remaining <= 0:
            if key in self.settle_error_cooldown:
                del self.settle_error_cooldown[key]
            return False
        return True

    def _set_settle_cooldown(self, code: str, hold_id: str) -> None:
        """指定建玉を指定サイクル数クールダウン状態にする。"""
        key = self._settle_cooldown_key(code, hold_id)
        self.settle_error_cooldown[key] = self.SETTLE_ERROR_COOLDOWN_CYCLES

    def _decrement_settle_cooldowns(self) -> None:
        """サイクル終了時にクールダウン残数を1減らす。"""
        expired = [
            key for key, remaining in self.settle_error_cooldown.items()
            if remaining <= 1
        ]
        for key in expired:
            del self.settle_error_cooldown[key]
        for key in self.settle_error_cooldown:
            self.settle_error_cooldown[key] -= 1

    def _cleanup_settlement_states(self) -> None:
        """返済済み・消失した建玉の状態をクリーンアップする。"""
        current_ids = {
            pos.hold_id
            for positions in self.holdings.credit.values()
            for pos in positions
            if pos.hold_id
        }
        expired = [k for k in self.settlement_states if k not in current_ids]
        for k in expired:
            del self.settlement_states[k]

    # ------------------------------------------------------------------
    # ループ制御
    # ------------------------------------------------------------------
    def run(self) -> None:
        """cycle 設定に従い処理を繰り返す。

        cycle == 9999 : 1回のみ実行 (default)
        cycle == 0    : 待機なしで連続実行
        1..3600       : 指定秒間隔で実行
        """
        if self.cycle == 9999:
            self.run_cycle()
            return

        logger.info(f"ループモード: {'待機なし' if self.cycle == 0 else f'{self.cycle}秒間隔'}"
                    f" / {'市場時間のみ' if self.market_hours else '24時間'}"
                    f" / Ctrl+C で停止")
        while True:
            try:
                if self.market_hours and not MarketHours.is_market_hours():
                    wait = MarketHours.seconds_until_next_open()
                    logger.warning(f"[{self._now()}] 市場時間外です ({wait:.0f}秒後に開場)")
                    time.sleep(min(max(wait, 5), 60))
                    continue
                self.run_cycle()
                if self.cycle == 0:
                    continue
                self._sleep_cycle(self.cycle)
            except KeyboardInterrupt:
                logger.info("\nユーザーにより中断されました。")
                break
            except Exception as e:
                logger.error(f"[{self._now()}] 予期せぬエラー: {e}")
                if self.cycle == 0:
                    time.sleep(1.0)
                else:
                    self._sleep_cycle(self.cycle)

    def _sleep_cycle(self, seconds: int) -> None:
        """サイクル間の待機 (市場時間モードでは開場を跨ぐため細かくスリープ)。"""
        remaining = float(seconds)
        while remaining > 0:
            if self.market_hours and not MarketHours.is_market_hours():
                return
            chunk = min(remaining, 5.0)
            time.sleep(chunk)
            remaining -= chunk

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%H:%M:%S")

    # ------------------------------------------------------------------
    # 1サイクル
    # ------------------------------------------------------------------
    def run_cycle(self) -> None:
        logger.info(f"\n{'=' * 60}")
        logger.info(f"[{self._now()}] サイクル開始 (mode={self.mode})")
        logger.info(f"{'=' * 60}")

        # 1) 指示ファイルの再読み込み (buylist / sellnolist は毎周期反映)
        self.no_sell = NoSellList(self.sellnolist_path).load()
        self.buy_list = BuyList(self.buylist_path).load()

        # 2) 所有リスト更新
        self.holdings = self.positions_api.build_holdings()
        logger.info(f"所有リスト: 現物 {len(self.holdings.cash)} 銘柄 / "
                    f"信用 {sum(len(v) for v in self.holdings.credit.values())} 建玉")
        self._print_credit_positions()

        # 3) 各事象を独立に処理
        self._check_buy_fills()
        self._process_buys()
        self._process_sells()
        self._process_settlements()
        self._decrement_settle_cooldowns()
        logger.info(f"[{self._now()}] サイクル終了")

    # ------------------------------------------------------------------
    # 買い (条件#1 / 条件#2)
    # ------------------------------------------------------------------
    def _check_buy_fills(self) -> None:
        """発注済み買い注文の約定確認 → 約定していれば list_buy.txt の行を無効化。"""
        for code in list(self.pending_buys.keys()):
            info = self.pending_buys[code]
            # 未約定注文がまだ生きている → 待機
            if self.orders_api.has_open_order(code, side="2", cashmargin=CASHMARGIN_CASH):
                continue
            # 注文が終了(全約定/取消/失効) → 約定数量を確認
            hold_qty = self.holdings.cash_qty(code)
            if hold_qty >= info["qty"]:
                logger.info(f"  [買約定] {code}: {hold_qty}株 取得 → list_buy.txt を無効化")
                row = next((r for r in self.buy_list.active_rows() if r.code == code), None)
                if row is not None:
                    self.buy_list.disable_row(row)
            else:
                logger.info(f"  [買注文終了] {code}: 約定せず終了 → 再評価します")
            del self.pending_buys[code]

    def _process_buys(self) -> None:
        """buylist の条件#1 (指値/成行) と条件#2 (トリガー以下で成行) を処理。"""
        for row in self.buy_list.active_rows():
            code = row.code
            if code in self.pending_buys:
                continue  # 発注済み (未約定) → 重複発注しない
            try:
                current = self.symbol_api.get_current_price(code)
            except Exception as e:
                logger.warning(f"  [買警告] {code}: 現在値取得失敗 ({e})")
                continue
            if current <= 0:
                continue
            signal = buy_condition(row.price, row.trigger_price, current)
            if signal is None:
                continue

            reason = "条件#2(トリガー以下)" if (
                row.trigger_price > 0 and current <= row.trigger_price
            ) else "条件#1"
            if self.order_env() is None:
                logger.info(f"  [DEV買] {code}: {signal} 数量={row.qty} ({reason})")
                continue

            price = row.price if signal == "limit" else 0.0
            result = self.trade.send_cash_buy(code, row.qty, price, env=self.order_env())
            if result.ok:
                self.pending_buys[code] = {"order_id": result.order_id, "qty": row.qty}
                logger.info(f"  [買注文] {code}: {signal} 数量={row.qty} 価格={price or '成行'} "
                            f"OrderId={result.order_id} ({reason})")
            else:
                logger.error(f"  [買注文エラー] {code}: {result.message}")

    # ------------------------------------------------------------------
    # 売り (条件#3 / 条件#4)
    # ------------------------------------------------------------------
    def _process_sells(self) -> None:
        """selllist の条件#3 (指値/成行) と条件#4 (トリガー以上で成行) を処理。"""
        for row in self.sell_list.active_rows():
            code = row.code
            if self.no_sell.contains(code):
                continue  # 売らないリスト指定品は売りに出さない
            holding = self.holdings.cash.get(code)
            if holding is None or holding.qty <= 0:
                continue  # 所有リストにない → 無視
            hold_qty = holding.qty
            sell_qty = row.qty if 0 < row.qty <= hold_qty else hold_qty  # 0/超過 → 全数

            current = holding.current_price or self.symbol_api.get_current_price(code)
            if current <= 0:
                continue
            signal = sell_condition(row.price, row.trigger_price, current)
            if signal is None:
                continue

            open_ids = self.orders_api.open_order_ids(
                code, side="1", cashmargin=CASHMARGIN_CASH
            )
            if open_ids and signal == "limit":
                continue  # 未約定の指値売りが既にある → 重複発注しない
            if open_ids and signal == "market":
                # 条件#4成立 → 現在の指値売りを取り消して成行
                for oid in open_ids:
                    logger.info(f"  [売取消] {code}: OrderId={oid} (条件#4成立)")
                    self.trade.cancel_order(oid)

            reason = "条件#4(トリガー以上)" if (
                row.trigger_price > 0 and current >= row.trigger_price
            ) else "条件#3"
            if self.order_env() is None:
                logger.info(f"  [DEV売] {code}: {signal} 数量={sell_qty} ({reason})")
                continue

            price = row.price if signal == "limit" else 0.0
            result = self.trade.send_cash_sell(code, sell_qty, price, env=self.order_env())
            if result.ok:
                logger.info(f"  [売注文] {code}: {signal} 数量={sell_qty} 価格={price or '成行'} "
                            f"OrderId={result.order_id} ({reason})")
            else:
                logger.error(f"  [売注文エラー] {code}: {result.message}")

    def _print_credit_positions(self) -> None:
        """信用建玉の詳細を表示する (コード・銘柄名・数量・建玉単価・時価・損益)。"""
        for symbol, positions in self.holdings.credit.items():
            for pos in positions:
                label = {"2": "買建", "1": "売建"}.get(pos.side, "信用")
                name = pos.name or "(不明)"
                logger.info(f"  [信用] {pos.symbol} {name} {label} "
                            f"数量={pos.qty} 建玉単価={pos.price:.1f} "
                            f"時価={pos.current_price:.1f} 損益={pos.profit_loss:.1f}")

    # ------------------------------------------------------------------
    # 返済 (条件#5 / 条件#6)
    # ------------------------------------------------------------------
    def _process_settlements(self) -> None:
        """retlist の信用建玉に対し条件#5 (指値返済) / #6 (損切り成行) を処理。"""
        for row in self.ret_list.active_rows():
            code = row.code
            positions = self.holdings.credit_positions(code)
            if not positions:
                continue  # 信用建玉なし → 無視

            try:
                _, group = self.symbol_api.unit_and_group(code)
            except Exception:
                group = "10000"

            for pos in positions:
                # 建玉ID が取得できていない場合は返済できない
                if not pos.hold_id:
                    logger.warning(f"  [返済スキップ] {code}: 建玉ID(HoldID) が未設定です")
                    continue

                label = {"2": "買建", "1": "売建"}.get(pos.side, "信用")

                # 前回のワンショット金額エラーなどでクールダウン中ならスキップ
                if self._is_settle_cooldown(code, pos.hold_id):
                    logger.warning(
                        f"  [返済クールダウン中] {code} {label}: "
                        f"前回の永続エラーのため "
                        f"あと{self.settle_error_cooldown.get(self._settle_cooldown_key(code, pos.hold_id), 0)}"
                        f"サイクル返済注文を抑制します"
                    )
                    continue

                # 対象建玉に紐づく未約定返済注文 (CashMargin=3)
                open_settle_ids = self.orders_api.open_order_ids_by_hold_id(
                    code, pos.hold_id, cashmargin=CASHMARGIN_SETTLE
                )

                tick = tick_size(
                    pos.price if pos.price > 0 else pos.current_price, group
                )
                spread_plus = row.effective_spread_plus() * tick
                sm, smm = row.normalized_spreads()
                spread_minus = sm * tick
                spread_minus_max = smm * tick
                count_max = row.effective_count_max()

                # 返済処理状態: ExecutionID(HoldID) -> status / counter / order_id
                exec_id = pos.hold_id
                state = self.settlement_states.get(
                    exec_id, {"status": settle_status_condition5(), "counter": 0, "order_id": ""}
                )
                status = state["status"]
                counter = state["counter"]

                current = pos.current_price or self.symbol_api.get_current_price(code)
                if current <= 0:
                    continue

                action_result = settle_action(
                    pos.side, pos.price, current,
                    spread_plus, spread_minus, spread_minus_max, count_max,
                    status, counter,
                )
                close_side = action_result["close_side"]
                if not close_side:
                    continue

                # 自プロセスが発注した返済注文を記憶から補完
                # (/orders の未約定レスポンスに HoldID/ExecutionID が含まれない場合の対策)
                pending_key = self._settle_cooldown_key(code, pos.hold_id)
                pending_oid = self.pending_settlements.get(pending_key)
                if pending_oid and pending_oid not in open_settle_ids:
                    if self.orders_api.has_open_order(
                        code, side=close_side, cashmargin=CASHMARGIN_SETTLE
                    ):
                        open_settle_ids.append(pending_oid)
                        logger.debug(f"  [返済判定補完] {code} {label}: 自プロセス発注の未約定返済注文 "
                                     f"OrderId={pending_oid} を取消対象に追加")

                logger.debug(f"  [返済判定] {code} {label}: 現在値={current}, "
                             f"status={status}, action={action_result['action']}, "
                             f"price={action_result['price']}, reason={action_result['reason']}, "
                             f"next_status={action_result['next_status']}, "
                             f"next_counter={action_result['next_counter']}, "
                             f"未約定返済注文={len(open_settle_ids)}件")

                if action_result["action"] == "none":
                    # condition-6-1 状態で #6-2 監視に移行する際、未約定注文がなければ
                    # #6-1 指値は約定したとみなして condition-5 に戻す
                    if (is_condition6_1_status(status)
                            and is_condition6_2_status(action_result["next_status"])
                            and not open_settle_ids):
                        logger.info(f"  [返済済み] {code} {label}: 条件#6-1 指値が約定したため状態をリセット")
                        self.settlement_states[exec_id] = {
                            "status": settle_status_condition5(),
                            "counter": 0,
                            "order_id": "",
                        }
                        continue
                    # 状態更新のみ
                    self.settlement_states[exec_id] = {
                        "status": action_result["next_status"],
                        "counter": action_result["next_counter"],
                        "order_id": state.get("order_id", ""),
                    }
                    continue

                # 返済可能数量（拘束数量を除く）
                available_qty = pos.qty - pos.hold_qty

                if action_result["action"] == "market":
                    # 条件#6-2 発火 → 建玉全体を成行返済。
                    # 未約定返済注文があれば先に取消する（建玉ID紐付けが取れない場合は銘柄単位で探す）
                    settle_qty = pos.qty
                    if settle_qty <= 0:
                        continue

                    if not open_settle_ids:
                        fallback_ids = self.orders_api.open_order_ids(
                            code, side=close_side, cashmargin=CASHMARGIN_SETTLE
                        )
                        if fallback_ids:
                            open_settle_ids = fallback_ids
                            logger.warning(f"  [返済判定補正] {code} {label}: 建玉ID紐付け外の未約定返済注文 "
                                           f"{len(open_settle_ids)}件を取消対象に設定")

                    for oid in open_settle_ids:
                        logger.info(f"  [返済取消] {code} {label}: OrderId={oid} ({action_result['reason']})")
                        self.trade.cancel_order(oid)
                    if self.order_env() is None:
                        logger.info(f"  [DEV返済] {code} {label}: 成行 数量={settle_qty} "
                                    f"({action_result['reason']}: 現在値{current})")
                        self.settlement_states[exec_id] = {
                            "status": action_result["next_status"],
                            "counter": action_result["next_counter"],
                            "order_id": state.get("order_id", ""),
                        }
                        continue
                    close_positions = [{"HoldID": pos.hold_id, "Qty": settle_qty}]
                    logger.info(
                        f"  [返済注文発行] {code} {label}: Side={close_side} "
                        f"成行 数量={settle_qty} Exchange={pos.exchange} "
                        f"MarginTradeType={pos.margin_trade_type or 3} "
                        f"ClosePositions={close_positions}"
                    )
                    result = self.trade.send_credit_settle(
                        code,
                        close_side,
                        settle_qty,
                        price=0.0,
                        close_positions=close_positions,
                        margin_trade_type=pos.margin_trade_type or 3,
                        exchange=pos.exchange,
                        env=self.order_env(),
                    )
                    tag = "返済注文(成行)"
                else:
                    # 条件#5 / 条件#6-1 → 指値返済
                    # status が変わる場合（condition-5 → condition-6-1）は既存の未約定注文を取消して再発注
                    # status が同じ場合は未約定注文があればスキップ
                    if open_settle_ids and status == action_result["next_status"]:
                        continue
                    if open_settle_ids and status != action_result["next_status"]:
                        for oid in open_settle_ids:
                            logger.info(f"  [返済取消] {code} {label}: OrderId={oid} ({action_result['reason']})")
                            self.trade.cancel_order(oid)

                    # 返済可能数量が 0 でも、未約定注文が検出できなければ建玉全体を指値返済する
                    if available_qty <= 0:
                        fallback_ids = self.orders_api.open_order_ids(
                            code, side=close_side, cashmargin=CASHMARGIN_SETTLE
                        )
                        if fallback_ids:
                            continue
                        settle_qty = pos.qty
                    else:
                        settle_qty = available_qty
                    if settle_qty <= 0:
                        continue
                    limit_price = round_to_tick(action_result["price"], group)
                    if self.order_env() is None:
                        logger.info(f"  [DEV返済] {code} {label}: 指値{limit_price} 数量={settle_qty} "
                                    f"(spread_plus={row.spread_plus or 1}, spread_minus={sm})")
                        self.settlement_states[exec_id] = {
                            "status": action_result["next_status"],
                            "counter": action_result["next_counter"],
                            "order_id": state.get("order_id", ""),
                        }
                        continue
                    close_positions = [{"HoldID": pos.hold_id, "Qty": settle_qty}]
                    logger.info(
                        f"  [返済注文発行] {code} {label}: Side={close_side} "
                        f"指値{limit_price} 数量={settle_qty} Exchange={pos.exchange} "
                        f"MarginTradeType={pos.margin_trade_type or 3} "
                        f"ClosePositions={close_positions}"
                    )
                    result = self.trade.send_credit_settle(
                        code,
                        close_side,
                        settle_qty,
                        price=limit_price,
                        close_positions=close_positions,
                        margin_trade_type=pos.margin_trade_type or 3,
                        exchange=pos.exchange,
                        env=self.order_env(),
                    )
                    tag = f"返済注文(指値{limit_price})"

                if result.ok:
                    logger.info(f"  [{tag}] {code} {label}: 数量={settle_qty} "
                                f"OrderId={result.order_id} "
                                f"(spread_plus={row.spread_plus or 1}, spread_minus={sm})")
                    # 発注した返済注文を記憶し、次サイクルで建玉ID紐付けが取れなくても検出できるようにする
                    if result.order_id:
                        self.pending_settlements[pending_key] = result.order_id
                    self.settlement_states[exec_id] = {
                        "status": action_result["next_status"],
                        "counter": action_result["next_counter"],
                        "order_id": result.order_id or "",
                    }
                else:
                    self.settlement_states[exec_id] = {
                        "status": action_result["next_status"],
                        "counter": action_result["next_counter"],
                        "order_id": state.get("order_id", ""),
                    }
                    if result.is_one_shot_amount_error:
                        self._set_settle_cooldown(code, pos.hold_id)
                        logger.error(
                            f"  [返済注文エラー(クールダウン)] {code} {label}: "
                            f"{result.one_shot_message}"
                        )
                    elif result.is_settle_spec_error:
                        # Code=8「決済指定内容に誤りがあります」は、同じ建玉に対して
                        # 既に返済注文が入っている（ポジションが返済中）可能性が高い。
                        # 連続した無駄な発注を避けるためクールダウンを設定する。
                        self._set_settle_cooldown(code, pos.hold_id)
                        logger.error(
                            f"  [返済注文エラー(クールダウン)] {code} {label}: "
                            f"{result.message} "
                            f"(同じ建玉に対して既に未約定の返済注文がある可能性があります。"
                            f"{self.SETTLE_ERROR_COOLDOWN_CYCLES}サイクル間返済注文を抑制します)"
                        )
                    else:
                        logger.error(f"  [返済注文エラー] {code} {label}: {result.message}")
                    if result.request_body:
                        logger.debug(f"  [返済注文リクエスト] {code} {label}: {result.request_body}")

        # 返済済み・消失した建玉の状態をクリーンアップ
        self._cleanup_settlement_states()
