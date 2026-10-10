"""kabuステーションAPI 自動取引 メインエントリポイント。

事前に環境変数を設定しておくこと:
    export KABU_API_HOST="localhost"          # kabuステーション動作ホスト
    export KABU_API_PORT="18080"              # 本番ポート (検証は +1)
    export KABU_API_PW_PROD="<本番パスワード>"
    export KABU_API_PW_DEV="<検証パスワード>"   # dev モード時に参照

使用方法:
    # 1回だけ実行 (default)
    python kabu_trade.py --mode dev

    # 60秒ごとに繰り返し (24時間)
    python kabu_trade.py --mode dev --cycle 60

    # 待機なしで連続実行
    python kabu_trade.py --mode dev --cycle 0

    # 10分ごとに市場時間(9:05-11:30, 12:30-15:24)のみ繰り返し
    python kabu_trade.py --mode prod --cycle 600 --market-hours
"""

import argparse
import sys

from kabu_api import KabuApiConfig, TradingEngine


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="kabuステーションAPI 自動取引ツール "
        "(買list/売list/返済list/売らないlist に基づく周期処理)"
    )
    parser.add_argument(
        "--mode", choices=["prod", "dev"], default="prod",
        help="稼働モード: prod=本番(実取引) / dev=開発(実取引しない)。default: prod",
    )
    parser.add_argument(
        "--cycle", type=int, default=9999,
        help="周期: 0=待機なし連続実行 / 1..3600=秒間隔 / 9999=1回のみ実行。default: 9999",
    )
    parser.add_argument(
        "--market-hours", action="store_true",
        help="市場時間(9:05-11:30, 12:30-15:24)のみ稼働。未指定なら Ctrl+C まで実行",
    )
    parser.add_argument(
        "--buylist", default="./list_buy.txt",
        help="買指示リストのパス。default: ./list_buy.txt",
    )
    parser.add_argument(
        "--selllist", default="./list_sell.txt",
        help="売指示リストのパス。default: ./list_sell.txt",
    )
    parser.add_argument(
        "--retlist", default="./list_ret.txt",
        help="信用返済リストのパス。default: ./list_ret.txt",
    )
    parser.add_argument(
        "--sellnolist", default="./list_no_sell.txt",
        help="売らないリストのパス。default: ./list_no_sell.txt",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.cycle not in (0, 9999) and not (1 <= args.cycle <= 3600):
        print("エラー: --cycle は 0 / 1〜3600 / 9999 のいずれかを指定してください。",
              file=sys.stderr)
        return 1

    engine = TradingEngine(
        config=KabuApiConfig(),
        mode=args.mode,
        cycle=args.cycle,
        market_hours=args.market_hours,
        buylist_path=args.buylist,
        selllist_path=args.selllist,
        retlist_path=args.retlist,
        sellnolist_path=args.sellnolist,
    )
    try:
        engine.setup()
    except Exception as e:
        print(f"エラー: 起動時処理に失敗しました: {e}", file=sys.stderr)
        return 1

    engine.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
