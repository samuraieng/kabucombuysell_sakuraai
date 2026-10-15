"""kabu_api/trading.py の単体テスト。"""

import unittest

from kabu_api.exceptions import KabuApiException
from kabu_api.trading import KabuApiTrade, OrderResult


class FakeClient:
    """sendorder/cancelorder の呼び出しを記録する偽クライアント。"""

    def __init__(self, responses=None):
        self.responses = responses or []
        self.post_calls = []
        self.put_calls = []

    def post(self, path, json_body, env="prod"):
        self.post_calls.append((path, json_body, env))
        if not self.responses:
            raise RuntimeError("FakeClient responses exhausted")
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp

    def put(self, path, json_body, env="prod"):
        self.put_calls.append((path, json_body, env))
        return {}


class TestSendOrder(unittest.TestCase):
    def test_success(self):
        client = FakeClient([{"OrderId": "ORDER0001"}])
        trade = KabuApiTrade(client)
        body = {"Symbol": "7203", "Qty": 100}
        result = trade.send_order(body)

        self.assertTrue(result.ok)
        self.assertEqual(result.order_id, "ORDER0001")
        self.assertEqual(result.request_body, body)

    def test_kabu_exception_includes_body_and_status(self):
        exc = KabuApiException(
            "POST /sendorder が失敗しました",
            status_code=500,
            response_body='{"Message":"Internal Server Error"}',
        )
        client = FakeClient([exc])
        trade = KabuApiTrade(client)
        body = {"Symbol": "7203", "Qty": 100}
        result = trade.send_order(body)

        self.assertFalse(result.ok)
        self.assertEqual(result.status_code, 500)
        self.assertEqual(result.response_body, '{"Message":"Internal Server Error"}')
        self.assertEqual(result.request_body, body)
        self.assertIn("500", result.message)
        self.assertIn("Internal Server Error", result.message)


class TestCancelOrder(unittest.TestCase):
    def test_cancel_order_uses_put_and_order_id_key(self):
        """cancel_order は PUT /cancelorder を OrderID キーで呼び出す。"""
        client = FakeClient()
        trade = KabuApiTrade(client)
        result = trade.cancel_order("20261013A01N72401417")

        self.assertTrue(result)
        self.assertEqual(len(client.put_calls), 1)
        path, body, env = client.put_calls[0]
        self.assertEqual(path, "/cancelorder")
        self.assertEqual(body, {"OrderID": "20261013A01N72401417"})
        self.assertEqual(env, "prod")

    def test_cancel_order_returns_false_on_exception(self):
        """cancel_order は例外時に False を返す。"""
        class RaisingClient:
            def put(self, path, json_body, env="prod"):
                raise RuntimeError("connection error")

        trade = KabuApiTrade(RaisingClient())
        result = trade.cancel_order("20261013A01N72401417")
        self.assertFalse(result)


class TestOrderResultOneShotError(unittest.TestCase):
    def test_detect_one_shot_from_response_body(self):
        """response_body に 4003001/ワンショット が含まれる場合を検出する。"""
        result = OrderResult(
            ok=False,
            status_code=400,
            response_body='{"Code":4003001,"Message":"ワンショット：金額エラー"}',
            message="POST /sendorder が失敗しました (status=400)",
            request_body={"Symbol": "285A", "Qty": 100, "Price": 17160.0},
        )
        self.assertTrue(result.is_one_shot_amount_error)
        self.assertIn("ワンショット上限", result.one_shot_message)
        self.assertIn("1,716,000", result.one_shot_message)

    def test_detect_one_shot_from_body(self):
        """body に 4003001 が含まれる場合も検出する。"""
        result = OrderResult(
            ok=False,
            status_code=400,
            body={"Code": 4003001, "Message": "ワンショット：金額エラー"},
            message="bad request",
        )
        self.assertTrue(result.is_one_shot_amount_error)

    def test_not_one_shot_for_other_400(self):
        """建玉未選択など、別の 400 エラーは検出しない。"""
        result = OrderResult(
            ok=False,
            status_code=400,
            response_body='{"Code":1009001,"Message":"建玉が選択されていません。"}',
            message="bad request",
        )
        self.assertFalse(result.is_one_shot_amount_error)

    def test_not_one_shot_for_500(self):
        """500 エラーは検出しない。"""
        result = OrderResult(
            ok=False,
            status_code=500,
            response_body='{"Code":500,"Message":"Internal Server Error"}',
            message="server error",
        )
        self.assertFalse(result.is_one_shot_amount_error)


class TestOrderResultSettleSpecError(unittest.TestCase):
    def test_detect_settle_spec_error_from_response_body(self):
        """response_body に Code=8/決済指定内容 に含まれる場合を検出する。"""
        result = OrderResult(
            ok=False,
            status_code=500,
            response_body='{"Code":8,"Message":"決済指定内容に誤りがあります"}',
            message="POST /sendorder が失敗しました (status=500)",
        )
        self.assertTrue(result.is_settle_spec_error)

    def test_detect_settle_spec_error_from_body(self):
        """body に Code=8 が含まれる場合も検出する。"""
        result = OrderResult(
            ok=False,
            status_code=500,
            body={"Code": 8, "Message": "決済指定内容に誤りがあります"},
            message="POST /sendorder が失敗しました (status=500)",
        )
        self.assertTrue(result.is_settle_spec_error)

    def test_not_settle_spec_error_for_other_errors(self):
        """Code=8 以外は検出しない。"""
        result = OrderResult(
            ok=False,
            status_code=500,
            response_body='{"Code":500,"Message":"Internal Server Error"}',
            message="server error",
        )
        self.assertFalse(result.is_settle_spec_error)


class TestCreditSettleRetry(unittest.TestCase):
    def test_closeposition_retry_on_500(self):
        """ClosePositions で 500 が返った場合、同じ ClosePositions で再試行する。"""
        client = FakeClient([
            KabuApiException(
                "POST /sendorder が失敗しました",
                status_code=500,
                response_body="Internal Server Error",
            ),
            {"OrderId": "ORDER0002"},
        ])
        trade = KabuApiTrade(client)
        result = trade.send_credit_settle(
            symbol="5802",
            close_side="2",
            qty=100,
            price=0.0,
            close_positions=[{"HoldID": "E123", "Qty": 100}],
            margin_trade_type=3,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.order_id, "ORDER0002")
        self.assertEqual(len(client.post_calls), 2)
        # 両方とも ClosePositions 付き、ClosePositionOrder は含まない
        for call in client.post_calls:
            self.assertIn("ClosePositions", call[1])
            self.assertNotIn("ClosePositionOrder", call[1])
        self.assertEqual(
            client.post_calls[0][1]["ClosePositions"],
            [{"HoldID": "E123", "Qty": 100}],
        )
        self.assertEqual(
            client.post_calls[1][1]["ClosePositions"],
            [{"HoldID": "E123", "Qty": 100}],
        )
        self.assertIn("retry", result.message)

    def test_credit_settle_uses_exchange_argument(self):
        """send_credit_settle は exchange 引数をリクエストボディに反映する。"""
        client = FakeClient([{"OrderId": "ORDER0003"}])
        trade = KabuApiTrade(client)
        result = trade.send_credit_settle(
            symbol="5802",
            close_side="2",
            qty=100,
            price=3000.0,
            close_positions=[{"HoldID": "E123", "Qty": 100}],
            margin_trade_type=3,
            exchange=1,
        )

        self.assertTrue(result.ok)
        self.assertEqual(client.post_calls[0][1]["Exchange"], 1)

    def test_closeposition_retry_fails(self):
        """ClosePositions で 500 の再試行も失敗した場合、元のエラーを返す。"""
        client = FakeClient([
            KabuApiException(
                "POST /sendorder が失敗しました",
                status_code=500,
                response_body="Internal Server Error 1",
            ),
            KabuApiException(
                "POST /sendorder が失敗しました",
                status_code=500,
                response_body="Internal Server Error 2",
            ),
        ])
        trade = KabuApiTrade(client)
        result = trade.send_credit_settle(
            symbol="5802",
            close_side="2",
            qty=100,
            close_positions=[{"HoldID": "E123", "Qty": 100}],
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.status_code, 500)
        self.assertEqual(len(client.post_calls), 2)
        for call in client.post_calls:
            self.assertIn("ClosePositions", call[1])
            self.assertNotIn("ClosePositionOrder", call[1])
        self.assertIn("retry also failed", result.message)

    def test_closeposition_no_retry_on_other_error(self):
        """500 以外のエラーでは再試行しない。"""
        client = FakeClient([
            KabuApiException(
                "POST /sendorder が失敗しました",
                status_code=400,
                response_body='{"Code":1009001,"Message":"建玉が選択されていません。"}',
            ),
        ])
        trade = KabuApiTrade(client)
        result = trade.send_credit_settle(
            symbol="5802",
            close_side="2",
            qty=100,
            close_positions=[{"HoldID": "E123", "Qty": 100}],
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.status_code, 400)
        self.assertEqual(len(client.post_calls), 1)
        self.assertIn("建玉が選択されていません", result.message)

    def test_invalid_close_positions(self):
        """HoldID が空や Qty が 0 以下の建玉指定は拒否する。"""
        client = FakeClient([])
        trade = KabuApiTrade(client)

        result = trade.send_credit_settle(
            symbol="5802",
            close_side="2",
            qty=100,
            close_positions=[{"HoldID": "", "Qty": 100}],
        )
        self.assertFalse(result.ok)
        self.assertIn("建玉指定が不正", result.message)
        self.assertEqual(len(client.post_calls), 0)

        result = trade.send_credit_settle(
            symbol="5802",
            close_side="2",
            qty=100,
            close_positions=[{"HoldID": "E123", "Qty": 0}],
        )
        self.assertFalse(result.ok)
        self.assertIn("建玉指定が不正", result.message)
        self.assertEqual(len(client.post_calls), 0)


if __name__ == "__main__":
    unittest.main()
