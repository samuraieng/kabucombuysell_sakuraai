"""kabu_api/client.py の単体テスト。"""

import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from kabu_api.client import KabuApiClient
from kabu_api.config import KabuApiConfig
from kabu_api.exceptions import KabuApiException


class TestApiLogging(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = KabuApiConfig(
            host="localhost",
            port=18080,
            pw_prod="dummy",
            pw_dev="dummy",
            log_dir=self.tmpdir,
        )

    def tearDown(self):
        for name in os.listdir(self.tmpdir):
            os.remove(os.path.join(self.tmpdir, name))
        os.rmdir(self.tmpdir)

    def _make_client(self):
        client = KabuApiClient(self.config)
        client._tokens["prod"] = "TESTTOKEN"
        return client

    def _make_response(self, status_code=200, json_data=None, text=None):
        resp = Mock()
        resp.status_code = status_code
        if text is not None:
            resp.text = text
        elif json_data is not None:
            resp.text = json.dumps(json_data)
            resp.json.return_value = json_data
        else:
            resp.text = ""
        return resp

    def test_logs_positions_request_and_response(self):
        """/positions の呼び出しでログファイルが生成されること。"""
        client = self._make_client()
        resp = self._make_response(json_data=[{"Symbol": "7203", "LeavesQty": 100}])

        with patch.object(client.session, "request", return_value=resp):
            result = client.get("/positions", env="prod", params={"product": 2})

        self.assertEqual(result, [{"Symbol": "7203", "LeavesQty": 100}])
        files = os.listdir(self.tmpdir)
        self.assertEqual(len(files), 1)

        with open(os.path.join(self.tmpdir, files[0]), "r", encoding="utf-8") as f:
            log = json.load(f)

        self.assertEqual(log["path"], "/positions")
        self.assertEqual(log["method"], "GET")
        self.assertEqual(log["request"]["params"], {"product": 2})
        self.assertEqual(log["response"]["status_code"], 200)
        self.assertEqual(log["response"]["body"][0]["Symbol"], "7203")

    def test_logs_sendorder_error(self):
        """/sendorder がエラーの場合もログファイルが生成されること。"""
        client = self._make_client()
        resp = self._make_response(
            status_code=500,
            text='{"Code":8,"Message":"決済指定内容に誤りがあります"}',
        )
        resp.json.side_effect = ValueError

        body = {"Symbol": "5801", "Qty": 100, "ClosePositions": [{"HoldID": "E1", "Qty": 100}]}
        with patch.object(client.session, "request", return_value=resp):
            with self.assertRaises(KabuApiException):
                client.post("/sendorder", json_body=body, env="prod")

        files = os.listdir(self.tmpdir)
        self.assertEqual(len(files), 1)

        with open(os.path.join(self.tmpdir, files[0]), "r", encoding="utf-8") as f:
            log = json.load(f)

        self.assertEqual(log["path"], "/sendorder")
        self.assertEqual(log["request"]["body"]["Symbol"], "5801")
        self.assertEqual(log["response"]["status_code"], 500)
        self.assertEqual(log["response"]["body"]["Code"], 8)

    def test_does_not_log_token_request(self):
        """/token はログ対象外であること。"""
        client = self._make_client()
        resp = self._make_response(json_data={"Token": "SECRET"})
        resp.status_code = 200

        with patch("kabu_api.client.requests.post", return_value=resp):
            token = client.refresh_token("prod")

        self.assertEqual(token, "SECRET")
        self.assertEqual(os.listdir(self.tmpdir), [])

    def test_masks_sensitive_values(self):
        """APIPassword などの機密情報がマスクされること。"""
        client = self._make_client()
        data = {
            "APIPassword": "secret1",
            "Token": "secret2",
            "nested": {"X-API-KEY": "secret3"},
            "normal": "keep",
        }
        masked = client._mask_sensitive(data)

        self.assertEqual(masked["APIPassword"], "***")
        self.assertEqual(masked["Token"], "***")
        self.assertEqual(masked["nested"]["X-API-KEY"], "***")
        self.assertEqual(masked["normal"], "keep")


if __name__ == "__main__":
    unittest.main()
