"""kabuステーションAPI 例外定義。"""


class KabuApiException(Exception):
    """kabuステーションAPI の呼び出しで発生する例外の基底クラス。"""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body

    def __str__(self) -> str:
        base = super().__str__()
        parts = [base]
        if self.status_code is not None:
            parts.append(f"(status={self.status_code})")
        if self.response_body:
            preview = self.response_body.strip()
            if len(preview) > 200:
                preview = preview[:200] + "..."
            parts.append(f"body={preview}")
        return " ".join(parts)


class KabuApiAuthError(KabuApiException):
    """認証(トークン発行)に失敗した場合の例外。"""
