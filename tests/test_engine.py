"""TradingEngine の各事象処理をフェイクAPIでテストする。"""

import os
import tempfile
import unittest

from kabu_api import KabuApiConfig
from kabu_api.engine import TradingEngine
from kabu_api.positions import CashHolding, CreditPosition, Holdings
from kabu_api.trading import OrderResult


# ======================================================================
# フェイク (APIに接続しない)
# ======================================================================
class FakePositionsApi:
    def __init__(self, holdings):
        self.holdings = holdings

    def build_holdings(self, env="prod"):
        return self.holdings


class FakeOrdersApi:
    def __init__(self, open_ids=None, open_by_hold_id=None):
        self.open_ids = open_ids or {}
        self.open_by_hold_id = open_by_hold_id or {}

    def has_open_order(self, symbol, side=None, cashmargin=None, product="0", env="prod"):
        return bool(self.open_ids.get(symbol))

    def open_order_ids(self, symbol, side=None, cashmargin=None, product="0", env="prod"):
        return list(self.open_ids.get(symbol, []))

    def open_order_ids_by_hold_id(self, symbol, hold_id, cashmargin=None, product="0", env="prod"):
        return list(self.open_by_hold_id.get((symbol, hold_id), []))


class FakeSymbolApi:
    def __init__(self, prices, groups=None):
        self.prices = prices
        self.groups = groups or {}

    def get_current_price(self, symbol, exchange=1, env="prod"):
        return self.prices.get(symbol, 0.0)

    def unit_and_group(self, symbol, env="prod"):
        return self.groups.get(symbol, (100, "10000"))


class FakeTrade:
    def __init__(self, settle_responses=None):
        self.calls = []
        self.settle_responses = list(settle_responses or [])

    def _rec(self, name, **kwargs):
        self.calls.append((name, kwargs))
        return OrderResult(ok=True, order_id="TEST0001")

    def send_cash_buy(self, symbol, qty, price=0.0, env="prod", expire_day=0):
        return self._rec("cash_buy", symbol=symbol, qty=qty, price=price)

    def send_cash_sell(self, symbol, qty, price=0.0, env="prod", expire_day=0):
        return self._rec("cash_sell", symbol=symbol, qty=qty, price=price)

    def send_credit_settle(self, symbol, close_side, qty, price=0.0,
                           close_positions=None, margin_trade_type=3,
                           exchange=9, env="prod", expire_day=0):
        self.calls.append(("credit_settle", {
            "symbol": symbol, "close_side": close_side, "qty": qty,
            "price": price, "close_positions": close_positions,
            "exchange": exchange,
        }))
        if self.settle_responses:
            return self.settle_responses.pop(0)
        return OrderResult(ok=True, order_id="TEST0001")

    def cancel_order(self, order_id, env="prod"):
        self.calls.append(("cancel", {"order_id": order_id}))
        return True


def make_holdings(cash=None, credit=None):
    h = Holdings()
    for c in cash or []:
        h.cash[c.symbol] = c
    for p in credit or []:
        h.credit.setdefault(p.symbol, []).append(p)
    return h


def make_engine(tmpdir, buy_content, sell_content="", ret_content="", nosell_content="",
                holdings=None, prices=None, groups=None, open_ids=None, open_by_hold_id=None,
                trade=None):
    paths = {}
    for name, content in (("buy", buy_content), ("sell", sell_content),
                          ("ret", ret_content), ("nosell", nosell_content)):
        p = os.path.join(tmpdir, f"list_{name}.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        paths[name] = p

    engine = TradingEngine(
        config=KabuApiConfig(),
        mode="prod",
        cycle=9999,
        buylist_path=paths["buy"],
        selllist_path=paths["sell"],
        retlist_path=paths["ret"],
        sellnolist_path=paths["nosell"],
    )
    engine.positions_api = FakePositionsApi(holdings or Holdings())
    engine.orders_api = FakeOrdersApi(open_ids, open_by_hold_id)
    engine.symbol_api = FakeSymbolApi(prices or {}, groups or {})
    engine.trade = trade or FakeTrade()
    return engine


class TestBuyProcessing(unittest.TestCase):
    def test_limit_buy(self):
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(d, "7203,2650,100,0\n", prices={"7203": 2600.0})
            e.run_cycle()
            buys = [c for c in e.trade.calls if c[0] == "cash_buy"]
            self.assertEqual(len(buys), 1)
            self.assertEqual(buys[0][1]["symbol"], "7203")
            self.assertEqual(buys[0][1]["price"], 2650.0)

    def test_market_buy_by_trigger(self):
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(d, "9602,0,100,2400\n", prices={"9602": 2390.0})
            e.run_cycle()
            buys = [c for c in e.trade.calls if c[0] == "cash_buy"]
            self.assertEqual(len(buys), 1)
            self.assertEqual(buys[0][1]["price"], 0.0)  # 成行

    def test_limit_buy_when_trigger_not_met(self):
        # トリガー未達でも条件#1(指値)は常時成立 → 指値買を出す
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(d, "7203,2650,100,2400\n", prices={"7203": 2500.0})
            e.run_cycle()
            buys = [c for c in e.trade.calls if c[0] == "cash_buy"]
            self.assertEqual(len(buys), 1)
            self.assertEqual(buys[0][1]["price"], 2650.0)

    def test_invalid_code_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(d, "xxxx,2650,100,0\n7203,2650,100,0\n", prices={"7203": 2600.0})
            e.run_cycle()
            buys = [c for c in e.trade.calls if c[0] == "cash_buy"]
            self.assertEqual(len(buys), 1)
            self.assertEqual(buys[0][1]["symbol"], "7203")

    def test_no_duplicate_while_pending(self):
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(d, "7203,2650,100,0\n", prices={"7203": 2600.0})
            e.run_cycle()
            # 2サイクル目: 未約定注文が残っている
            e.orders_api.open_ids = {"7203": ["TEST0001"]}
            e.run_cycle()
            buys = [c for c in e.trade.calls if c[0] == "cash_buy"]
            self.assertEqual(len(buys), 1)  # 増えない

    def test_fill_disables_row(self):
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(
                d, "7203,0,100,2400\n",
                holdings=make_holdings(cash=[CashHolding("7203", 100, 2395.0, 2400.0)]),
                prices={"7203": 2400.0},
            )
            e.pending_buys["7203"] = {"order_id": "TEST0001", "qty": 100}
            e.run_cycle()  # open_ids 空なので注文終了 → 約定 → 無効化
            with open(e.buylist_path, encoding="utf-8") as f:
                lines = f.readlines()
            self.assertTrue(lines[0].startswith("#7203"))
            self.assertNotIn("7203", e.pending_buys)

    def test_dev_mode_skips(self):
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(d, "7203,2650,100,0\n", prices={"7203": 2600.0})
            e.mode = "dev"
            e.run_cycle()
            buys = [c for c in e.trade.calls if c[0] == "cash_buy"]
            self.assertEqual(len(buys), 0)


class TestSellProcessing(unittest.TestCase):
    def test_limit_sell_all(self):
        # qty=0 → 全数売り
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(
                d, "", sell_content="6501,3800,0,0\n",
                holdings=make_holdings(cash=[CashHolding("6501", 200, 3500.0, 3700.0)]),
            )
            e.run_cycle()
            sells = [c for c in e.trade.calls if c[0] == "cash_sell"]
            self.assertEqual(len(sells), 1)
            self.assertEqual(sells[0][1]["qty"], 200)     # 全数
            self.assertEqual(sells[0][1]["price"], 3800.0)

    def test_qty_over_hold_means_all(self):
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(
                d, "", sell_content="6501,3800,500,0\n",
                holdings=make_holdings(cash=[CashHolding("6501", 100, 3500.0, 3700.0)]),
            )
            e.run_cycle()
            sells = [c for c in e.trade.calls if c[0] == "cash_sell"]
            self.assertEqual(sells[0][1]["qty"], 100)

    def test_not_held_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(d, "", sell_content="6501,3800,0,0\n")
            e.run_cycle()
            sells = [c for c in e.trade.calls if c[0] == "cash_sell"]
            self.assertEqual(len(sells), 0)

    def test_no_sell_list_excludes(self):
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(
                d, "", sell_content="6501,3800,0,0\n", nosell_content="6501\n",
                holdings=make_holdings(cash=[CashHolding("6501", 100, 3500.0, 3700.0)]),
            )
            e.run_cycle()
            sells = [c for c in e.trade.calls if c[0] == "cash_sell"]
            self.assertEqual(len(sells), 0)

    def test_trigger_market_cancels_open_limit(self):
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(
                d, "", sell_content="6501,0,0,3800\n",
                holdings=make_holdings(cash=[CashHolding("6501", 100, 3500.0, 3810.0)]),
                open_ids={"6501": ["OPEN1"]},
            )
            e.run_cycle()
            cancels = [c for c in e.trade.calls if c[0] == "cancel"]
            sells = [c for c in e.trade.calls if c[0] == "cash_sell"]
            self.assertEqual(len(cancels), 1)
            self.assertEqual(len(sells), 1)
            self.assertEqual(sells[0][1]["price"], 0.0)  # 成行

    def test_skip_when_open_limit_exists(self):
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(
                d, "", sell_content="6501,3800,0,0\n",
                holdings=make_holdings(cash=[CashHolding("6501", 100, 3500.0, 3700.0)]),
                open_ids={"6501": ["OPEN1"]},
            )
            e.run_cycle()
            sells = [c for c in e.trade.calls if c[0] == "cash_sell"]
            self.assertEqual(len(sells), 0)


class TestSettlementProcessing(unittest.TestCase):
    def test_limit_settle_long(self):
        # 買建 1000円, spread 10 → 1010円で売り返済
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(
                d, "", ret_content="7203,10,50\n",
                holdings=make_holdings(credit=[
                    CreditPosition(hold_id="E123", symbol="7203", side="2", price=1000.0, qty=100, current_price=1005.0, margin_trade_type=3),
                ]),
            )
            e.run_cycle()
            settles = [c for c in e.trade.calls if c[0] == "credit_settle"]
            self.assertEqual(len(settles), 1)
            self.assertEqual(settles[0][1]["close_side"], "1")     # 売り返済
            self.assertEqual(settles[0][1]["price"], 1010.0)
            self.assertEqual(settles[0][1]["qty"], 100)
            self.assertEqual(settles[0][1]["close_positions"],
                             [{"HoldID": "E123", "Qty": 100}])
            self.assertEqual(settles[0][1]["exchange"], 9)  # デフォルト SOR

    def test_limit_settle_uses_position_exchange(self):
        # 建玉市場が東証(1)の場合、返済注文も Exchange=1 で発注する
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(
                d, "", ret_content="7203,10,50\n",
                holdings=make_holdings(credit=[
                    CreditPosition(hold_id="E123", symbol="7203", side="2",
                                   price=1000.0, qty=100, current_price=1005.0,
                                   margin_trade_type=3, exchange=1),
                ]),
            )
            e.run_cycle()
            settles = [c for c in e.trade.calls if c[0] == "credit_settle"]
            self.assertEqual(len(settles), 1)
            self.assertEqual(settles[0][1]["exchange"], 1)

    def test_limit_settle_short(self):
        # 売建 2000円, spread 10 → 1990円で買い返済
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(
                d, "", ret_content="8306,10,50\n",
                holdings=make_holdings(credit=[
                    CreditPosition(hold_id="E999", symbol="8306", side="1", price=2000.0, qty=100, current_price=1990.0, margin_trade_type=3),
                ]),
            )
            e.run_cycle()
            settles = [c for c in e.trade.calls if c[0] == "credit_settle"]
            self.assertEqual(len(settles), 1)
            self.assertEqual(settles[0][1]["close_side"], "2")     # 買い返済
            self.assertEqual(settles[0][1]["price"], 1990.0)

    def test_stop_loss_market_cancels(self):
        # 買建 1000円, spread 10 → 現在値 950 で損切り (取消 + 成行)
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(
                d, "", ret_content="7203,10,50\n",
                holdings=make_holdings(credit=[
                    CreditPosition(hold_id="E123", symbol="7203", side="2", price=1000.0, qty=100, current_price=950.0, margin_trade_type=3),
                ]),
                open_by_hold_id={("7203", "E123"): ["OPEN1"]},
            )
            e.run_cycle()
            cancels = [c for c in e.trade.calls if c[0] == "cancel"]
            settles = [c for c in e.trade.calls if c[0] == "credit_settle"]
            self.assertEqual(len(cancels), 1)
            self.assertEqual(len(settles), 1)
            self.assertEqual(settles[0][1]["price"], 0.0)          # 成行
            self.assertEqual(settles[0][1]["close_side"], "1")


    def test_stop_loss_when_fully_held(self):
        # 条件#5 の指値注文が出済みで建玉全体が拘束中（hold_qty == qty）でも、
        # 条件#6 が成立すれば未約定注文を取消して成行返済する
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(
                d, "", ret_content="7203,10,50\n",
                holdings=make_holdings(credit=[
                    CreditPosition(hold_id="E123", symbol="7203", side="2", price=1000.0,
                                   qty=100, hold_qty=100, current_price=950.0, margin_trade_type=3),
                ]),
                open_by_hold_id={("7203", "E123"): ["OPEN1"]},
            )
            e.run_cycle()
            cancels = [c for c in e.trade.calls if c[0] == "cancel"]
            settles = [c for c in e.trade.calls if c[0] == "credit_settle"]
            self.assertEqual(len(cancels), 1)
            self.assertEqual(len(settles), 1)

    def test_stop_loss_falls_back_to_symbol_level_orders(self):
        # 建玉IDに紐づく未約定返済注文が検出できない場合でも、
        # 銘柄単位の未約定返済注文を取消して成行返済する
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(
                d, "", ret_content="5016,,\n",
                holdings=make_holdings(credit=[
                    CreditPosition(hold_id="E5016", symbol="5016", side="1", price=3560.0,
                                   qty=100, hold_qty=100, current_price=3566.0, margin_trade_type=3),
                ]),
                groups={"5016": (100, "10003")},  # TOPIX500: 3560円 → tick=1
                open_ids={"5016": ["OPEN_F1"]},
            )
            e.run_cycle()
            cancels = [c for c in e.trade.calls if c[0] == "cancel"]
            settles = [c for c in e.trade.calls if c[0] == "credit_settle"]
            self.assertEqual(len(cancels), 1)
            self.assertEqual(cancels[0][1]["order_id"], "OPEN_F1")
            self.assertEqual(len(settles), 1)
            self.assertEqual(settles[0][1]["price"], 0.0)          # 成行
            self.assertEqual(settles[0][1]["qty"], 100)            # 全建玉を返済
            self.assertEqual(settles[0][1]["close_side"], "2")     # 買い返済

    def test_spread_zero_uses_tick(self):
        # spread=0 → 呼値単位 (1000円 → 1円)
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(
                d, "", ret_content="7203,,\n",
                holdings=make_holdings(credit=[
                    CreditPosition(hold_id="E123", symbol="7203", side="2", price=1000.0, qty=100, current_price=1005.0, margin_trade_type=3),
                ]),
            )
            e.run_cycle()
            settles = [c for c in e.trade.calls if c[0] == "credit_settle"]
            self.assertEqual(settles[0][1]["price"], 1001.0)

    def test_stop_loss_ignores_hold_qty_when_no_open_order(self):
        # hold_qty == qty でも未約定返済注文がなければ、条件#6 で成行返済する
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(
                d, "", ret_content="5016,,\n",
                holdings=make_holdings(credit=[
                    CreditPosition(hold_id="E5016", symbol="5016", side="1", price=3560.0,
                                   qty=100, hold_qty=100, current_price=3566.0, margin_trade_type=3),
                ]),
                groups={"5016": (100, "10003")},
            )
            e.run_cycle()
            cancels = [c for c in e.trade.calls if c[0] == "cancel"]
            settles = [c for c in e.trade.calls if c[0] == "credit_settle"]
            self.assertEqual(len(cancels), 0)                       # 取消対象がない
            self.assertEqual(len(settles), 1)
            self.assertEqual(settles[0][1]["price"], 0.0)          # 成行
            self.assertEqual(settles[0][1]["qty"], 100)
            self.assertEqual(settles[0][1]["close_side"], "2")     # 買い返済

    def test_no_credit_position_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(d, "", ret_content="7203,10,50\n")
            e.run_cycle()
            settles = [c for c in e.trade.calls if c[0] == "credit_settle"]
            self.assertEqual(len(settles), 0)

    def test_skip_when_open_settle_exists(self):
        with tempfile.TemporaryDirectory() as d:
            e = make_engine(
                d, "", ret_content="7203,10,50\n",
                holdings=make_holdings(credit=[
                    CreditPosition(hold_id="E123", symbol="7203", side="2", price=1000.0, qty=100, current_price=1005.0, margin_trade_type=3),
                ]),
                open_by_hold_id={("7203", "E123"): ["OPEN1"]},
            )
            e.run_cycle()
            settles = [c for c in e.trade.calls if c[0] == "credit_settle"]
            self.assertEqual(len(settles), 0)  # 指値返済済み → 追加発注しない


class TestSettlementOneShotCooldown(unittest.TestCase):
    def test_one_shot_error_sets_cooldown_and_skips_next_cycle(self):
        """ワンショット金額エラー発生時はクールダウンを設定し、次のサイクルは抑制する。"""
        with tempfile.TemporaryDirectory() as d:
            error_result = OrderResult(
                ok=False,
                status_code=400,
                response_body='{"Code":4003001,"Message":"ワンショット：金額エラー"}',
                message="POST /sendorder が失敗しました (status=400)",
                request_body={"Symbol": "285A", "Qty": 100, "Price": 0.0},
            )
            trade = FakeTrade(settle_responses=[error_result, OrderResult(ok=True)])
            e = make_engine(
                d, "", ret_content="285A,,\n",
                holdings=make_holdings(credit=[
                    CreditPosition(hold_id="E285A", symbol="285A", side="1", price=17130.0,
                                   qty=100, current_price=17170.0, margin_trade_type=3),
                ]),
                trade=trade,
            )
            # 1サイクル目: 条件#6成立、成行返済を試みるがワンショットエラー
            e.run_cycle()
            settles = [c for c in e.trade.calls if c[0] == "credit_settle"]
            self.assertEqual(len(settles), 1)
            self.assertIn("285A:E285A", e.settle_error_cooldown)

            # 2サイクル目: クールダウン中なので send_credit_settle は呼ばれない
            e.run_cycle()
            settles = [c for c in e.trade.calls if c[0] == "credit_settle"]
            self.assertEqual(len(settles), 1)  # 増えない

    def test_cooldown_expires_and_retries(self):
        """クールダウン期限が切れたら再試行する。"""
        with tempfile.TemporaryDirectory() as d:
            error_result = OrderResult(
                ok=False,
                status_code=400,
                response_body='{"Code":4003001,"Message":"ワンショット：金額エラー"}',
                message="POST /sendorder が失敗しました (status=400)",
                request_body={"Symbol": "285A", "Qty": 100, "Price": 0.0},
            )
            trade = FakeTrade(settle_responses=[error_result, OrderResult(ok=True)])
            e = make_engine(
                d, "", ret_content="285A,,\n",
                holdings=make_holdings(credit=[
                    CreditPosition(hold_id="E285A", symbol="285A", side="1", price=17130.0,
                                   qty=100, current_price=17170.0, margin_trade_type=3),
                ]),
                trade=trade,
            )
            e.SETTLE_ERROR_COOLDOWN_CYCLES = 0  # 即期限切れ
            e.run_cycle()
            e.run_cycle()
            settles = [c for c in e.trade.calls if c[0] == "credit_settle"]
            self.assertEqual(len(settles), 2)


class TestSettlementSpecCooldown(unittest.TestCase):
    def test_settle_spec_error_sets_cooldown_and_skips_next_cycle(self):
        """Code=8 決済指定内容エラー発生時はクールダウンを設定し、次のサイクルは抑制する。"""
        with tempfile.TemporaryDirectory() as d:
            error_result = OrderResult(
                ok=False,
                status_code=500,
                response_body='{"Code":8,"Message":"決済指定内容に誤りがあります"}',
                message="POST /sendorder が失敗しました (status=500)",
                request_body={"Symbol": "5016", "Qty": 100, "Price": 3591},
            )
            trade = FakeTrade(settle_responses=[error_result, OrderResult(ok=True)])
            e = make_engine(
                d, "", ret_content="5016,,\n",
                holdings=make_holdings(credit=[
                    CreditPosition(hold_id="E2026100904PD1", symbol="5016", side="2",
                                   price=3589.7, qty=100, current_price=3590.0,
                                   margin_trade_type=3),
                ]),
                trade=trade,
            )
            # 1サイクル目: 条件#5成立、指値返済を試みるが Code=8 エラー
            e.run_cycle()
            settles = [c for c in e.trade.calls if c[0] == "credit_settle"]
            self.assertEqual(len(settles), 1)
            self.assertIn("5016:E2026100904PD1", e.settle_error_cooldown)

            # 2サイクル目: クールダウン中なので send_credit_settle は呼ばれない
            e.run_cycle()
            settles = [c for c in e.trade.calls if c[0] == "credit_settle"]
            self.assertEqual(len(settles), 1)  # 増えない


class TestMarketHours(unittest.TestCase):
    def test_market_hours(self):
        from datetime import datetime
        from kabu_api.engine import MarketHours
        self.assertTrue(MarketHours.is_market_hours(datetime(2026, 9, 5, 9, 5)))
        self.assertTrue(MarketHours.is_market_hours(datetime(2026, 9, 5, 10, 0)))
        self.assertFalse(MarketHours.is_market_hours(datetime(2026, 9, 5, 11, 31)))
        self.assertFalse(MarketHours.is_market_hours(datetime(2026, 9, 5, 12, 0)))
        self.assertTrue(MarketHours.is_market_hours(datetime(2026, 9, 5, 12, 30)))
        self.assertTrue(MarketHours.is_market_hours(datetime(2026, 9, 5, 15, 24)))
        self.assertFalse(MarketHours.is_market_hours(datetime(2026, 9, 5, 15, 25)))
        self.assertFalse(MarketHours.is_market_hours(datetime(2026, 9, 5, 3, 0)))


if __name__ == "__main__":
    unittest.main()
