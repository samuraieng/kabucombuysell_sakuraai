"""kabuステーションAPI 接続設定。"""

import os


class KabuApiConfig:
    """kabuステーションAPI 接続設定クラス。

    環境変数:
        KABU_API_HOST     : kabuステーションが動作するホスト (default: localhost)
        KABU_API_PORT     : 本番ポート番号 (default: 18080)。検証環境は +1 のポート
        KABU_API_PW_PROD  : 本番環境 (kabuステーションログインパスワード)
        KABU_API_PW_DEV   : 検証環境 (kabuステーションAPI検証用パスワード)
        KABU_API_LOG_DIR  : API通信ログ出力先 (default: /tmp/kabu_api_logs)
    """

    BASE_PATH = "/kabusapi"
    DEFAULT_LOG_DIR = "/tmp/kabu_api_logs"

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        pw_prod: str | None = None,
        pw_dev: str | None = None,
        log_dir: str | None = None,
    ):
        self.server_address = (
            host if host is not None else os.environ.get("KABU_API_HOST", "localhost")
        )
        base_port = (
            port if port is not None else int(os.environ.get("KABU_API_PORT", "18080"))
        )
        self.port_prod = base_port
        self.port_dev = base_port + 1

        self.password_prod = (
            pw_prod if pw_prod is not None else os.environ.get("KABU_API_PW_PROD", "")
        )
        self.password_dev = (
            pw_dev if pw_dev is not None else os.environ.get("KABU_API_PW_DEV", "")
        )
        self.log_dir = (
            log_dir
            if log_dir is not None
            else os.environ.get("KABU_API_LOG_DIR", self.DEFAULT_LOG_DIR)
        )

    @property
    def base_url_prod(self) -> str:
        return f"http://{self.server_address}:{self.port_prod}{self.BASE_PATH}"

    @property
    def base_url_dev(self) -> str:
        return f"http://{self.server_address}:{self.port_dev}{self.BASE_PATH}"

    def base_url(self, env: str = "prod") -> str:
        if env == "prod":
            return self.base_url_prod
        if env == "dev":
            return self.base_url_dev
        raise ValueError(f"Invalid environment: {env}. Use 'prod' or 'dev'.")

    def password(self, env: str = "prod") -> str:
        if env == "prod":
            return self.password_prod
        if env == "dev":
            return self.password_dev
        raise ValueError(f"Invalid environment: {env}. Use 'prod' or 'dev'.")
