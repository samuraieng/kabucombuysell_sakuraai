#!/usr/bin/env python3
"""API通信ログから指定日の発注履歴をCSVに抽出するスクリプト。

使用例:
    python get_history.py 2026/10/23
    python get_history.py 20261023 -o /tmp/trades.csv --success-only
    python get_history.py 2026-10-23 --detail

対象ログ:
    KABU_API_LOG_DIR 環境変数で指定されたディレクトリ内の
    ``YYYYMMDD_*_POST_sendorder.json`` ファイル。
    未設定時は ``/tmp/kabu_api_logs`` を参照する。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from kabu_api import KabuApiConfig

SIDE_LABELS = {"1": "売り", "2": "買い"}
CASHMARGIN_LABELS = {1: "現物", 2: "信用新規", 3: "信用返済"}
FRONTORDER_LABELS = {10: "成行", 20: "指値"}


def parse_date(value: str) -> str:
    """YYYY/MM/DD または YYYYMMDD 形式を YYYYMMDD に正規化する。"""
    normalized = value.replace("-", "").replace("/", "")
    if len(normalized) != 8 or not normalized.isdigit():
        raise argparse.ArgumentTypeError(
            f"日付は YYYY/MM/DD または YYYYMMDD 形式で指定してください: {value}"
        )
    try:
        datetime.strptime(normalized, "%Y%m%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"無効な日付です: {value}") from exc
    return normalized


def resolve_log_dir(args_log_dir: str | None) -> Path:
    """ログディレクトリを解決する。"""
    if args_log_dir:
        return Path(args_log_dir)
    return Path(KabuApiConfig().log_dir)


def _label(value, mapping: dict) -> str:
    """mapping に存在すればラベルを返し、無ければ文字列化して返す。"""
    return mapping.get(value, str(value))


def extract_sendorders(log_dir: Path, date: str) -> list[dict]:
    """指定日の POST /sendorder ログを時系列順にパースして返す。"""
    prefix = f"{date}_"
    suffix = "_POST_sendorder.json"
    rows: list[dict] = []

    if not log_dir.is_dir():
        raise FileNotFoundError(f"ログディレクトリが存在しません: {log_dir}")

    # os.listdir は巨大ディレクトリでも os.scandir より軽量に名前だけ取れる
    for name in os.listdir(log_dir):
        if not (name.startswith(prefix) and name.endswith(suffix)):
            continue
        path = log_dir / name
        if not path.is_file():
            continue

        with open(path, encoding="utf-8") as fh:
            try:
                log_entry = json.load(fh)
            except json.JSONDecodeError as exc:
                print(f"警告: JSONパース失敗 {name}: {exc}", file=sys.stderr)
                continue

        body = log_entry.get("request", {}).get("body") or {}
        response = log_entry.get("response", {}) or {}

        side_code = body.get("Side", "")
        cashmargin = body.get("CashMargin")
        front_order_type = body.get("FrontOrderType")

        rows.append(
            {
                "timestamp": log_entry.get("timestamp", ""),
                "symbol": body.get("Symbol", ""),
                "side": _label(side_code, SIDE_LABELS),
                "qty": body.get("Qty", ""),
                "price": body.get("Price", ""),
                "status_code": response.get("status_code", ""),
                "cashmargin": _label(cashmargin, CASHMARGIN_LABELS),
                "front_order_type": _label(front_order_type, FRONTORDER_LABELS),
            }
        )

    rows.sort(key=lambda r: r["timestamp"])
    return rows


def write_csv(rows: list[dict], out_path: Path | None, detail: bool) -> None:
    """抽出結果を CSV 形式で出力する。"""
    base_columns = ["取引時間", "銘柄", "売り・買い", "数量", "価格"]
    extra_columns = ["HTTPステータス", "注文種別", "執行条件"]
    headers = base_columns + (extra_columns if detail else [])

    def to_row(row: dict) -> list:
        return [
            row["timestamp"],
            row["symbol"],
            row["side"],
            row["qty"],
            row["price"],
            *( [row["status_code"], row["cashmargin"], row["front_order_type"]] if detail else [] ),
        ]

    if out_path is None:
        writer = csv.writer(sys.stdout)
        writer.writerow(headers)
        writer.writerows(to_row(r) for r in rows)
        return

    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        writer.writerows(to_row(r) for r in rows)
    print(f"{len(rows)} 件を {out_path} に書き出しました。", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="API通信ログから指定日の発注履歴を CSV で出力する。"
    )
    parser.add_argument(
        "date",
        type=parse_date,
        help="抽出対象日 (YYYY/MM/DD または YYYYMMDD)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="出力ファイル名。未指定時は 'history_YYYYMMDD.csv' を作成する。",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="ファイルではなく標準出力に出力する。",
    )
    parser.add_argument(
        "--success-only",
        action="store_true",
        help="HTTP 200 で受付成功した注文のみ抽出する。",
    )
    parser.add_argument(
        "--detail",
        action="store_true",
        help="HTTPステータス・注文種別・執行条件列を追加する。",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="API通信ログディレクトリ。未指定時は KABU_API_LOG_DIR 環境変数 / 既定値を使用。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    log_dir = resolve_log_dir(args.log_dir)
    print(f"ログディレクトリ: {log_dir}", file=sys.stderr)

    try:
        rows = extract_sendorders(log_dir, args.date)
    except FileNotFoundError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1

    if args.success_only:
        rows = [r for r in rows if str(r["status_code"]) == "200"]

    if not rows:
        print("該当する発注ログが見つかりませんでした。", file=sys.stderr)
        return 0

    if args.stdout:
        out_path: Path | None = None
    elif args.output:
        out_path = args.output
    else:
        out_path = Path(f"history_{args.date}.csv")

    write_csv(rows, out_path, detail=args.detail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
