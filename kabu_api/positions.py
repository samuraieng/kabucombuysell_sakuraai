"""kabuステーションAPI 残高照会 (GET /positions)。"""

from dataclasses import dataclass, field

from .client import KabuApiClient

# /positions の product パラメータ
PRODUCT_ALL = 0
PRODUCT_CASH = 1      # 現物
PRODUCT_MARGIN = 2    # 信用
PRODUCT_FUTURE = 3
PRODUCT_OPTION = 4


@dataclass
class CashHolding:
    """現物保有 (銘柄単位に集約した所有リストの1要素)。"""

    symbol: str
    qty: int
    price: float          # 取得価額 (確定買値)
    current_price: float  # 現在値


@dataclass
class CreditPosition:
    """信用建玉 1件 (建玉ID単位)。"""

    hold_id: str          # 建玉ID (ExecutionID, 先頭 "E")
    symbol: str
    name: str = ""        # 銘柄名 (SymbolName)
    side: str = ""        # "1"=売建 / "2"=買建
    price: float = 0.0    # 確定値 (取得価額)
    qty: int = 0          # 残数量 (LeavesQty)
    current_price: float = 0.0  # 現在値
    profit_loss: float = 0.0    # 損益
    margin_trade_type: int = 0  # 1=制度 / 2=一般(長期) / 3=一般(デイトレ)
    hold_qty: int = 0     # 拘束数量
    exchange: int = 9     # 建玉市場 (1=東証 / 27=東証+ / 9=SOR 等)


@dataclass
class Holdings:
    """全保有銘柄一覧 (現物 + 信用)。"""

    cash: dict = field(default_factory=dict)      # symbol -> CashHolding
    credit: dict = field(default_factory=dict)    # symbol -> list[CreditPosition]

    def cash_qty(self, symbol: str) -> int:
        h = self.cash.get(symbol)
        return h.qty if h else 0

    def cash_price(self, symbol: str) -> float:
        h = self.cash.get(symbol)
        return h.price if h else 0.0

    def cash_current(self, symbol: str) -> float:
        h = self.cash.get(symbol)
        return h.current_price if h else 0.0

    def credit_positions(self, symbol: str) -> list:
        return self.credit.get(symbol, [])

    def credit_qty(self, symbol: str) -> int:
        return sum(p.qty for p in self.credit_positions(symbol))

    def symbols(self) -> list:
        return sorted(set(self.cash.keys()) | set(self.credit.keys()))


class KabuApiPositions:
    """残高照会 API ラッパー。"""

    def __init__(self, client: KabuApiClient):
        self.client = client

    def fetch(self, product: int = PRODUCT_ALL, symbol: str | None = None,
              env: str = "prod") -> list[dict]:
        """/positions を呼び出し、生の辞書リストを返す。

        addinfo=true (追加情報出力) で CurrentPrice を含める。
        """
        params: dict = {"product": product, "addinfo": "true"}
        if symbol:
            params["symbol"] = symbol
        data = self.client.get("/positions", env=env, params=params)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        return []

    def build_holdings(self, env: str = "prod") -> Holdings:
        """現物 + 信用の全ポジションを取得し Holdings に整理して返す。

        現物は銘柄単位に集約 (数量・加重平均取得価額)。
        信用は建玉ID単位で保持。
        """
        holdings = Holdings()

        # ---- 現物 ----
        agg: dict[str, dict] = {}
        for p in self.fetch(product=PRODUCT_CASH, env=env):
            symbol = str(p.get("Symbol", ""))
            if not symbol:
                continue
            qty = int(p.get("LeavesQty") or 0)
            if qty <= 0:
                continue
            price = float(p.get("Price") or 0.0)
            current = float(p.get("CurrentPrice") or 0.0)
            if symbol in agg:
                a = agg[symbol]
                total_qty = a["qty"] + qty
                a["price"] = (
                    (a["price"] * a["qty"] + price * qty) / total_qty if total_qty else 0.0
                )
                a["qty"] = total_qty
                a["current_price"] = current or a["current_price"]
            else:
                agg[symbol] = {
                    "qty": qty,
                    "price": price,
                    "current_price": current,
                }
        for symbol, a in agg.items():
            holdings.cash[symbol] = CashHolding(
                symbol=symbol,
                qty=a["qty"],
                price=a["price"],
                current_price=a["current_price"],
            )

        # ---- 信用 ----
        for p in self.fetch(product=PRODUCT_MARGIN, env=env):
            symbol = str(p.get("Symbol", ""))
            qty = int(p.get("LeavesQty") or 0)
            if not symbol or qty <= 0:
                continue
            pos = CreditPosition(
                hold_id=str(p.get("ExecutionID") or ""),
                symbol=symbol,
                name=str(p.get("SymbolName") or ""),
                side=str(p.get("Side") or ""),
                price=float(p.get("Price") or 0.0),
                qty=qty,
                current_price=float(p.get("CurrentPrice") or 0.0),
                profit_loss=float(p.get("ProfitLoss") or 0.0),
                margin_trade_type=int(p.get("MarginTradeType") or 0),
                hold_qty=int(p.get("HoldQty") or 0),
                exchange=int(p.get("Exchange") or 9),
            )
            holdings.credit.setdefault(symbol, []).append(pos)

        return holdings
