"""conditions / symbol_info (tick計算) のテスト。"""

import unittest

from kabu_api.conditions import buy_condition, sell_condition, settle_signal, settle_targets
from kabu_api.symbol_info import GROUP_NORMAL, GROUP_TOPIX500, round_to_tick, tick_size


class TestBuyCondition(unittest.TestCase):
    def test_limit_buy(self):
        # 条件#1: 指値指定
        self.assertEqual(buy_condition(2500, 0, 2600), "limit")

    def test_market_buy_zero_price(self):
        # 条件#1: 0指定 → 成行
        self.assertEqual(buy_condition(0, 0, 2600), "market")

    def test_trigger_below(self):
        # 条件#2: 現在値がトリガー以下 → 成行
        self.assertEqual(buy_condition(2500, 2400, 2399), "market")
        self.assertEqual(buy_condition(2500, 2400, 2400), "market")

    def test_no_condition(self):
        # トリガー未達で指値指定 → 条件#1の指値
        self.assertEqual(buy_condition(2500, 2400, 2450), "limit")
        # 全部0 → 成行 (0指定ルール)
        self.assertEqual(buy_condition(0, 0, 3000), "market")
        # 指値もトリガーも無しで0でない現在地 → 成行(0指定)
        self.assertIsNotNone(buy_condition(0, 0, 100))


class TestSellCondition(unittest.TestCase):
    def test_limit_sell(self):
        self.assertEqual(sell_condition(3000, 0, 2900), "limit")

    def test_market_sell_zero_price(self):
        self.assertEqual(sell_condition(0, 0, 2900), "market")

    def test_trigger_above(self):
        # 条件#4: 現在値がトリガー以上 → 成行
        self.assertEqual(sell_condition(3000, 3200, 3201), "market")
        self.assertEqual(sell_condition(3000, 3200, 3200), "market")

    def test_no_condition(self):
        self.assertEqual(sell_condition(3000, 3200, 3100), "limit")


class TestSettleTargets(unittest.TestCase):
    def test_long_position(self):
        # 買建 (Side=2): 確定買値+spread_plus で売り返済 / 確定買値-spread_minus で損切り
        t = settle_targets("2", 1000.0, 10.0, 50.0)
        self.assertEqual(t["close_side"], "1")
        self.assertAlmostEqual(t["limit_price"], 1010.0)
        self.assertAlmostEqual(t["stop_current"], 950.0)

    def test_short_position(self):
        # 売建 (Side=1): 確定売値-spread_plus で買い返済 / 確定売値+spread_minus で損切り
        t = settle_targets("1", 1000.0, 10.0, 50.0)
        self.assertEqual(t["close_side"], "2")
        self.assertAlmostEqual(t["limit_price"], 990.0)
        self.assertAlmostEqual(t["stop_current"], 1050.0)

    def test_spec_example(self):
        # 仕様例: 買値1000, spread_plus=1, spread_minus=5 → +1円で売 / -5円で損切り
        t = settle_targets("2", 1000.0, 1.0, 5.0)
        self.assertAlmostEqual(t["limit_price"], 1001.0)
        self.assertAlmostEqual(t["stop_current"], 995.0)

    def test_invalid_side(self):
        t = settle_targets("9", 1000.0, 10.0, 50.0)
        self.assertEqual(t["close_side"], "")


class TestSettleSignal(unittest.TestCase):
    def test_stop_market_long(self):
        t = settle_targets("2", 1000.0, 10.0, 50.0)
        self.assertEqual(settle_signal(t, 950.0), "market")
        self.assertEqual(settle_signal(t, 949.0), "market")

    def test_limit_long(self):
        t = settle_targets("2", 1000.0, 10.0, 50.0)
        self.assertEqual(settle_signal(t, 1000.0), "limit")
        self.assertEqual(settle_signal(t, 900.0), "market")  # 損切り優先

    def test_stop_market_short(self):
        t = settle_targets("1", 1000.0, 10.0, 50.0)
        self.assertEqual(settle_signal(t, 1050.0), "market")

    def test_no_signal(self):
        t = settle_targets("2", 1000.0, 10.0, 50.0)
        # 条件#5 は「即時、返済金額で指す」が常時成立 (指値返済を維持)
        self.assertEqual(settle_signal(t, 1005.0), "limit")
        self.assertEqual(settle_signal(t, 900.0), "market")  # 損切り優先
        # 指値が0以下 (売建の確定売値 < spread_plus) は指値不可 → 条件#6に達していなければ発動しない
        t2 = settle_targets("1", 5.0, 10.0, 50.0)
        self.assertIsNone(settle_signal(t2, 10.0))
        # ただし損切りライン (確定売値+spread_minus=55円) を超えれば成行損切り
        self.assertEqual(settle_signal(t2, 100.0), "market")


class TestTickSize(unittest.TestCase):
    def test_normal_group(self):
        # 10000: 3000円以下=1, 5000円以下=5, 30000円以下=10, 50000円以下=50,
        #        300000円以下=100, 500000円以下=500, 3000000円以下=1000
        self.assertEqual(tick_size(1000, GROUP_NORMAL), 1)
        self.assertEqual(tick_size(3000, GROUP_NORMAL), 1)
        self.assertEqual(tick_size(3001, GROUP_NORMAL), 5)
        self.assertEqual(tick_size(5000, GROUP_NORMAL), 5)
        self.assertEqual(tick_size(5001, GROUP_NORMAL), 10)
        self.assertEqual(tick_size(20000, GROUP_NORMAL), 10)
        self.assertEqual(tick_size(30001, GROUP_NORMAL), 50)
        self.assertEqual(tick_size(100000, GROUP_NORMAL), 100)
        self.assertEqual(tick_size(300001, GROUP_NORMAL), 500)
        self.assertEqual(tick_size(500001, GROUP_NORMAL), 1000)
        self.assertEqual(tick_size(3000001, GROUP_NORMAL), 10000)

    def test_topix500_group(self):
        # 10003: 1000円以下=0.1 / 3000円以下=0.5 / 10000円以下=1 / 30000円以下=5 /
        #        100000円以下=10 / 300000円以下=50 / 1000000円以下=100
        self.assertEqual(tick_size(500, GROUP_TOPIX500), 0.1)
        self.assertEqual(tick_size(1000, GROUP_TOPIX500), 0.1)
        self.assertEqual(tick_size(1500, GROUP_TOPIX500), 0.5)
        self.assertEqual(tick_size(5000, GROUP_TOPIX500), 1)
        self.assertEqual(tick_size(10001, GROUP_TOPIX500), 5)
        self.assertEqual(tick_size(50001, GROUP_TOPIX500), 10)
        self.assertEqual(tick_size(100001, GROUP_TOPIX500), 50)
        self.assertEqual(tick_size(300001, GROUP_TOPIX500), 100)

    def test_unknown_group_fallback(self):
        self.assertEqual(tick_size(1000, "99999"), 1)

    def test_round_to_tick(self):
        self.assertEqual(round_to_tick(1001.3, GROUP_NORMAL), 1001.0)
        self.assertEqual(round_to_tick(3002, GROUP_NORMAL), 3000.0)
        self.assertEqual(round_to_tick(1001.04, GROUP_TOPIX500), 1001.0)


if __name__ == "__main__":
    unittest.main()
