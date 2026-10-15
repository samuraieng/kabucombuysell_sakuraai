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
        spread_minus = 5.0

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