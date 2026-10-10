"""kabuステーションAPI 銘柄情報 (GET /symbol/{symbol}) と呼値計算。"""

import math

from .client import KabuApiClient

# 呼値グループ (GET /symbol の PriceRangeGroup)
GROUP_NORMAL = "10000"      # 株式(通常の呼値単位の銘柄)
GROUP_TOPIX500 = "10003"    # 株式(TOPIX500構成銘柄 ※売買単位が10口以上のETF等含む)
GROUP_ETF_1UNIT = "10004"   # 株式(売買単位が1口のETF等)

# 呼値テーブル: [(価格上限, 呼値単位)] 昇順に適用 (最初にマッチしたもの)
# 出典: kabuステーションAPI リファレンス PriceRangeGroup 説明 / JPX呼値表
_TICK_TABLES: dict[str, list[tuple[float, float]]] = {
    GROUP_NORMAL: [
        (3000, 1), (5000, 5), (30000, 10), (50000, 50),
        (300000, 100), (500000, 500), (3000000, 1000),
        (30000000, 10000), (50000000, 50000), (math.inf, 100000),
    ],
    GROUP_TOPIX500: [
        (1000, 0.1), (3000, 0.5), (10000, 1), (30000, 5), (100000, 10),
        (300000, 50), (1000000, 100), (3000000, 500),
        (10000000, 1000), (30000000, 5000), (math.inf, 10000),
    ],
    GROUP_ETF_1UNIT: [
        (10000, 1), (30000, 5), (100000, 10), (300000, 50),
        (1000000, 100), (3000000, 500), (10000000, 1000),
        (30000000, 5000), (math.inf, 10000),
    ],
}


def tick_size(price: float, price_range_group: str = GROUP_NORMAL) -> float:
    """価格水準と呼値グループから 呼値単位 (最小価格刻み) を返す。

    Args:
        price: 対象の価格 (円)
        price_range_group: GET /symbol の PriceRangeGroup ("10000"/"10003"/"10004")

    Returns:
        呼値単位 (円)。未知のグループは GROUP_NORMAL のテーブルを適用する。
    """
    table = _TICK_TABLES.get(str(price_range_group), _TICK_TABLES[GROUP_NORMAL])
    for upper, tick in table:
        if price <= upper:
            return tick
    return table[-1][1]  # pragma: no cover


def round_to_tick(price: float, price_range_group: str = GROUP_NORMAL) -> float:
    """価格を呼値単位に丸める (指値は呼値単位である必要があるため)。"""
    t = tick_size(price, price_range_group)
    return round(round(price / t) * t, 2)


class KabuApiSymbol:
    """銘柄情報取得 API ラッパー。"""

    def __init__(self, client: KabuApiClient):
        self.client = client
        self._cache: dict[str, dict] = {}

    def get(self, symbol: str, exchange: int = 1, addinfo: bool = True,
            env: str = "prod", use_cache: bool = True) -> dict:
        """GET /symbol/{symbol}@{exchange} の結果を返す (キャッシュあり)。

        addinfo=True で追加情報 (TotalStocks 等) を含める。
        """
        key = f"{symbol}@{exchange}"
        if use_cache and key in self._cache:
            return self._cache[key]
        path = f"/symbol/{symbol}@{exchange}"
        data = self.client.get(path, env=env, params={"addinfo": "true" if addinfo else "false"})
        self._cache[key] = data if isinstance(data, dict) else {}
        return self._cache[key]

    def trading_unit(self, symbol: str, env: str = "prod") -> int:
        """売買単位 (単元株数) を返す。取得できない場合は 100。"""
        info = self.get(symbol, env=env)
        unit = info.get("TradingUnit")
        try:
            unit = int(unit)
        except (TypeError, ValueError):
            return 100
        return unit if unit > 0 else 100

    def price_range_group(self, symbol: str, env: str = "prod") -> str:
        """呼値グループコードを返す。取得できない場合は "10000"。"""
        info = self.get(symbol, env=env)
        group = info.get("PriceRangeGroup")
        return str(group) if group else GROUP_NORMAL

    def unit_and_group(self, symbol: str, env: str = "prod") -> tuple[int, str]:
        """(売買単位, 呼値グループ) をまとめて返す。"""
        info = self.get(symbol, env=env)
        try:
            unit = int(info.get("TradingUnit"))
        except (TypeError, ValueError):
            unit = 100
        if unit <= 0:
            unit = 100
        group = str(info.get("PriceRangeGroup") or GROUP_NORMAL)
        return unit, group

    # ------------------------------------------------------------------
    # 板情報 (現在値)
    # ------------------------------------------------------------------
    def get_current_price(self, symbol: str, exchange: int = 1,
                          env: str = "prod") -> float:
        """GET /board/{symbol}@{exchange} から現在値 (CurrentPrice) を返す。

        注意: /board を参照した銘柄はAPI登録銘柄リスト(最大50銘柄)に
        自動登録される。
        """
        path = f"/board/{symbol}@{exchange}"
        data = self.client.get(path, env=env)
        price = data.get("CurrentPrice") if isinstance(data, dict) else None
        try:
            return float(price)
        except (TypeError, ValueError):
            return 0.0
