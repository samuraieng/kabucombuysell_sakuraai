"""kabuステーションAPI 注文照会 (GET /orders)。"""

from .client import KabuApiClient

# 注文状態 (State)
STATE_WAITING = "1"      # 待機(発注待機)
STATE_PROCESSING = "2"   # 処理中(発注送信中)
STATE_ORDERED = "3"      # 処理済(発注済・訂正済)
STATE_CANCELING = "4"    # 訂正取消送信中
STATE_FINISHED = "5"     # 終了(発注エラー・取消済・全約定・失効・期限切れ)

# 未約定とみなす状態
OPEN_STATES = (STATE_WAITING, STATE_PROCESSING, STATE_ORDERED)

# CashMargin
CASHMARGIN_CASH = 1      # 現物
CASHMARGIN_NEW = 2       # 信用新規
CASHMARGIN_SETTLE = 3    # 信用返済


class KabuApiOrders:
    """注文照会 API ラッパー。"""

    def __init__(self, client: KabuApiClient):
        self.client = client

    def fetch(self, product: str = "0", symbol: str | None = None,
              state: str | None = None, side: str | None = None,
              cashmargin: str | None = None, env: str = "prod") -> list[dict]:
        """GET /orders の結果 (辞書リスト) を返す。"""
        params: dict = {"product": product, "details": "true"}
        if symbol:
            params["symbol"] = symbol
        if state:
            params["state"] = state
        if side:
            params["side"] = side
        if cashmargin:
            params["cashmargin"] = cashmargin
        data = self.client.get("/orders", env=env, params=params)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        return []

    def open_orders(self, symbol: str, product: str = "0",
                    cashmargin: int | None = None,
                    env: str = "prod") -> list[dict]:
        """指定銘柄の未約定注文 (State 1/2/3 かつ残数量あり) を返す。"""
        result = []
        for o in self.fetch(
            product=product,
            symbol=symbol,
            cashmargin=str(cashmargin) if cashmargin is not None else None,
            env=env,
        ):
            if str(o.get("State", "")) not in OPEN_STATES:
                continue
            leaves = self._leaves_qty(o)
            if leaves <= 0:
                continue
            result.append(o)
        return result

    def _leaves_qty(self, order: dict) -> int:
        """注文の未約定数量を返す。

        /orders のレスポンスに LeavesQty が含まれない場合、
        OrderQty - CumQty から未約定数量を計算する。
        """
        leaves = order.get("LeavesQty")
        if leaves is not None and leaves != "":
            try:
                return int(float(leaves))
            except (TypeError, ValueError):
                pass
        try:
            order_qty = float(order.get("OrderQty") or 0)
            cum_qty = float(order.get("CumQty") or 0)
        except (TypeError, ValueError):
            return 0
        return int(order_qty - cum_qty)

    def has_open_order(self, symbol: str, side: str | None = None,
                       cashmargin: int | None = None,
                       product: str = "0", env: str = "prod") -> bool:
        """指定条件の未約定注文が存在するかどうか。"""
        for o in self.open_orders(symbol, product=product, cashmargin=cashmargin, env=env):
            if side is not None and str(o.get("Side", "")) != str(side):
                continue
            if cashmargin is not None and str(o.get("CashMargin", "")) != str(cashmargin):
                continue
            return True
        return False

    def open_order_ids(self, symbol: str, side: str | None = None,
                       cashmargin: int | None = None,
                       product: str = "0", env: str = "prod") -> list[str]:
        """未約定注文の OrderId 一覧を返す。"""
        ids = []
        for o in self.open_orders(symbol, product=product, cashmargin=cashmargin, env=env):
            if side is not None and str(o.get("Side", "")) != str(side):
                continue
            if cashmargin is not None and str(o.get("CashMargin", "")) != str(cashmargin):
                continue
            oid = o.get("OrderId")
            if oid:
                ids.append(str(oid))
        return ids

    def open_order_ids_by_hold_id(self, symbol: str, hold_id: str,
                                  cashmargin: int | None = None,
                                  product: str = "0", env: str = "prod") -> list[str]:
        """指定建玉IDに紐づく未約定注文の OrderId 一覧を返す。

        返済注文の Details には建玉紐付け用に HoldID または ExecutionID が
        含まれる。いずれかが hold_id と一致すれば対象注文とみなす。
        """
        result = []
        for o in self.open_orders(symbol, product=product, cashmargin=cashmargin, env=env):
            details = o.get("Details") or []
            for d in details:
                matched_id = str(d.get("HoldID", "") or d.get("ExecutionID", ""))
                if matched_id == str(hold_id):
                    oid = o.get("OrderId")
                    if oid:
                        result.append(str(oid))
                    break
        return result
