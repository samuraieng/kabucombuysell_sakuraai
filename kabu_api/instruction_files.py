"""指示ファイル4種 (buylist / selllist / retlist / sellnolist) の処理。

ファイル共通ルール:
    - カンマ区切りCSV (ヘッダー行があれば自動スキップ)
    - 行頭が "#" または "//" → コメント行
    - 行中に "#" または "//" が出現 → 以降をコメントとして無視
    - ファイルが無くても空リストとして動作継続

書式 (カラム):
    list_buy.txt  : code, price, qty, trigger_price
    list_sell.txt : code, price, qty, trigger_price
    list_ret.txt  : code, spread_plus, spread_minus, spread_minus_max, count_max
    list_no_sell.txt : code (1行1コード)
"""

import re
from dataclasses import dataclass, field

from .logger import get_logger

logger = get_logger(__name__)

COMMENT_PREFIXES = ("#", "//")
CODE_RE = re.compile(r"^[A-Za-z0-9]{4,5}$")


# ======================================================================
# 行解析ユーティリティ
# ======================================================================
def split_comment(line: str) -> str:
    """コメント部を除去した実効内容を返す。

    行頭コメント ("#"/"//") も行中コメントも扱う。
    """
    idx = len(line)
    for p in COMMENT_PREFIXES:
        pos = line.find(p)
        if pos != -1:
            idx = min(idx, pos)
    return line[:idx]


def parse_csv_line(line: str) -> list[str]:
    """1行をコメント除去・カンマ分割・前後空白除去したフィールド列で返す。"""
    content = split_comment(line).strip()
    if not content:
        return []
    return [f.strip() for f in content.split(",")]


def to_float(value: str | None) -> float | None:
    """文字列をfloatに変換 (失敗時 None)。"""
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    try:
        f = float(v)
    except ValueError:
        return None
    return f


def to_int(value: str | None) -> int | None:
    """文字列をintに変換 (失敗時 None)。"""
    f = to_float(value)
    if f is None:
        return None
    return int(f)


# ======================================================================
# 行データ
# ======================================================================
@dataclass
class BuyRow:
    """list_buy.txt の1行: code, price, qty, trigger_price。

    条件#1: price 指値買 (0 → 成行)
    条件#2: current <= trigger_price で成行買 (trigger_price=0 → 条件なし)
    """

    code: str
    price: float
    qty: int
    trigger_price: float
    line_no: int
    raw_line: str
    active: bool = True


@dataclass
class SellRow:
    """list_sell.txt の1行: code, price, qty, trigger_price。

    条件#3: price 指値売 (0 → 成行)
    条件#4: current >= trigger_price で成行売 (trigger_price=0 → 条件なし)
    qty=0 または所有数超過 → 全数売り
    """

    code: str
    price: float
    qty: int
    trigger_price: float
    line_no: int
    raw_line: str
    active: bool = True


@dataclass
class RetRow:
    """list_ret.txt の1行: code, spread_plus, spread_minus, spread_minus_max, count_max。

    spread_plus:        利確幅の呼値単位倍数。空欄/0 → 1（呼値単位×1）。
    spread_minus:       条件#6-2 ヒストリ監視の監視幅の呼値単位倍数。空欄/0 → 2。
    spread_minus_max:   条件#6-1 強制返済の損切り幅の呼値単位倍数。空欄/0 → spread_minus の値。
    count_max:          条件#6-2 の n-strike-out カウント。空欄/0 → 3。
    """

    code: str
    spread_plus: float       # 利確幅の呼値単位倍数
    spread_minus: float      # 条件#6-2 監視幅の呼値単位倍数
    spread_minus_max: float  # 条件#6-1 強制返済幅の呼値単位倍数
    count_max: int           # 条件#6-2 の n-strike-out カウント
    line_no: int
    raw_line: str
    tick: float = 0.0        # 読み込み時に /symbol から取得した呼値単位
    active: bool = True

    def effective_spread_plus(self) -> float:
        """実効利確幅（呼値単位倍数）を返す (0指定時は1)。"""
        if self.spread_plus and self.spread_plus > 0:
            return self.spread_plus
        return 1.0

    def effective_spread_minus(self) -> float:
        """実効監視幅（呼値単位倍数）を返す (0指定時は2)。"""
        if self.spread_minus and self.spread_minus > 0:
            return self.spread_minus
        return 2.0

    def effective_spread_minus_max(self) -> float:
        """実効強制返済幅（呼値単位倍数）を返す (0指定時は spread_minus の値)。"""
        if self.spread_minus_max and self.spread_minus_max > 0:
            return self.spread_minus_max
        return self.effective_spread_minus()

    def effective_count_max(self) -> int:
        """実効 n-strike-out カウントを返す (0指定時は3)。"""
        if self.count_max and self.count_max > 0:
            return self.count_max
        return 3

    def normalized_spreads(self) -> tuple[float, float]:
        """spread_minus と spread_minus_max の大小関係を正規化して返す。

        spread_minus > spread_minus_max の場合は入れ替える。
        """
        sm = self.effective_spread_minus()
        smm = self.effective_spread_minus_max()
        return min(sm, smm), max(sm, smm)


@dataclass
class InstructionFile:
    """指示ファイルの共通コンテナ。

    raw_lines: 元ファイルの行 (書き戻し用)
    rows: 解析済み有効行
    """

    path: str
    raw_lines: list[str] = field(default_factory=list)
    rows: list = field(default_factory=list)

    def load(self) -> "InstructionFile":
        """ファイルを読み込み rows を再構築する。サブクラスで _parse を実装。"""
        self.raw_lines = []
        self.rows = []
        try:
            with open(self.path, encoding="utf-8") as f:
                self.raw_lines = f.readlines()
        except FileNotFoundError:
            logger.warning(f"指示ファイルが見つかりません(空リスト扱い): {self.path}")
            return self
        except OSError as e:
            logger.error(f"指示ファイル読込エラー({self.path}): {e}")
            return self

        header_seen = False
        for line_no, raw in enumerate(self.raw_lines, start=1):
            fields = parse_csv_line(raw)
            if not fields:
                continue
            if not header_seen and not CODE_RE.match(fields[0]):
                # 先頭有効行がコードでない → ヘッダー行としてスキップ
                header_seen = True
                continue
            if not CODE_RE.match(fields[0]):
                # 銘柄コード不正 (4-5桁の数字以外) → スキップ
                logger.warning(f"  [警告] {self.path} 行{line_no}: 銘柄コード不正 → スキップ")
                continue
            row = self._parse(fields, line_no, raw)
            if row is not None:
                self.rows.append(row)

        logger.info(f"指示ファイル読込: {self.path} ({len(self.rows)} 行)")
        return self

    def _parse(self, fields: list[str], line_no: int, raw_line: str):
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 書き戻し
    # ------------------------------------------------------------------
    def disable_row(self, row) -> bool:
        """該当行の先頭に "#" を付与して無効化し、ファイルへ書き戻す。"""
        if row.line_no < 1 or row.line_no > len(self.raw_lines):
            return False
        if not row.active:
            return True
        raw = self.raw_lines[row.line_no - 1].rstrip("\n")
        if raw.lstrip().startswith(COMMENT_PREFIXES):
            row.active = False
            return True
        self.raw_lines[row.line_no - 1] = f"#{raw}\n"
        row.active = False
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                f.writelines(self.raw_lines)
        except OSError as e:
            logger.error(f"指示ファイル書込エラー({self.path}): {e}")
            return False
        logger.info(f"  [無効化] {self.path} {row.code} 行{row.line_no}: # を付与")
        return True

    def active_rows(self) -> list:
        return [r for r in self.rows if r.active]


# ======================================================================
# 4種の指示ファイル
# ======================================================================
class BuyList(InstructionFile):
    """list_buy.txt: code, price, qty, trigger_price"""

    def _parse(self, fields: list[str], line_no: int, raw_line: str):
        if len(fields) < 4:
            logger.warning(f"  [警告] {self.path} 行{line_no}: カラム不足 (code,price,qty,trigger_price)")
            return None
        code = fields[0]
        price = to_float(fields[1])
        qty = to_int(fields[2])
        trigger = to_float(fields[3])
        if price is None or qty is None or trigger is None:
            logger.warning(f"  [警告] {self.path} 行{line_no}: 数値項目不正 → スキップ")
            return None
        return BuyRow(
            code=code, price=price, qty=qty, trigger_price=trigger,
            line_no=line_no, raw_line=raw_line,
        )


class SellList(InstructionFile):
    """list_sell.txt: code, price, qty, trigger_price"""

    def _parse(self, fields: list[str], line_no: int, raw_line: str):
        if len(fields) < 4:
            logger.warning(f"  [警告] {self.path} 行{line_no}: カラム不足 (code,price,qty,trigger_price)")
            return None
        code = fields[0]
        price = to_float(fields[1])
        qty = to_int(fields[2])
        trigger = to_float(fields[3])
        if price is None or qty is None or trigger is None:
            logger.warning(f"  [警告] {self.path} 行{line_no}: 数値項目不正 → スキップ")
            return None
        return SellRow(
            code=code, price=price, qty=qty, trigger_price=trigger,
            line_no=line_no, raw_line=raw_line,
        )


class RetList(InstructionFile):
    """list_ret.txt: code, spread_plus, spread_minus, spread_minus_max, count_max"""

    def _parse(self, fields: list[str], line_no: int, raw_line: str):
        if len(fields) < 1:
            return None
        code = fields[0]
        spread_plus = to_float(fields[1]) if len(fields) >= 2 else None
        spread_minus = to_float(fields[2]) if len(fields) >= 3 else None
        spread_minus_max = to_float(fields[3]) if len(fields) >= 4 else None
        count_max = to_int(fields[4]) if len(fields) >= 5 else None
        if spread_plus is None:
            spread_plus = 0.0
        if spread_minus is None:
            spread_minus = 0.0
        if spread_minus_max is None:
            spread_minus_max = 0.0
        if count_max is None:
            count_max = 0
        return RetRow(
            code=code, spread_plus=spread_plus, spread_minus=spread_minus,
            spread_minus_max=spread_minus_max, count_max=count_max,
            line_no=line_no, raw_line=raw_line,
        )


class NoSellList(InstructionFile):
    """list_no_sell.txt: code を1行1コードで列挙 (売らない銘柄)。"""

    def _parse(self, fields: list[str], line_no: int, raw_line: str):
        if not CODE_RE.match(fields[0]):
            logger.warning(f"  [警告] {self.path} 行{line_no}: 銘柄コード不正 → スキップ")
            return None
        # 単純化のため RetRow 形式を流用 (spread_* / count_max は未使用)
        return RetRow(
            code=fields[0], spread_plus=0.0, spread_minus=0.0,
            spread_minus_max=0.0, count_max=0,
            line_no=line_no, raw_line=raw_line,
        )

    def codes(self) -> set:
        return {r.code for r in self.rows}

    def contains(self, code: str) -> bool:
        return code in self.codes()
