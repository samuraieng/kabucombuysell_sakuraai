"""売買条件の評価 (API非依存の純粋関数群)。テスト容易性のため分離。"""

from .trading import SIDE_BUY, SIDE_SELL


def buy_condition(price: float, trigger_price: float, current: float) -> str | None:
    """list_buy の条件#1 / 条件#2 を評価する。

    Args:
        price: 指値指定 (0 → 成行)
        trigger_price: 条件#2 のトリガー価格 (0 → 条件なし)
        current: 現在値

    Returns:
        "market"  : 成行買を発注すべき (条件#1の0指定 または 条件#2成立)
        "limit"   : price で指値買を発注すべき (条件#1)
        None      : 条件未成立
    """
    # 条件#2: トリガー指定があり、現在値がトリガー以下 → 成行(即時)
    if trigger_price and trigger_price > 0 and current <= trigger_price:
        return "market"
    # 条件#1: 0指定 → 成行(即時)
    if price == 0:
        return "market"
    # 条件#1: 指値
    if price > 0:
        return "limit"
    return None


def sell_condition(price: float, trigger_price: float, current: float) -> str | None:
    """list_sell の条件#3 / 条件#4 を評価する。

    Args:
        price: 指値指定 (0 → 成行)
        trigger_price: 条件#4 のトリガー価格 (0 → 条件なし)
        current: 現在値

    Returns:
        "market"  : 成行売を発注すべき (条件#3の0指定 または 条件#4成立)
        "limit"   : price で指値売を発注すべき (条件#3)
        None      : 条件未成立
    """
    # 条件#4: トリガー指定があり、現在値がトリガー以上 → 成行(即時)
    if trigger_price and trigger_price > 0 and current >= trigger_price:
        return "market"
    # 条件#3: 0指定 → 成行(即時)
    if price == 0:
        return "market"
    # 条件#3: 指値
    if price > 0:
        return "limit"
    return None


def settle_targets(side: str, price: float, spread_plus: float, spread_minus: float) -> dict:
    """信用建玉1件に対する返済条件#5 / #6 の目標価格を計算する。

    Args:
        side: 建玉の売買区分 ("1"=売建 / "2"=買建)
        price: 確定値 (建玉の取得価額)
        spread_plus:  利確幅の金額 (1株あたり, 円)
        spread_minus: 損切幅の金額 (1株あたり, 円)

    Returns:
        {
            "close_side": 返済方向の Side ("1"=返済売 / "2"=返済買),
            "limit_price":  条件#5 の指値価格,
            "stop_current": 条件#6 の発火現在値 (この価格に達したら成行),
        }
    """
    if spread_plus <= 0:
        spread_plus = 1.0
    if spread_minus <= 0:
        spread_minus = 2.0

    if side == "2":
        # 買建玉 → 売り返済
        return {
            "close_side": SIDE_SELL,
            "limit_price": price + spread_plus,
            "stop_current": price - spread_minus,  # 現在値 <= で損切り
        }
    if side == "1":
        # 売建玉 → 買い返済
        return {
            "close_side": SIDE_BUY,
            "limit_price": price - spread_plus,
            "stop_current": price + spread_minus,  # 現在値 >= で損切り
        }
    return {"close_side": "", "limit_price": 0.0, "stop_current": 0.0}


def settle_signal(targets: dict, current: float) -> str | None:
    """現在値から返済の発動要否と執行条件を返す。

    Returns:
        "market" : 条件#6成立 → 成行返済 (反対指示条件)
        "limit"  : 条件#5成立 → 指値返済
        None     : 条件未成立
    """
    close_side = targets.get("close_side", "")
    if not close_side:
        return None
    stop = targets.get("stop_current", 0.0)
    if close_side == "1":
        # 売り返済: 損切りは現在値が下落したとき
        if stop > 0 and current <= stop:
            return "market"
    else:
        # 買い返済: 損切りは現在値が上昇したとき
        if stop > 0 and current >= stop:
            return "market"
    limit_price = targets.get("limit_price", 0.0)
    if limit_price <= 0:
        # 指値が0以下 (売建の確定売値が指定金額未満など) → 指値不可、成行は危険
        return None
    return "limit"


# =============================================================================
# 新しい条件#5 / 条件#6-1 / 条件#6-2 評価
# =============================================================================
def normalize_spreads(spread_minus: float, spread_minus_max: float) -> tuple[float, float]:
    """spread_minus と spread_minus_max の大小関係を正規化する。

    spread_minus > spread_minus_max の場合は入れ替える。
    """
    if spread_minus > spread_minus_max:
        return spread_minus_max, spread_minus
    return spread_minus, spread_minus_max


def settle_limit_price(side: str, price: float, spread_plus: float) -> float:
    """条件#5 の指値価格を計算する。"""
    if spread_plus <= 0:
        spread_plus = 1.0
    if side == "2":
        return price + spread_plus
    if side == "1":
        return price - spread_plus
    return 0.0


def stop_loss_price(side: str, price: float, spread_minus_max: float) -> float:
    """条件#6-1 強制返済の損切り金額を計算する。"""
    if side == "2":
        return price - spread_minus_max
    if side == "1":
        return price + spread_minus_max
    return 0.0


def history_watch_price(side: str, price: float, spread_minus: float) -> float:
    """条件#6-2 ヒストリ監視の監視金額を計算する。"""
    if side == "2":
        return price - spread_minus
    if side == "1":
        return price + spread_minus
    return 0.0


def settle_status_condition5() -> str:
    return "condition-5"


def settle_status_condition6_1(spread_minus_max: float) -> str:
    return f"condition-6-1-{int(spread_minus_max)}"


def settle_status_condition6_2(spread_minus: float, counter: int, count_max: int) -> str:
    return f"condition-6-2-{int(spread_minus)}-{counter}-{count_max}"


def is_condition6_1_status(status: str) -> bool:
    return status.startswith("condition-6-1-")


def is_condition6_2_status(status: str) -> bool:
    return status.startswith("condition-6-2-")


def parse_condition6_1_status(status: str) -> int | None:
    """condition-6-1-{spread_minus_max} から spread_minus_max を取り出す。"""
    if not is_condition6_1_status(status):
        return None
    parts = status.split("-")
    # "condition-6-1-{spread_minus_max}" → ['condition', '6', '1', spread_minus_max]
    if len(parts) != 4:
        return None
    try:
        return int(parts[3])
    except ValueError:
        return None


def parse_condition6_2_status(status: str) -> tuple[int, int, int] | None:
    """condition-6-2-{spread_minus}-{counter}-{count_max} をパースする。"""
    if not is_condition6_2_status(status):
        return None
    parts = status.split("-")
    # "condition-6-2-{spread_minus}-{counter}-{count_max}" → 6要素
    if len(parts) != 6:
        return None
    try:
        return int(parts[3]), int(parts[4]), int(parts[5])
    except ValueError:
        return None


def _is_hit_condition6_2(side: str, current: float, watch: float) -> bool:
    """条件#6-2 の監視金額（および条件#6-1 の損切り金額）に達しているかどうか。"""
    if side == "2":
        return current <= watch
    if side == "1":
        return current >= watch
    return False


def settle_action(
    side: str,
    price: float,
    current: float,
    spread_plus: float,
    spread_minus: float,
    spread_minus_max: float,
    count_max: int,
    status: str,
    counter: int,
) -> dict:
    """現在の状態から次の返済アクションを決定する。

    Args:
        side: 建玉の売買区分 ("1"=売建 / "2"=買建)
        price: 確定値 (建玉の取得価額)
        current: 現在値
        spread_plus: 利確幅の金額 (1株あたり, 円)
        spread_minus: 条件#6-2 監視幅の金額 (1株あたり, 円)
        spread_minus_max: 条件#6-1 強制返済幅の金額 (1株あたり, 円)
        count_max: 条件#6-2 の n-strike-out カウント
        status: 現在の処理ステータス
        counter: 条件#6-2 の連続ヒットカウント

    Returns:
        {
            "action": "none" | "limit" | "market",
            "price": float,          # limit 時の価格
            "next_status": str,
            "next_counter": int,
            "reason": str,
            "close_side": str,       # 返済方向 Side
        }
    """
    spread_minus, spread_minus_max = normalize_spreads(spread_minus, spread_minus_max)
    if spread_plus <= 0:
        spread_plus = 1.0
    if count_max <= 0:
        count_max = 3

    close_side = ""
    if side == "2":
        close_side = SIDE_SELL
    elif side == "1":
        close_side = SIDE_BUY

    limit_price = settle_limit_price(side, price, spread_plus)
    stop_loss = stop_loss_price(side, price, spread_minus_max)
    watch = history_watch_price(side, price, spread_minus)

    hit_c6_2 = _is_hit_condition6_2(side, current, watch)
    hit_c6_1 = _is_hit_condition6_2(side, current, stop_loss)

    def _c6_2_result(next_counter: int, reason_prefix: str) -> dict:
        next_status = settle_status_condition6_2(spread_minus, next_counter, count_max)
        if next_counter >= count_max - 1:
            return {
                "action": "market",
                "price": 0.0,
                "next_status": next_status,
                "next_counter": next_counter,
                "reason": f"{reason_prefix} 発火（成行）",
                "close_side": close_side,
            }
        return {
            "action": "none",
            "price": 0.0,
            "next_status": next_status,
            "next_counter": next_counter,
            "reason": f"{reason_prefix} 監視中",
            "close_side": close_side,
        }

    if status == settle_status_condition5():
        if hit_c6_1:
            return {
                "action": "limit",
                "price": stop_loss,
                "next_status": settle_status_condition6_1(spread_minus_max),
                "next_counter": 0,
                "reason": "条件#6-1 発火（指値返済）",
                "close_side": close_side,
            }
        if hit_c6_2:
            return _c6_2_result(1, "条件#6-2")
        return {
            "action": "limit",
            "price": limit_price,
            "next_status": status,
            "next_counter": 0,
            "reason": "条件#5 指値返済",
            "close_side": close_side,
        }

    if is_condition6_1_status(status):
        if hit_c6_2:
            return _c6_2_result(1, "条件#6-2")
        return {
            "action": "limit",
            "price": stop_loss,
            "next_status": status,
            "next_counter": 0,
            "reason": "条件#6-1 指値返済維持",
            "close_side": close_side,
        }

    if is_condition6_2_status(status):
        parsed = parse_condition6_2_status(status)
        current_counter = parsed[1] if parsed else 0
        next_counter = current_counter + 1 if hit_c6_2 else 0
        return _c6_2_result(next_counter, "条件#6-2")

    # 未知の status は condition-5 としてリセット
    return {
        "action": "limit",
        "price": limit_price,
        "next_status": settle_status_condition5(),
        "next_counter": 0,
        "reason": "状態リセット（条件#5 指値返済）",
        "close_side": close_side,
    }