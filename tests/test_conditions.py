"""conditions / symbol_info (tick計算) のテスト。"""

import unittest

from kabu_api.conditions import (
    buy_condition,
    sell_condition,
    settle_action,
    settle_signal,
    settle_status_condition5,
    settle_status_condition6_1,
    settle_status_condition6_2,
    settle_targets,
)
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


class TestSettleAction(unittest.TestCase):
    def test_condition5_limit(self):
        # 買建 side="2", price=1000, spread_plus=10, spread_minus=2, spread_minus_max=6, count_max=3
        # current=1000 → 条件#5 指値返済
        r = settle_action("2", 1000.0, 1000.0, 10.0, 2.0, 6.0, 3, settle_status_condition5(), 0)
        self.assertEqual(r["action"], "limit")
        self.assertAlmostEqual(r["price"], 1010.0)
        self.assertEqual(r["next_status"], settle_status_condition5())
        self.assertEqual(r["next_counter"], 0)

    def test_condition5_stop_loss(self):
        # current=993 → 条件#6-1 発火 (1000-6=994)
        r = settle_action("2", 1000.0, 993.0, 10.0, 2.0, 6.0, 3, settle_status_condition5(), 0)
        self.assertEqual(r["action"], "limit")
        self.assertAlmostEqual(r["price"], 994.0)
        self.assertEqual(r["next_status"], settle_status_condition6_1(6))
        self.assertEqual(r["next_counter"], 0)

    def test_condition5_history_first_hit(self):
        # current=998 → 条件#6-2 監視金額 (1000-2=998) に到達、count_max=3 ならまだ発火しない
        r = settle_action("2", 1000.0, 998.0, 10.0, 2.0, 6.0, 3, settle_status_condition5(), 0)
        self.assertEqual(r["action"], "none")
        self.assertEqual(r["next_status"], settle_status_condition6_2(2, 1, 3))
        self.assertEqual(r["next_counter"], 1)

    def test_condition5_history_fire_count_max_1(self):
        # count_max=1 の場合、初回ヒットで即座に成行
        r = settle_action("2", 1000.0, 998.0, 10.0, 2.0, 6.0, 1, settle_status_condition5(), 0)
        self.assertEqual(r["action"], "market")
        self.assertEqual(r["next_status"], settle_status_condition6_2(2, 1, 1))

    def test_condition6_2_fire(self):
        # status=condition-6-2-2-1-3, current=998 → counter=2 で発火
        r = settle_action("2", 1000.0, 998.0, 10.0, 2.0, 6.0, 3,
                          settle_status_condition6_2(2, 1, 3), 1)
        self.assertEqual(r["action"], "market")
        self.assertEqual(r["next_status"], settle_status_condition6_2(2, 2, 3))
        self.assertEqual(r["next_counter"], 2)

    def test_condition6_2_reset_on_miss(self):
        # status=condition-6-2-2-1-3, current=1000 → 監視金額を割らないので counter=0 にリセット
        r = settle_action("2", 1000.0, 1000.0, 10.0, 2.0, 6.0, 3,
                          settle_status_condition6_2(2, 1, 3), 1)
        self.assertEqual(r["action"], "none")
        self.assertEqual(r["next_status"], settle_status_condition6_2(2, 0, 3))
        self.assertEqual(r["next_counter"], 0)

    def test_condition6_1_to_condition6_2_fire(self):
        # status=condition-6-1, current=998 → 条件#6-2 ヒット、counter=1 から count_max=3 ならまだ発火しない
        r = settle_action("2", 1000.0, 998.0, 10.0, 2.0, 6.0, 3,
                          settle_status_condition6_1(6), 0)
        self.assertEqual(r["action"], "none")
        self.assertEqual(r["next_status"], settle_status_condition6_2(2, 1, 3))

        # 次のサイクルでも current=998 → counter=2 で発火
        r2 = settle_action("2", 1000.0, 998.0, 10.0, 2.0, 6.0, 3,
                           r["next_status"], r["next_counter"])
        self.assertEqual(r2["action"], "market")

    def test_condition6_1_maintain(self):
        # status=condition-6-1, current=1000 → #6-2 未ヒット、#6-1 指値維持
        r = settle_action("2", 1000.0, 1000.0, 10.0, 2.0, 6.0, 3,
                          settle_status_condition6_1(6), 0)
        self.assertEqual(r["action"], "limit")
        self.assertAlmostEqual(r["price"], 994.0)
        self.assertEqual(r["next_status"], settle_status_condition6_1(6))

    def test_normalize_spreads(self):
        # spread_minus > spread_minus_max の場合は入れ替える
        # price=1000, spread_minus=10, spread_minus_max=2 → spread_minus=2, spread_minus_max=10
        # 監視金額=998, 損切り金額=990
        r = settle_action("2", 1000.0, 990.0, 10.0, 10.0, 2.0, 3, settle_status_condition5(), 0)
        self.assertEqual(r["action"], "limit")
        self.assertAlmostEqual(r["price"], 990.0)
        self.assertEqual(r["next_status"], settle_status_condition6_1(10))

    def test_short_position(self):
        # 売建 side="1", price=1000, spread_plus=10, spread_minus=2, spread_minus_max=6
        # current=1001 → 条件#5 指値 (990)
        r = settle_action("1", 1000.0, 1001.0, 10.0, 2.0, 6.0, 3, settle_status_condition5(), 0)
        self.assertEqual(r["action"], "limit")
        self.assertAlmostEqual(r["price"], 990.0)

        # current=1003 → 条件#6-2 監視金額 (1002) に到達 (条件#6-1 の 1006 には到達しない)
        r = settle_action("1", 1000.0, 1003.0, 10.0, 2.0, 6.0, 3, settle_status_condition5(), 0)
        self.assertEqual(r["action"], "none")
        self.assertEqual(r["next_status"], settle_status_condition6_2(2, 1, 3))

        # current=1007 → 条件#6-1 損切り金額 (1006) に到達
        r = settle_action("1", 1000.0, 1007.0, 10.0, 2.0, 6.0, 3, settle_status_condition5(), 0)
        self.assertEqual(r["action"], "limit")
        self.assertAlmostEqual(r["price"], 1006.0)
        self.assertEqual(r["next_status"], settle_status_condition6_1(6))


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
