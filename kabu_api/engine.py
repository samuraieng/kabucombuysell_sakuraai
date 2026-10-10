"""周期処理エンジン: 買い / 売り / 返済 / 売らない の4事象を独立に処理する。"""

import time
from datetime import datetime, time as dtime

from .client import KabuApiClient
from .config import KabuApiConfig
from .conditions import buy_condition, sell_condition, settle_signal, settle_targets
from .instruction_files import BuyList, NoSellList, RetList, SellList
from .orders import CASHMARGIN_SETTLE, KabuApiOrders
from .positions import KabuApiPositions
from .symbol_info import KabuApiSymbol, round_to_tick, tick_size
from .trading import CASHMARGIN_CASH, CASHMARGIN_SETTLE, KabuApiTrade


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
    # しばらく抑制するためのクールダウン時間(秒)。
    SETTLE_ERROR_COOLDOWN_SECONDS = 60.0

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
        self.settle_error_cooldown: dict[str, float] = {}  # hold_id -> クールダウン期限(epoch秒)

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
                print(f"  [retlist] {row.code}: 売買単位={unit}, 呼値グループ={group}, "
                      f"指定金額={row.spread or '呼値(自動)'}")
            except Exception as e:
                print(f"  [retlist警告] {row.code}: 銘柄情報取得失敗 ({e})")

    def order_env(self) -> str | None:
        """注文発行先の環境。dev モードは発注しないため None。"""
        return "prod" if self.mode == "prod" else None

    def _settle_cooldown_key(self, code: str, hold_id: str) -> str:
        """建玉ごとのクールダウン管理用キー。"""
        return f"{code}:{hold_id}"

    def _is_settle_cooldown(self, code: str, hold_id: str) -> bool:
        """指定建玉が前回の永続エラーによりクールダウン中かどうか。"""
        key = self._settle_cooldown_key(code, hold_id)
        expire = self.settle_error_cooldown.get(key)
        if expire is None:
            return False
        if time.monotonic() >= expire:
            del self.settle_error_cooldown[key]
            return False
        return True

    def _set_settle_cooldown(self, code: str, hold_id: str) -> None:
        """指定建玉を一定時間クールダウン状態にする。"""
        key = self._settle_cooldown_key(code, hold_id)
        self.settle_error_cooldown[key] = (
            time.monotonic() + self.SETTLE_ERROR_COOLDOWN_SECONDS
        )

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

        print(f"ループモード: {'待機なし' if self.cycle == 0 else f'{self.cycle}秒間隔'}"
              f" / {'市場時間のみ' if self.market_hours else '24時間'}"
              f" / Ctrl+C で停止")
        while True:
            try:
                if self.market_hours and not MarketHours.is_market_hours():
                    wait = MarketHours.seconds_until_next_open()
                    print(f"[{self._now()}] 市場時間外です ({wait:.0f}秒後に開場)")
                    time.sleep(min(max(wait, 5), 60))
                    continue
                self.run_cycle()
                if self.cycle == 0:
                    continue
                self._sleep_cycle(self.cycle)
            except KeyboardInterrupt:
                print("\nユーザーにより中断されました。")
                break
            except Exception as e:
                print(f"[{self._now()}] 予期せぬエラー: {e}")
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
        print(f"\n{'=' * 60}")
        print(f"[{self._now()}] サイクル開始 (mode={self.mode})")
        print(f"{'=' * 60}")

        # 1) 指示ファイルの再読み込み (buylist / sellnolist は毎周期反映)
        self.no_sell = NoSellList(self.sellnolist_path).load()
        self.buy_list = BuyList(self.buylist_path).load()

        # 2) 所有リスト更新
        self.holdings = self.positions_api.build_holdings()
        print(f"所有リスト: 現物 {len(self.holdings.cash)} 銘柄 / "
              f"信用 {sum(len(v) for v in self.holdings.credit.values())} 建玉")
        self._print_credit_positions()

        # 3) 各事象を独立に処理
        self._check_buy_fills()
        self._process_buys()
        self._process_sells()
        self._process_settlements()
        print(f"[{self._now()}] サイクル終了")

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
                print(f"  [買約定] {code}: {hold_qty}株 取得 → list_buy.txt を無効化")
                row = next((r for r in self.buy_list.active_rows() if r.code == code), None)
                if row is not None:
                    self.buy_list.disable_row(row)
            else:
                print(f"  [買注文終了] {code}: 約定せず終了 → 再評価します")
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
                print(f"  [買警告] {code}: 現在値取得失敗 ({e})")
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
                print(f"  [DEV買] {code}: {signal} 数量={row.qty} ({reason})")
                continue

            price = row.price if signal == "limit" else 0.0
            result = self.trade.send_cash_buy(code, row.qty, price, env=self.order_env())
            if result.ok:
                self.pending_buys[code] = {"order_id": result.order_id, "qty": row.qty}
                print(f"  [買注文] {code}: {signal} 数量={row.qty} 価格={price or '成行'} "
                      f"OrderId={result.order_id} ({reason})")
            else:
                print(f"  [買注文エラー] {code}: {result.message}")

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
                    print(f"  [売取消] {code}: OrderId={oid} (条件#4成立)")
                    self.trade.cancel_order(oid)

            reason = "条件#4(トリガー以上)" if (
                row.trigger_price > 0 and current >= row.trigger_price
            ) else "条件#3"
            if self.order_env() is None:
                print(f"  [DEV売] {code}: {signal} 数量={sell_qty} ({reason})")
                continue

            price = row.price if signal == "limit" else 0.0
            result = self.trade.send_cash_sell(code, sell_qty, price, env=self.order_env())
            if result.ok:
                print(f"  [売注文] {code}: {signal} 数量={sell_qty} 価格={price or '成行'} "
                      f"OrderId={result.order_id} ({reason})")
            else:
                print(f"  [売注文エラー] {code}: {result.message}")

    def _print_credit_positions(self) -> None:
        """信用建玉の詳細を表示する (コード・銘柄名・数量・建玉単価・時価・損益)。"""
        for symbol, positions in self.holdings.credit.items():
            for pos in positions:
                label = {"2": "買建", "1": "売建"}.get(pos.side, "信用")
                name = pos.name or "(不明)"
                print(f"  [信用] {pos.symbol} {name} {label} "
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
                    print(f"  [返済スキップ] {code}: 建玉ID(HoldID) が未設定です")
                    continue

                label = {"2": "買建", "1": "売建"}.get(pos.side, "信用")

                # 前回のワンショット金額エラーなどでクールダウン中ならスキップ
                if self._is_settle_cooldown(code, pos.hold_id):
                    print(
                        f"  [返済クールダウン中] {code} {label}: "
                        f"前回の永続エラーのため "
                        f"{self.SETTLE_ERROR_COOLDOWN_SECONDS:.0f}秒間返済注文を抑制します"
                    )
                    continue

                # 対象建玉に紐づく未約定返済注文 (CashMargin=3)
                open_settle_ids = self.orders_api.open_order_ids_by_hold_id(
                    code, pos.hold_id, cashmargin=CASHMARGIN_SETTLE
                )

                spread = row.spread if row.spread > 0 else tick_size(
                    pos.price if pos.price > 0 else pos.current_price, group
                )
                targets = settle_targets(pos.side, pos.price, spread)
                current = pos.current_price or self.symbol_api.get_current_price(code)
                if current <= 0 or not targets["close_side"]:
                    continue
                signal = settle_signal(targets, current)

                limit_price = round_to_tick(targets["limit_price"], group)
                stop_current = targets["stop_current"]
                # 条件#6（反対指示・損切り）までの価格幅
                if pos.side == "2":
                    distance = current - stop_current
                else:
                    distance = stop_current - current
                print(f"  [返済判定] {code} {label}: 現在値={current}, "
                      f"条件#5指値={limit_price}, 条件#6損切={stop_current}, "
                      f"反転までの猶予={distance:.2f}, signal={signal}, "
                      f"未約定返済注文={len(open_settle_ids)}件")
                if signal is None:
                    continue

                # 返済可能数量（拘束数量を除く）
                available_qty = pos.qty - pos.hold_qty

                if signal == "market":
                    # 条件#6 → 建玉全体を成行返済。
                    # 未約定返済注文があれば先に取消する（建玉ID紐付けが取れない場合は銘柄単位で探す）
                    settle_qty = pos.qty
                    if settle_qty <= 0:
                        continue

                    if not open_settle_ids:
                        fallback_ids = self.orders_api.open_order_ids(
                            code, side=targets["close_side"], cashmargin=CASHMARGIN_SETTLE
                        )
                        if fallback_ids:
                            open_settle_ids = fallback_ids
                            print(f"  [返済判定補正] {code} {label}: 建玉ID紐付け外の未約定返済注文 "
                                  f"{len(open_settle_ids)}件を取消対象に設定")

                    for oid in open_settle_ids:
                        print(f"  [返済取消] {code} {label}: OrderId={oid} (条件#6成立)")
                        self.trade.cancel_order(oid)
                    if self.order_env() is None:
                        print(f"  [DEV返済] {code} {label}: 成行 数量={settle_qty} "
                              f"(損切り: 現在値{current})")
                        continue
                    result = self.trade.send_credit_settle(
                        code,
                        targets["close_side"],
                        settle_qty,
                        price=0.0,
                        close_positions=[{"HoldID": pos.hold_id, "Qty": settle_qty}],
                        margin_trade_type=pos.margin_trade_type or 3,
                        exchange=pos.exchange,
                        env=self.order_env(),
                    )
                    tag = "返済注文(成行)"
                else:
                    # 条件#5 → 指値返済
                    # 建玉IDに紐づく未約定返済注文があればスキップ
                    if open_settle_ids:
                        continue
                    # 返済可能数量が 0 でも、未約定注文が検出できなければ建玉全体を指値返済する
                    if available_qty <= 0:
                        fallback_ids = self.orders_api.open_order_ids(
                            code, side=targets["close_side"], cashmargin=CASHMARGIN_SETTLE
                        )
                        if fallback_ids:
                            continue
                        settle_qty = pos.qty
                    else:
                        settle_qty = available_qty
                    if settle_qty <= 0:
                        continue
                    limit_price = round_to_tick(targets["limit_price"], group)
                    if self.order_env() is None:
                        print(f"  [DEV返済] {code} {label}: 指値{limit_price} 数量={settle_qty} "
                              f"(指定金額={spread})")
                        continue
                    result = self.trade.send_credit_settle(
                        code,
                        targets["close_side"],
                        settle_qty,
                        price=limit_price,
                        close_positions=[{"HoldID": pos.hold_id, "Qty": settle_qty}],
                        margin_trade_type=pos.margin_trade_type or 3,
                        exchange=pos.exchange,
                        env=self.order_env(),
                    )
                    tag = f"返済注文(指値{limit_price})"

                if result.ok:
                    print(f"  [{tag}] {code} {label}: 数量={settle_qty} "
                          f"OrderId={result.order_id} (指定金額={spread})")
                else:
                    if result.is_one_shot_amount_error:
                        self._set_settle_cooldown(code, pos.hold_id)
                        print(
                            f"  [返済注文エラー(クールダウン)] {code} {label}: "
                            f"{result.one_shot_message}"
                        )
                    elif result.is_settle_spec_error:
                        # Code=8「決済指定内容に誤りがあります」は、同じ建玉に対して
                        # 既に返済注文が入っている（ポジションが返済中）可能性が高い。
                        # 連続した無駄な発注を避けるためクールダウンを設定する。
                        self._set_settle_cooldown(code, pos.hold_id)
                        print(
                            f"  [返済注文エラー(クールダウン)] {code} {label}: "
                            f"{result.message} "
                            f"(同じ建玉に対して既に未約定の返済注文がある可能性があります。"
                            f"{self.SETTLE_ERROR_COOLDOWN_SECONDS:.0f}秒間返済注文を抑制します)"
                        )
                    else:
                        print(f"  [返済注文エラー] {code} {label}: {result.message}")
                    if result.request_body:
                        print(f"  [返済注文リクエスト] {code} {label}: {result.request_body}")
