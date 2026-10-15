"""kabuステーションAPI 共通HTTPクライアント(トークン管理込み)。"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests

from .config import KabuApiConfig
from .exceptions import KabuApiAuthError, KabuApiException


class KabuApiClient:
    """kabuステーションAPI の共通クライアント。

    機能:
        - POST /token によるトークン発行・キャッシュ
        - X-API-KEY ヘッダ付与による REST 呼び出し
        - 401 (トークン無効) 検出時: トークン再取得のうえ1回リトライ
        - 429 (流量制限) 検出時: 指定間隔待機して1回リトライ
        - 発注系API (sendorder/cancelorder) の簡易流量制限
          (公式仕様: 秒間10件)

    環境名 ("prod" / "dev") を指定して使う。データ取得は常に prod を、
    注文は mode に応じた環境を利用する想定。
    """

    REQUEST_TIMEOUT = 10.0        # 秒
    RETRY_WAIT_429 = 0.3          # 秒 (429 リトライ前の待機)
    ORDER_MIN_INTERVAL = 0.12     # 秒 (発注系APIの最短間隔 ≒ 秒間8件)

    # 通信ログを残す対象パス（/tmp 以下に JSON で保存）
    LOG_TARGET_PATHS = {"/positions", "/orders", "/sendorder", "/cancelorder"}

    def __init__(self, config: KabuApiConfig | None = None):
        self.config = config or KabuApiConfig()
        self._tokens: dict[str, str] = {}
        self.session = requests.Session()
        self._last_order_time: float = 0.0

    # ------------------------------------------------------------------
    # トークン
    # ------------------------------------------------------------------
    def get_token(self, env: str = "prod") -> str:
        """指定環境のAPIトークンを発行して返す (キャッシュあり)。

        別のトークンが発行されると旧トークンは無効化されるため、
        401 時は refresh_token() で再取得する。
        """
        token = self._tokens.get(env)
        if token:
            return token
        return self.refresh_token(env)

    def refresh_token(self, env: str = "prod") -> str:
        """トークンを再発行して返す (キャッシュを更新)。"""
        url = f"{self.config.base_url(env)}/token"
        password = self.config.password(env)
        if not password:
            raise KabuApiAuthError(
                f"パスワードが未設定です (env={env})。"
                f"環境変数 KABU_API_PW_{env.upper()} を設定してください。"
            )
        try:
            resp = requests.post(
                url, json={"APIPassword": password}, timeout=self.REQUEST_TIMEOUT
            )
        except requests.RequestException as e:
            raise KabuApiException(f"トークン発行APIに接続できません: {url}: {e}") from e

        if resp.status_code != 200:
            raise KabuApiAuthError(
                "トークン発行に失敗しました",
                status_code=resp.status_code,
                response_body=resp.text,
            )
        try:
            token = resp.json().get("Token", "")
        except ValueError as e:
            raise KabuApiException(
                f"トークン発行の応答がJSON不正です: {resp.text[:200]}",
                status_code=resp.status_code,
            ) from e
        if not token:
            raise KabuApiAuthError(
                "トークン発行の応答に Token がありません",
                status_code=resp.status_code,
                response_body=resp.text,
            )
        self._tokens[env] = token
        return token

    # ------------------------------------------------------------------
    # 共通リクエスト
    # ------------------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        env: str = "prod",
        params: dict | None = None,
        json_body: dict | None = None,
    ):
        """REST API を呼び出し、JSONデコード結果 (dict / list) を返す。

        401 → トークン再発行して1回だけリトライ。
        429 → RETRY_WAIT_429 待機して1回だけリトライ。
        その他の非200 → KabuApiException。
        """
        url = f"{self.config.base_url(env)}{path}"
        order_api = path in ("/sendorder", "/cancelorder")
        should_log = path in self.LOG_TARGET_PATHS
        log_entry = None

        for attempt in (1, 2):
            token = self.get_token(env)
            headers = {"X-API-KEY": token}
            if should_log and attempt == 1:
                log_entry = {
                    "timestamp": datetime.now().isoformat(),
                    "env": env,
                    "method": method,
                    "path": path,
                    "url": url,
                    "request": {
                        "params": self._mask_sensitive(params),
                        "body": self._mask_sensitive(json_body),
                        "headers": {"X-API-KEY": "***"},
                    },
                }
            try:
                resp = self.session.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    timeout=self.REQUEST_TIMEOUT,
                )
            except requests.RequestException as e:
                if log_entry is not None:
                    log_entry["error"] = {
                        "type": type(e).__name__,
                        "message": str(e),
                    }
                    self._write_api_log(log_entry)
                raise KabuApiException(f"{method} {url} に失敗: {e}") from e

            if log_entry is not None:
                log_entry["response"] = {
                    "status_code": resp.status_code,
                    "body": self._mask_sensitive(self._parse_json_safe(resp.text)),
                }

            if resp.status_code == 401 and attempt == 1:
                # トークン失効 → 再発行して1回だけリトライ
                self.refresh_token(env)
                if log_entry is not None:
                    log_entry["retry_reason"] = "401 token refresh"
                continue

            if resp.status_code == 429 and attempt == 1:
                time.sleep(self.RETRY_WAIT_429)
                if log_entry is not None:
                    log_entry["retry_reason"] = "429 rate limit"
                continue

            if resp.status_code != 200:
                if log_entry is not None:
                    self._write_api_log(log_entry)
                raise KabuApiException(
                    f"{method} {path} が失敗しました",
                    status_code=resp.status_code,
                    response_body=resp.text,
                )

            if log_entry is not None:
                self._write_api_log(log_entry)

            if not resp.text:
                return {}

            try:
                return resp.json()
            except ValueError as e:
                raise KabuApiException(
                    f"{method} {path} の応答がJSON不正: {resp.text[:200]}",
                    status_code=resp.status_code,
                ) from e
        raise KabuApiException(f"{method} {path} がリトライ後も失敗しました")  # pragma: no cover

    def get(self, path: str, env: str = "prod", params: dict | None = None):
        return self.request("GET", path, env=env, params=params)

    def post(self, path: str, json_body: dict, env: str = "prod"):
        self._throttle_order(path)
        return self.request("POST", path, env=env, json_body=json_body)

    def put(self, path: str, json_body: dict, env: str = "prod"):
        self._throttle_order(path)
        return self.request("PUT", path, env=env, json_body=json_body)

    def delete(self, path: str, json_body: dict, env: str = "prod"):
        self._throttle_order(path)
        return self.request("DELETE", path, env=env, json_body=json_body)

    # ------------------------------------------------------------------
    # 流量制限
    # ------------------------------------------------------------------
    def _throttle_order(self, path: str) -> None:
        """発注系API (sendorder / cancelorder) の簡易流量制限。"""
        if path not in ("/sendorder", "/cancelorder"):
            return
        now = time.monotonic()
        elapsed = now - self._last_order_time
        if elapsed < self.ORDER_MIN_INTERVAL:
            time.sleep(self.ORDER_MIN_INTERVAL - elapsed)
        self._last_order_time = time.monotonic()

    # ------------------------------------------------------------------
    # 通信ログ
    # ------------------------------------------------------------------
    def _write_api_log(self, log_entry: dict) -> None:
        """API通信ログを /tmp/kabu_api_logs 以下に JSON で書き出す。"""
        log_dir = Path(self.config.log_dir)
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        now = datetime.now()
        path_part = str(log_entry.get("path", "")).replace("/", "__").strip("__")
        filename = (
            f"{now.strftime('%Y%m%d_%H%M%S')}_{now.microsecond:06d}_"
            f"{log_entry.get('method', 'UNKNOWN')}_{path_part}.json"
        )
        filepath = log_dir / filename
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(log_entry, f, ensure_ascii=False, indent=2, default=str)
        except OSError:
            pass

    def _mask_sensitive(self, data):
        """パスワード・トークンなどの機密情報をマスクする。"""
        if data is None:
            return None
        if isinstance(data, dict):
            masked = {}
            for key, value in data.items():
                upper = str(key).upper()
                if upper in {"APIPASSWORD", "PASSWORD", "TOKEN", "X-API-KEY"}:
                    masked[key] = "***"
                elif isinstance(value, (dict, list)):
                    masked[key] = self._mask_sensitive(value)
                else:
                    masked[key] = value
            return masked
        if isinstance(data, list):
            return [self._mask_sensitive(item) for item in data]
        return data

    def _parse_json_safe(self, text: str):
        """JSON文字列をパースし、失敗時は生文字列を返す。"""
        if not text:
            return ""
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return text
