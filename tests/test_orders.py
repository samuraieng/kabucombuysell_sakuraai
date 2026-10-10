"""kabu_api/orders.py の単体テスト。"""

import unittest

from kabu_api.orders import KabuApiOrders, OPEN_STATES


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, path, env="prod", params=None):
        self.calls.append((path, env, params))
        return self.responses.pop(0)


class TestKabuApiOrders(unittest.TestCase):
    def test_open_orders_passes_params_via_signature(self):
        """open_orders が fetch() のシグネチャに合わせて引数を渡すこと。"""
        orders_data = [
            {
                "Symbol": "7203",
                "State": "3",
                "LeavesQty": 100,
                "Side": "1",
                "CashMargin": 2,
            },
            {
                "Symbol": "7203",
                "State": "5",  # 終了状態なので除外
                "LeavesQty": 100,
                "Side": "2",
                "CashMargin": 2,
            },
        ]
        client = FakeClient([orders_data])
        api = KabuApiOrders(client)

        result = api.open_orders("7203", product="0", cashmargin=2, env="prod")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["State"], "3")
        self.assertEqual(client.calls, [
            ("/orders", "prod", {
                "product": "0",
                "details": "true",
                "symbol": "7203",
                "cashmargin": "2",
            }),
        ])

    def test_open_orders_without_cashmargin(self):
        """cashmargin 未指定時も正しく動作すること。"""
        orders_data = [
            {
                "Symbol": "7203",
                "State": "2",
                "LeavesQty": 50,
                "Side": "1",
            },
        ]
        client = FakeClient([orders_data])
        api = KabuApiOrders(client)

        result = api.open_orders("7203")

        self.assertEqual(len(result), 1)
        self.assertEqual(client.calls, [
            ("/orders", "prod", {
                "product": "0",
                "details": "true",
                "symbol": "7203",
            }),
        ])

    def test_open_order_ids_by_hold_id_matches_hold_id_field(self):
        """Details に HoldID フィールドがある場合、それで建玉を特定できること。"""
        orders_data = [
            {
                "Symbol": "5801",
                "State": "3",
                "LeavesQty": 100,
                "Side": "1",
                "CashMargin": 3,
                "OrderId": "ORDER001",
                "Details": [
                    {"SeqNo": 1, "HoldID": "E20261009059NO", "Qty": 100},
                ],
            },
        ]
        client = FakeClient([orders_data])
        api = KabuApiOrders(client)

        result = api.open_order_ids_by_hold_id("5801", "E20261009059NO", cashmargin=3)

        self.assertEqual(result, ["ORDER001"])

    def test_open_order_ids_by_hold_id_matches_execution_id_field(self):
        """Details に ExecutionID フィールドしかない場合も後方互換で動作すること。"""
        orders_data = [
            {
                "Symbol": "5801",
                "State": "3",
                "LeavesQty": 100,
                "Side": "1",
                "CashMargin": 3,
                "OrderId": "ORDER002",
                "Details": [
                    {"SeqNo": 1, "ExecutionID": "E20261009059NO", "Qty": 100},
                ],
            },
        ]
        client = FakeClient([orders_data])
        api = KabuApiOrders(client)

        result = api.open_order_ids_by_hold_id("5801", "E20261009059NO", cashmargin=3)

        self.assertEqual(result, ["ORDER002"])

    def test_open_orders_falls_back_to_orderqty_minus_cumqty(self):
        """LeavesQty が null の場合、OrderQty - CumQty で未約定数量を計算すること。"""
        orders_data = [
            {
                "Symbol": "5801",
                "State": 3,
                "LeavesQty": None,
                "OrderQty": 100.0,
                "CumQty": 0.0,
                "Side": "1",
                "CashMargin": 3,
                "ID": "ORDER003",
                "Details": [
                    {"SeqNo": 1, "ExecutionID": None},
                    {"SeqNo": 4, "ExchangeID": "gINfV_wOSnqpzA7Zp0e3RwA"},
                ],
            },
        ]
        client = FakeClient([orders_data])
        api = KabuApiOrders(client)

        result = api.open_orders("5801", cashmargin=3)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["ID"], "ORDER003")
        self.assertEqual(api._leaves_qty(result[0]), 100)

    def test_open_orders_excludes_fully_filled(self):
        """OrderQty == CumQty の場合、未約定注文から除外すること。"""
        orders_data = [
            {
                "Symbol": "5801",
                "State": 3,
                "LeavesQty": None,
                "OrderQty": 100.0,
                "CumQty": 100.0,
                "Side": "1",
                "CashMargin": 3,
                "ID": "ORDER004",
            },
        ]
        client = FakeClient([orders_data])
        api = KabuApiOrders(client)

        result = api.open_orders("5801", cashmargin=3)

        self.assertEqual(len(result), 0)


if __name__ == "__main__":
    unittest.main()
