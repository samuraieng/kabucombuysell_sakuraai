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
        self.assertAlmostEqual(t["watch_price"], 999.0)      # price - tick
        self.assertAlmostEqual(t["stop_history"], 999.0)
        self.assertAlmostEqual(t["stop_time_3s"], 1000.0)
        self.assertAlmostEqual(t["stop_time_5s"], 700.0)     # 1000 - 50*6

    def test_short_position(self):
        # 売建 (Side=1): 確定売値-spread_plus で買い返済 / 確定売値+spread_minus で損切り
        t = settle_targets("1", 1000.0, 10.0, 50.0)
        self.assertEqual(t["close_side"], "2")
        self.assertAlmostEqual(t["limit_price"], 990.0)
        self.assertAlmostEqual(t["stop_current"], 1050.0)
        self.assertAlmostEqual(t["watch_price"], 1001.0)
        self.assertAlmostEqual(t["stop_history"], 1001.0)
        self.assertAlmostEqual(t["stop_time_3s"], 1000.0)
        self.assertAlmostEqual(t["stop_time_5s"], 1300.0)

    def test_spec_example(self):
        # 仕様例: 買値1000, spread_plus=1, spread_minus=5, tick=1 → +1円で売 / -5円で損切り
        t = settle_targets("2", 1000.0, 1.0, 5.0, tick=1.0)
        self.assertAlmostEqual(t["limit_price"], 1001.0)
        self.assertAlmostEqual(t["stop_current"], 995.0)

    def test_invalid_side(self):
        t = settle_targets("9", 1000.0, 10.0, 50.0)
        self.assertEqual(t["close_side"], "")


class TestSettleSignal(unittest.TestCase):
    def test_stop_market_long(self):
        t = settle_targets("2", 1000.0, 10.0, 50.0)
        self.assertEqual(settle_signal(t, 950.0), ("market", "6-1"))
        self.assertEqual(settle_signal(t, 949.0), ("market", "6-1"))

    def test_limit_long(self):
        t = settle_targets("2", 1000.0, 10.0, 50.0)
        self.assertEqual(settle_signal(t, 1000.0), ("limit", "5"))
        self.assertEqual(settle_signal(t, 900.0), ("market", "6-1"))  # 損切り優先

    def test_stop_market_short(self):
        t = settle_targets("1", 1000.0, 10.0, 50.0)
        self.assertEqual(settle_signal(t, 1050.0), ("market", "6-1"))

    def test_no_signal(self):
        t = settle_targets("2", 1000.0, 10.0, 50.0)
        # 条件#5 は「即時、返済金額で指す」が常時成立 (指値返済を維持)
        self.assertEqual(settle_signal(t, 1005.0), ("limit", "5"))
        self.assertEqual(settle_signal(t, 900.0), ("market", "6-1"))  # 損切り優先
        # 指値が0以下 (売建の確定売値 < spread_plus) は指値不可 → 条件#6に達していなければ発動しない
        t2 = settle_targets("1", 5.0, 10.0, 50.0)
        self.assertEqual(settle_signal(t2, 10.0), (None, None))
        # ただし損切りライン (確定売値+spread_minus=55円) を超えれば損切り
        self.assertEqual(settle_signal(t2, 100.0), ("market", "6-1"))

    def test_history_stop_long(self):
        # 買建: 監視金額 999 を割れば buffer に 1。buffer_size=3 で 2 回連続で発火
        t = settle_targets("2", 1000.0, 10.0, 50.0)
        history: list[int] = []
        history.append(1)  # current=999 のフラグ（呼び出し側が更新）
        self.assertEqual(settle_signal(t, 999.0, history=history, buffer_size=3), ("limit", "5"))
        history.append(1)  # 2回目
        self.assertEqual(settle_signal(t, 999.0, history=history, buffer_size=3), ("market", "6-2"))
        self.assertEqual(history, [1, 1])

    def test_history_stop_short(self):
        # 売建: 監視金額 1001 を超えれば buffer に 1
        t = settle_targets("1", 1000.0, 10.0, 50.0)
        history: list[int] = []
        history.append(1)
        self.assertEqual(settle_signal(t, 1001.0, history=history, buffer_size=3), ("limit", "5"))
        history.append(1)
        self.assertEqual(settle_signal(t, 1001.0, history=history, buffer_size=3), ("market", "6-2"))

    def test_history_buffer_no_update(self):
        # settle_signal は history を更新しない（呼び出し側が更新する）
        t = settle_targets("2", 1000.0, 10.0, 50.0)
        history: list[int] = []
        settle_signal(t, 999.0, history=history, buffer_size=3)
        self.assertEqual(history, [])

    def test_history_zero_fills_buffer(self):
        # 条件を満たさないサイクルでは 0、満たすサイクルでは 1 が追加される（呼び出し側で）
        t = settle_targets("2", 1000.0, 10.0, 50.0)
        history: list[int] = [0, 1]
        self.assertEqual(settle_signal(t, 1000.0, history=history, buffer_size=3), ("limit", "5"))
        history.append(1)  # [0, 1, 1] で過半数に達し発火
        self.assertEqual(settle_signal(t, 999.0, history=history, buffer_size=3), ("market", "6-2"))

    def test_time_stop_3s_long(self):
        t = settle_targets("2", 1000.0, 10.0, 50.0)
        # 3秒経過で建玉価格（1000）を割れば損切り
        self.assertEqual(settle_signal(t, 999.0, elapsed_seconds=3.0), ("market", "6-3-3s"))
        # 3秒未満では条件#5
        self.assertEqual(settle_signal(t, 999.0, elapsed_seconds=2.9), ("limit", "5"))

    def test_time_stop_5s_long(self):
        t = settle_targets("2", 1000.0, 10.0, 5.0)
        # 5秒経過で強い損切り（970）を割れば発火
        self.assertEqual(settle_signal(t, 969.0, elapsed_seconds=5.0), ("market", "6-3-5s"))
        # 5秒経過でも 6-1(995) と 6-3-5s(970) の両方を割らなければ条件#5
        self.assertEqual(settle_signal(t, 996.0, elapsed_seconds=5.0), ("limit", "5"))

    def test_multiple_conditions_pick_worst(self):
        # 買建: 6-1(950) と 6-3-5s(700) が両方発火する場合、より不利な 6-3-5s を選択
        t = settle_targets("2", 1000.0, 10.0, 50.0)
        signal, reason = settle_signal(t, 600.0, elapsed_seconds=5.0)
        self.assertEqual(signal, "market")
        self.assertEqual(reason, "6-3-5s")

    def test_history_and_time_prefer_worst(self):
        # 買建: 6-2(999) と 6-3-3s(1000) が両方発火する場合、より不利な 6-2 を選択
        t = settle_targets("2", 1000.0, 10.0, 50.0)
        history: list[int] = [1, 1]  # 既に過半数が1
        signal, reason = settle_signal(t, 999.0, history=history, buffer_size=3, elapsed_seconds=3.0)
        self.assertEqual(signal, "market")
        self.assertEqual(reason, "6-2")


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
