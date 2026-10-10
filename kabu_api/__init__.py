"""kabu_api: kabuステーションAPI(kabusapi) クライアントパッケージ。

三菱UFJモルガン・スタンレー証券「kabuステーション」REST API
(https://kabucom.github.io/kabusapi/) を利用した自動取引を支援する、
再利用可能なクライアント群を提供する。

構成:
    KabuApiConfig      : 接続設定 (ホスト / ポート / APIパスワード)
    KabuApiClient      : トークン管理・共通HTTP呼び出し (401/429リトライ込み)
    KabuApiPositions   : 残高照会 (GET /positions) → 現物・信用ポジション
    KabuApiSymbol      : 銘柄情報 (GET /symbol/{symbol}) → 売買単位・呼値
    KabuApiOrders      : 注文照会 (GET /orders) → 未約定注文の検出
    KabuApiTrade       : 注文発行・取消 (POST /sendorder, DELETE /cancelorder)
    InstructionFile(s) : 指示ファイル4種の読み込み・解析・無効化書き戻し
    TradingEngine      : 周期処理エンジン (買い/売り/返済/売らない)
"""

from .config import KabuApiConfig
from .exceptions import KabuApiAuthError, KabuApiException
from .client import KabuApiClient
from .positions import KabuApiPositions, CashHolding, CreditPosition
from .symbol_info import KabuApiSymbol, tick_size
from .orders import KabuApiOrders
from .trading import KabuApiTrade
from .instruction_files import (
    BuyList,
    BuyRow,
    RetList,
    RetRow,
    SellList,
    SellRow,
    NoSellList,
    parse_csv_line,
)
from .conditions import (
    buy_condition,
    sell_condition,
    settle_signal,
    settle_targets,
)
from .engine import TradingEngine, MarketHours

__all__ = [
    "KabuApiConfig",
    "KabuApiException",
    "KabuApiAuthError",
    "KabuApiClient",
    "KabuApiPositions",
    "CashHolding",
    "CreditPosition",
    "KabuApiSymbol",
    "tick_size",
    "KabuApiOrders",
    "KabuApiTrade",
    "BuyList",
    "BuyRow",
    "RetList",
    "RetRow",
    "SellList",
    "SellRow",
    "NoSellList",
    "parse_csv_line",
    "buy_condition",
    "sell_condition",
    "settle_signal",
    "settle_targets",
    "TradingEngine",
    "MarketHours",
]
