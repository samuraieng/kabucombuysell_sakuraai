"""保有銘柄一覧を表示するワンショットスクリプト。

本番環境 (KABU_API_HOST / KABU_API_PORT / KABU_API_PW_PROD) に接続し、
現物・信用の保有銘柄をコード/銘柄名/区分/数量/取得価格/時価/損益で表示する。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from kabu_api import KabuApiClient, KabuApiConfig, KabuApiException, KabuApiSymbol
from kabu_api.positions import PRODUCT_CASH, PRODUCT_MARGIN, KabuApiPositions


@dataclass
class StockRow:
    code: str
    name: str
    account_type: str
    qty: int
    avg_price: float
    current_price: float
    profit_loss: float


class StockRetriever:
    def __init__(
        self,
        positions_api: KabuApiPositions,
        symbol_api: KabuApiSymbol | None = None,
    ):
        self.positions_api = positions_api
        self.symbol_api = symbol_api
        self._name_cache: dict[str, str] = {}

    def retrieve(self, env: str = "prod") -> list[StockRow]:
        rows = self._cash_rows(env) + self._margin_rows(env)
        order = {"現物": 0, "信用買建": 1, "信用売建": 2, "信用": 3}
        rows.sort(key=lambda r: (r.code, order.get(r.account_type, 9)))
        return rows

    def _cash_rows(self, env: str) -> list[StockRow]:
        raw = self.positions_api.fetch(product=PRODUCT_CASH, env=env)
        agg: dict[str, dict] = {}
        for p in raw:
            code = str(p.get("Symbol") or "")
            qty = int(p.get("LeavesQty") or 0)
            if not code or qty <= 0:
                continue
            price = float(p.get("Price") or 0.0)
            current = float(p.get("CurrentPrice") or 0.0)
            pl = float(p.get("ProfitLoss") or 0.0)
            name = p.get("SymbolName") or ""
            if code in agg:
                a = agg[code]
                total = a["qty"] + qty
                a["avg_price"] = (a["avg_price"] * a["qty"] + price * qty) / total if total else 0.0
                a["qty"] = total
                a["profit_loss"] += pl
                a["current_price"] = current or a["current_price"]
                a["name"] = a["name"] or name
            else:
                agg[code] = {
                    "qty": qty,
                    "avg_price": price,
                    "current_price": current,
                    "profit_loss": pl,
                    "name": name,
                }
        return [
            StockRow(
                code=code,
                name=self._resolve_name(code, a["name"], env),
                account_type="現物",
                qty=a["qty"],
                avg_price=a["avg_price"],
                current_price=a["current_price"],
                profit_loss=a["profit_loss"],
            )
            for code, a in agg.items()
        ]

    def _margin_rows(self, env: str) -> list[StockRow]:
        raw = self.positions_api.fetch(product=PRODUCT_MARGIN, env=env)
        rows: list[StockRow] = []
        for p in raw:
            code = str(p.get("Symbol") or "")
            qty = int(p.get("LeavesQty") or 0)
            if not code or qty <= 0:
                continue
            side = str(p.get("Side") or "")
            account_type = {"2": "信用買建", "1": "信用売建"}.get(side, "信用")
            rows.append(
                StockRow(
                    code=code,
                    name=self._resolve_name(code, p.get("SymbolName") or "", env),
                    account_type=account_type,
                    qty=qty,
                    avg_price=float(p.get("Price") or 0.0),
                    current_price=float(p.get("CurrentPrice") or 0.0),
                    profit_loss=float(p.get("ProfitLoss") or 0.0),
                )
            )
        return rows

    def _resolve_name(self, code: str, name_from_position: str, env: str) -> str:
        if name_from_position:
            self._name_cache[code] = name_from_position
            return name_from_position
        if code in self._name_cache:
            return self._name_cache[code]
        name = ""
        if self.symbol_api is not None:
            try:
                info = self.symbol_api.get(code, env=env)
                name = info.get("SymbolName", "") if isinstance(info, dict) else ""
            except KabuApiException:
                name = ""
        self._name_cache[code] = name
        return name


def format_table(rows: list[StockRow]) -> str:
    if not rows:
        return "保有銘柄はありません。"
    lines = [
        f"{'コード':<8} {'銘柄名':<24} {'区分':<10} {'数量':>8} {'取得価格':>10} {'時価':>10} {'損益':>12}",
        "-" * 88,
    ]
    for r in rows:
        lines.append(
            f"{r.code:<8} {r.name or '(不明)':<24} {r.account_type:<10} "
            f"{r.qty:>8,} {r.avg_price:>10,.1f} {r.current_price:>10,.1f} {r.profit_loss:>12,.1f}"
        )
    return "\n".join(lines)


def main() -> int:
    try:
        config = KabuApiConfig()
        client = KabuApiClient(config)
        positions_api = KabuApiPositions(client)
        symbol_api = KabuApiSymbol(client)
        rows = StockRetriever(positions_api, symbol_api).retrieve(env="prod")
    except KabuApiException as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # pragma: no cover
        print(f"予期しないエラー: {e}", file=sys.stderr)
        return 1

    print(format_table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
