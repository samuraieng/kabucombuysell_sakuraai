"""指示ファイル読み込み・無効化のテスト。"""

import os
import tempfile
import unittest

from kabu_api.instruction_files import (
    BuyList,
    NoSellList,
    RetList,
    SellList,
    parse_csv_line,
    split_comment,
)


def write_tmp(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class TestParsing(unittest.TestCase):
    def test_split_comment(self):
        self.assertEqual(split_comment("# 全コメント"), "")
        self.assertEqual(split_comment("// 全コメント"), "")
        self.assertEqual(split_comment("9602,0,100,2400"), "9602,0,100,2400")
        self.assertEqual(split_comment("9602,0,100,2400 # コメント"), "9602,0,100,2400 ")
        self.assertEqual(split_comment("9602,0,100,2400 // コメント"), "9602,0,100,2400 ")
        self.assertEqual(split_comment(""), "")

    def test_parse_csv_line(self):
        self.assertEqual(parse_csv_line("9602,0,100,2400"), ["9602", "0", "100", "2400"])
        self.assertEqual(parse_csv_line("9602, 0 , 100 , 2400 # tail"),
                         ["9602", "0", "100", "2400"])
        self.assertEqual(parse_csv_line("# comment"), [])
        self.assertEqual(parse_csv_line("8306, // ret"), ["8306", ""])


class TestBuyList(unittest.TestCase):
    def test_load_and_disable(self):
        path = write_tmp(
            "# ヘッダー的コメント\n"
            "code,price,qty,trigger_price\n"
            "9602,0,100,2400\n"
            "7203,2650,100,0 // 指値買\n"
            "123456,1,2,3\n"
        )
        try:
            bl = BuyList(path).load()
            self.assertEqual(len(bl.rows), 2)  # ヘッダーと不正行は除外
            row0, row1 = bl.rows
            self.assertEqual(row0.code, "9602")
            self.assertEqual(row0.price, 0.0)
            self.assertEqual(row0.qty, 100)
            self.assertEqual(row0.trigger_price, 2400.0)
            self.assertEqual(row1.code, "7203")
            self.assertEqual(row1.price, 2650.0)

            # 無効化 → ファイルに行頭 # が付く
            self.assertTrue(bl.disable_row(row0))
            self.assertFalse(row0.active)
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
            self.assertTrue(lines[2].startswith("#9602"))
            self.assertTrue(lines[3].startswith("7203"))  # 他行は無傷

            # 再読込 → 無効化行はコメント扱いで rows から除外される
            bl2 = BuyList(path).load()
            self.assertEqual(len(bl2.rows), 1)
            self.assertEqual(bl2.rows[0].code, "7203")
            self.assertEqual(len(bl2.active_rows()), 1)
        finally:
            os.unlink(path)

    def test_missing_file(self):
        bl = BuyList("/nonexistent/list_buy.txt").load()
        self.assertEqual(bl.rows, [])
        self.assertEqual(bl.active_rows(), [])


class TestSellList(unittest.TestCase):
    def test_load(self):
        path = write_tmp("6501,3800,0,0\n7203,2800,100,2801\n")
        try:
            sl = SellList(path).load()
            self.assertEqual(len(sl.rows), 2)
            self.assertEqual(sl.rows[0].qty, 0)       # 全数指定
            self.assertEqual(sl.rows[1].trigger_price, 2801.0)
        finally:
            os.unlink(path)


class TestRetList(unittest.TestCase):
    def test_load(self):
        path = write_tmp("7203,10,50,5\n9433,,,\n8306,0,0,0\n5016,,\n")
        try:
            rl = RetList(path).load()
            self.assertEqual(rl.rows[0].spread_plus, 10.0)
            self.assertEqual(rl.rows[0].spread_minus, 50.0)
            self.assertEqual(rl.rows[0].buffer_size, 5)
            self.assertEqual(rl.rows[1].spread_plus, 0.0)  # 空欄 → 0
            self.assertEqual(rl.rows[1].spread_minus, 0.0)
            self.assertEqual(rl.rows[1].buffer_size, 0)    # 空欄 → 0
            self.assertEqual(rl.rows[2].spread_plus, 0.0)
            self.assertEqual(rl.rows[2].spread_minus, 0.0)
            self.assertEqual(rl.rows[2].buffer_size, 0)
            self.assertEqual(rl.rows[3].buffer_size, 0)    # 列なし → 0
            # effective_*: 呼値未取得時のフォールバック
            self.assertEqual(rl.rows[1].effective_spread_plus(), 1.0)
            self.assertEqual(rl.rows[1].effective_spread_minus(), 5.0)
            self.assertEqual(rl.rows[1].effective_buffer_size(), 3)
            rl.rows[1].spread_plus = 5.0
            rl.rows[1].buffer_size = 7
            self.assertEqual(rl.rows[1].effective_spread_plus(), 5.0)
            self.assertEqual(rl.rows[1].effective_buffer_size(), 7)
        finally:
            os.unlink(path)


class TestNoSellList(unittest.TestCase):
    def test_load(self):
        path = write_tmp("# コメント\n6501\n// コメント\n7203\nxx\n")
        try:
            nl = NoSellList(path).load()
            self.assertEqual(nl.codes(), {"6501", "7203"})
            self.assertTrue(nl.contains("6501"))
            self.assertFalse(nl.contains("9433"))
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
