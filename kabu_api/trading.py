"""kabuステーションAPI 注文発行・取消 (POST /sendorder, PUT /cancelorder)。

注文パラメータの既定値 (要求仕様に基づく):
    - 現物買 : CashMargin=1, Side="2", DelivType=2(お預り金), FundType="AA"(信用代用),
               AccountType=4(特定口座), Exchange=9(SOR)
    - 現物売 : CashMargin=1, Side="1", DelivType=0, FundType="  "(半角スペース2つ),
               AccountType=4(特定口座), Exchange=9(SOR)
    - 信用返済: CashMargin=3, Side=返済方向, DelivType=2(お預り金), FundType="11"(信用取引),
               ClosePositions=[{HoldID, Qty}] で建玉指定
    - 執行条件: FrontOrderType 20=指値 / 10=成行 (Price は成行時 0)
    - 信用返済の Exchange: 建玉の市場 (/positions の Exchange) を使用する。
      建玉を ClosePositions で指定する場合、建玉市場と一致させる必要がある。
"""

import json
import time

from .client import KabuApiClient
from .exceptions import KabuApiException
from .logger import get_logger

logger = get_logger(__name__)

SIDE_SELL = "1"
SIDE_BUY = "2"

CASHMARGIN_CASH = 1      # 現物
CASHMARGIN_SETTLE = 3    # 信用返済

DELIV_NONE = 0           # 指定なし
DELIV_DEPOSIT = 2        # お預り金

FUND_CASH_SELL = "  "    # 現物売 (半角スペース2つ)
FUND_SUBSTITUTE = "AA"   # 信用代用 (現物買)
FUND_MARGIN = "11"       # 信用取引

ACCOUNT_SPECIFIC = 4     # 特定口座

FRONT_MARKET = 10        # 成行
FRONT_LIMIT = 20         # 指値

#EXCHANGE_TSE = 1         # TSE
EXCHANGE_SOR = 9         # SOR (新規発注用, 信用返済)


class OrderResult:
    """注文発行の結果。"""

    def __init__(self, ok: bool, order_id: str = "", status_code: int | None = None,
                 body: dict | None = None, message: str = "",
                 response_body: str | None = None, request_body: dict | None = None):
        self.ok = ok
        self.order_id = order_id
        self.status_code = status_code
        self.body = body or {}
        self.message = message
        self.response_body = response_body
        self.request_body = request_body

    @property
    def is_one_shot_amount_error(self) -> bool:
        """kabuステーションのワンショット金額制限エラー(4003001)かどうか。"""
        if self.status_code != 400:
            return False
        # body は成功時のみ設定される。エラー時は response_body に生JSONが入る。
        for source in (self.body, self._parsed_response_body()):
            if not source:
                continue
            code = source.get("Code")
            message = source.get("Message", "")
            if code is None:
                continue
            if str(code) == "4003001" or "ワンショット" in str(message):
                return True
        return False

    @property
    def is_settle_spec_error(self) -> bool:
        """kabuステーションの決済指定内容エラー(Code=8)かどうか。"""
        # body は成功時のみ設定される。エラー時は response_body に生JSONが入る。
        for source in (self.body, self._parsed_response_body()):
            if not source:
                continue
            code = source.get("Code")
            message = source.get("Message", "")
            if code is None:
                continue
            if str(code) == "8" or "決済指定内容に誤りがあります" in str(message):
                return True
        return False

    def _parsed_response_body(self) -> dict | None:
        if not self.response_body:
            return None
        try:
            data = json.loads(self.response_body)
            return data if isinstance(data, dict) else None
        except (ValueError, TypeError):
            return None

    @property
    def one_shot_message(self) -> str:
        """ワンショット金額エラー発生時のユーザー向け説明メッセージ。"""
        qty = 0
        price = 0.0
        if self.request_body:
            qty = int(self.request_body.get("Qty") or 0)
            price = float(self.request_body.get("Price") or 0.0)
        amount = int(price * qty)
        return (
            f"{self.message} "
            f"(kabuステーションの「ソフトリミット(ワンショット上限)」を超過した可能性があります。"
            f"注文金額=約{amount:,}円(株価{price:.1f}×{qty}株)。"
            f"kabuステーションの「システム設定」→「ソフトリミット(ワンショット上限)」"
            f"で信用の上限を引き上げるか、注文数量を減らしてください)"
        )

    def __repr__(self):  # pragma: no cover
        return (f"OrderResult(ok={self.ok}, order_id={self.order_id!r}, "
                f"message={self.message!r})")


class KabuApiTrade:
    """注文発行・取消ラッパー。"""

    def __init__(self, client: KabuApiClient):
        self.client = client

    # ------------------------------------------------------------------
    # 注文発行
    # ------------------------------------------------------------------
    def send_order(self, body: dict, env: str = "prod") -> OrderResult:
        """POST /sendorder を発行し OrderResult を返す。"""
        try:
            data = self.client.post("/sendorder", body, env=env)
        except KabuApiException as e:
            return OrderResult(
                ok=False,
                message=str(e),
                status_code=e.status_code,
                response_body=e.response_body,
                request_body=body,
            )
        except Exception as e:  # その他の予期しない例外
            return OrderResult(ok=False, message=str(e), request_body=body)
        order_id = str(data.get("OrderId", "")) if isinstance(data, dict) else ""
        return OrderResult(ok=bool(order_id), order_id=order_id, body=data, request_body=body)

    def send_cash_buy(self, symbol: str, qty: int, price: float = 0.0,
                      env: str = "prod", expire_day: int = 0) -> OrderResult:
        """現物買い (特定口座 / 信用代用預り)。price=0 なら成行。"""
        if qty <= 0:
            return OrderResult(ok=False, message="数量が0以下です")
        limit = price > 0
        body = {
            "Symbol": symbol,
            "Exchange": EXCHANGE_SOR,
            "SecurityType": 1,
            "Side": SIDE_BUY,
            "CashMargin": CASHMARGIN_CASH,
            "DelivType": DELIV_DEPOSIT,
            "FundType": FUND_SUBSTITUTE,
            "AccountType": ACCOUNT_SPECIFIC,
            "Qty": qty,
            "FrontOrderType": FRONT_LIMIT if limit else FRONT_MARKET,
            "Price": price if limit else 0,
            "ExpireDay": expire_day,
        }
        return self.send_order(body, env=env)

    def send_cash_sell(self, symbol: str, qty: int, price: float = 0.0,
                       env: str = "prod", expire_day: int = 0) -> OrderResult:
        """現物売り (特定口座)。price=0 なら成行。"""
        if qty <= 0:
            return OrderResult(ok=False, message="数量が0以下です")
        limit = price > 0
        body = {
            "Symbol": symbol,
            "Exchange": EXCHANGE_SOR,
            "SecurityType": 1,
            "Side": SIDE_SELL,
            "CashMargin": CASHMARGIN_CASH,
            "DelivType": DELIV_NONE,
            "FundType": FUND_CASH_SELL,
            "AccountType": ACCOUNT_SPECIFIC,
            "Qty": qty,
            "FrontOrderType": FRONT_LIMIT if limit else FRONT_MARKET,
            "Price": price if limit else 0,
            "ExpireDay": expire_day,
        }
        return self.send_order(body, env=env)

    # 建玉指定が一時的に参照できない場合の再試行間隔 (秒)
    CLOSEPOSITION_RETRY_WAIT = 0.4

    def send_credit_settle(
        self,
        symbol: str,
        close_side: str,
        qty: int,
        price: float = 0.0,
        close_positions: list[dict] | None = None,
        margin_trade_type: int = 3,
        exchange: int = EXCHANGE_SOR,
        env: str = "prod",
        expire_day: int = 0,
    ) -> OrderResult:
        """信用返済注文を発行する。

        Args:
            symbol: 銘柄コード
            close_side: 返済方向 ("1"=返済売(買建玉の解消) / "2"=返済買(売建玉の解消))
            qty: 返済数量
            price: 指値価格 (0 なら成行)
            close_positions: [{"HoldID": "E...", "Qty": n}] (建玉指定)
            margin_trade_type: 1=制度 / 2=一般(長期) / 3=一般(デイトレ)
            exchange: 建玉市場 (1=東証 / 27=東証+ / 9=SOR 等)
        """
        if qty <= 0:
            return OrderResult(ok=False, message="数量が0以下です")
        if close_side not in (SIDE_SELL, SIDE_BUY):
            return OrderResult(ok=False, message=f"不正な返済方向: {close_side}")

        # 建玉指定がある場合は中身を検証する
        validated_positions: list[dict] = []
        if close_positions:
            for p in close_positions:
                hold_id = str(p.get("HoldID") or "")
                pos_qty = int(p.get("Qty") or 0)
                if not hold_id or pos_qty <= 0:
                    return OrderResult(
                        ok=False,
                        message=(
                            "建玉指定が不正です: "
                            f"HoldID={hold_id!r}, Qty={pos_qty}"
                        ),
                    )
                validated_positions.append({"HoldID": hold_id, "Qty": pos_qty})

        limit = price > 0
        body: dict = {
            "Symbol": symbol,
            "Exchange": exchange,
            "SecurityType": 1,
            "Side": close_side,
            "CashMargin": CASHMARGIN_SETTLE,
            "MarginTradeType": margin_trade_type,
            "DelivType": DELIV_DEPOSIT,
            "FundType": FUND_MARGIN,
            "AccountType": ACCOUNT_SPECIFIC,
            "Qty": qty,
            "FrontOrderType": FRONT_LIMIT if limit else FRONT_MARKET,
            "Price": price if limit else 0,
            "ExpireDay": expire_day,
        }
        if validated_positions:
            # ClosePositionOrder と ClosePositions は排他。建玉指定を優先する。
            body["ClosePositions"] = validated_positions
        else:
            # 建玉指定がない場合は決済順序で返済
            body["ClosePositionOrder"] = 2  # 日付(新しい順)、損益(高い順)

        result = self.send_order(body, env=env)

        # ClosePositions で 500 が返ってきた場合、建玉反映の遅延を想定し
        # 同じ ClosePositions で 1 回だけ再試行する。
        # ClosePositionOrder への切り替えは行わない（公式回答 #1239 などで、
        # 建玉未反映時に ClosePositionOrder でも 1009001 が返ることが報告されている）。
        if (
            not result.ok
            and result.status_code == 500
            and "ClosePositions" in body
        ):
            time.sleep(self.CLOSEPOSITION_RETRY_WAIT)
            retry = self.send_order(body, env=env)
            if retry.ok:
                retry.message = (
                    f"{retry.message} (ClosePositions retry after "
                    f"{self.CLOSEPOSITION_RETRY_WAIT}s)"
                )
                return retry
            # 再試行も失敗した場合は、元のエラーを返す（リクエストボディは元のまま）
            result.message = (
                f"{result.message} (retry also failed: {retry.message})"
            )
        return result

    # ------------------------------------------------------------------
    # 注文取消
    # ------------------------------------------------------------------
    def cancel_order(self, order_id: str, env: str = "prod") -> bool:
        """PUT /cancelorder で注文を取消する。

        公式リファレンスではリクエストボディのキーは ``OrderID`` である。
        """
        try:
            self.client.put("/cancelorder", {"OrderID": str(order_id)}, env=env)
            return True
        except Exception as e:
            logger.error(f"  [取消失敗] OrderId={order_id}: {e}")
            return False
