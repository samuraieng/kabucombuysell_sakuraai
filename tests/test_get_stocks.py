"""get_stocks.py の単体テスト。"""

import unittest

from kabu_api.positions import PRODUCT_CASH, PRODUCT_MARGIN
from get_stocks import StockRetriever, StockRow, format_table


class FakePositionsApi:
    def __init__(self, cash=None, margin=None):
        self.cash = cash or []
        self.margin = margin or []

    def fetch(self, product, env="prod", symbol=None):
        if product == PRODUCT_CASH:
            return self.cash
        if product == PRODUCT_MARGIN:
            return self.margin
        return []


class FakeSymbolApi:
    def __init__(self, names=None):
        self.names = names or {}
        self.calls = []

    def get(self, code, exchange=1, addinfo=True, env="prod", use_cache=True):
        self.calls.append(code)
        return {"SymbolName": self.names.get(code, "")}


class TestStockRetriever(unittest.TestCase):
    def test_empty(self):
        retriever = StockRetriever(FakePositionsApi())
        self.assertEqual(retriever.retrieve(), [])

    def test_cash_aggregation(self):
        cash = [
            {
                "Symbol": "7203",
                "SymbolName": "トヨタ自動車",
                "LeavesQty": 100,
                "Price": 2500.0,
                "CurrentPrice": 2600.0,
                "ProfitLoss": 10000.0,
            },
            {
                "Symbol": "7203",
                "SymbolName": "トヨタ自動車",
                "LeavesQty": 200,
                "Price": 2550.0,
                "CurrentPrice": 2600.0,
                "ProfitLoss": 10000.0,
            },
        ]
        rows = StockRetriever(FakePositionsApi(cash=cash)).retrieve()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.code, "7203")
        self.assertEqual(row.name, "トヨタ自動車")
        self.assertEqual(row.account_type, "現物")
        self.assertEqual(row.qty, 300)
        self.assertAlmostEqual(row.avg_price, 2533.3333333, places=4)
        self.assertAlmostEqual(row.current_price, 2600.0)
        self.assertAlmostEqual(row.profit_loss, 20000.0)

    def test_margin_positions(self):
        margin = [
            {
                "Symbol": "8306",
                "SymbolName": "三菱UFJFG",
                "Side": "2",
                "LeavesQty": 100,
                "Price": 700.0,
                "CurrentPrice": 710.0,
                "ProfitLoss": 1000.0,
            },
            {
                "Symbol": "8306",
                "SymbolName": "三菱UFJFG",
                "Side": "1",
                "LeavesQty": 100,
                "Price": 710.0,
                "CurrentPrice": 700.0,
                "ProfitLoss": 1000.0,
            },
        ]
        rows = StockRetriever(FakePositionsApi(margin=margin)).retrieve()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r.account_type for r in rows}, {"信用買建", "信用売建"})

    def test_name_fallback(self):
        cash = [
            {
                "Symbol": "6758",
                "SymbolName": "",
                "LeavesQty": 100,
                "Price": 12000.0,
                "CurrentPrice": 12100.0,
                "ProfitLoss": 10000.0,
            }
        ]
        symbol_api = FakeSymbolApi({"6758": "ソニーグループ"})
        rows = StockRetriever(FakePositionsApi(cash=cash), symbol_api).retrieve()
        self.assertEqual(rows[0].name, "ソニーグループ")
        self.assertIn("6758", symbol_api.calls)


class TestFormatTable(unittest.TestCase):
    def test_no_positions(self):
        self.assertEqual(format_table([]), "保有銘柄はありません。")

    def test_output_contains_fields(self):
        rows = [
            StockRow(
                code="7203",
                name="トヨタ自動車",
                account_type="現物",
                qty=100,
                avg_price=2500.0,
                current_price=2600.0,
                profit_loss=10000.0,
            )
        ]
        out = format_table(rows)
        self.assertIn("7203", out)
        self.assertIn("トヨタ自動車", out)
        self.assertIn("現物", out)
        self.assertIn("100", out)
        self.assertIn("2,500.0", out)
        self.assertIn("2,600.0", out)
        self.assertIn("10,000.0", out)


if __name__ == "__main__":
    unittest.main()
