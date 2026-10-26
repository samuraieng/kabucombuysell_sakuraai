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


def settle_targets(side: str, price: float, spread_plus: float, spread_minus: float, tick: float = 1.0) -> dict:
    """信用建玉1件に対する返済条件#5 / #6 の目標価格を計算する。

    Args:
        side: 建玉の売買区分 ("1"=売建 / "2"=買建)
        price: 確定値 (建玉の取得価額)
        spread_plus:  利確幅の呼値単位倍数 (0 → 1)
        spread_minus: 損切幅の呼値単位倍数 (0 → 5)
        tick: 呼値単位 (円)

    Returns:
        {
            "close_side":    返済方向の Side ("1"=返済売 / "2"=返済買),
            "limit_price":   条件#5 の指値価格,
            "stop_current":  条件#6-1 強制返済の発火現在値,
            "watch_price":   条件#6-2 ヒストリ監視の監視金額,
            "stop_history":  条件#6-2 発火時の損切り金額,
            "stop_time_3s":  条件#6-3 3秒経過時の損切り金額,
            "stop_time_5s":  条件#6-3 5秒経過時の損切り金額,
        }
    """
    if spread_plus <= 0:
        spread_plus = 1.0
    if spread_minus <= 0:
        spread_minus = 5.0

    plus_amount = spread_plus * tick
    minus_amount = spread_minus * tick

    if side == "2":
        # 買建玉 → 売り返済
        return {
            "close_side": SIDE_SELL,
            "limit_price": price + plus_amount,
            "stop_current": price - minus_amount,   # 現在値 <= で損切り
            "watch_price": price - tick,             # 現在値 <= で buffer に 1
            "stop_history": price - tick,            # 条件#6-2 発火時の損切り金額
            "stop_time_3s": price,                   # 建玉価格（∓ 0 tick）
            "stop_time_5s": price - minus_amount * 6,
        }
    if side == "1":
        # 売建玉 → 買い返済
        return {
            "close_side": SIDE_BUY,
            "limit_price": price - plus_amount,
            "stop_current": price + minus_amount,   # 現在値 >= で損切り
            "watch_price": price + tick,             # 現在値 >= で buffer に 1
            "stop_history": price + tick,            # 条件#6-2 発火時の損切り金額
            "stop_time_3s": price,                   # 建玉価格（∓ 0 tick）
            "stop_time_5s": price + minus_amount * 6,
        }
    return {
        "close_side": "",
        "limit_price": 0.0,
        "stop_current": 0.0,
        "watch_price": 0.0,
        "stop_history": 0.0,
        "stop_time_3s": 0.0,
        "stop_time_5s": 0.0,
    }


def settle_signal(targets: dict, current: float, history: list[int] | None = None,
                  buffer_size: int = 3, elapsed_seconds: float | None = None) -> tuple[str | None, str | None]:
    """現在値から返済の発動要否と執行条件を返す。

    Args:
        targets: settle_targets() の戻り値
        current: 現在値
        history: 条件#6-2 用の履歴バッファ（1/0）。呼び出し側で毎サイクル更新済みのもの。
        buffer_size: 履歴バッファサイズ（デフォルト 3）
        elapsed_seconds: 建玉入手（初認識）からの経過秒数（条件#6-3 用）

    Returns:
        (signal, reason)
        signal: "market" / "limit" / None
        reason: "6-1" / "6-2" / "6-3-3s" / "6-3-5s" / "5" / None
    """
    close_side = targets.get("close_side", "")
    if not close_side:
        return (None, None)

    candidates: list[tuple[str, float]] = []  # (reason, stop_price)

    # 条件#6-1 強制返済
    stop_current = targets.get("stop_current", 0.0)
    if close_side == "1":
        if stop_current > 0 and current <= stop_current:
            candidates.append(("6-1", stop_current))
    else:
        if stop_current > 0 and current >= stop_current:
            candidates.append(("6-1", stop_current))

    # 条件#6-2 ヒストリ監視
    if history is not None:
        threshold = buffer_size // 2 + 1
        if sum(history) >= threshold:
            stop_history = targets.get("stop_history", 0.0)
            if stop_history > 0:
                candidates.append(("6-2", stop_history))

    # 条件#6-3 時間監視
    if elapsed_seconds is not None:
        if elapsed_seconds >= 5:
            stop = targets.get("stop_time_5s", 0.0)
            if close_side == "1":
                if stop > 0 and current <= stop:
                    candidates.append(("6-3-5s", stop))
            else:
                if stop > 0 and current >= stop:
                    candidates.append(("6-3-5s", stop))
        elif elapsed_seconds >= 3:
            stop = targets.get("stop_time_3s", 0.0)
            if close_side == "1":
                if stop > 0 and current <= stop:
                    candidates.append(("6-3-3s", stop))
            else:
                if stop > 0 and current >= stop:
                    candidates.append(("6-3-3s", stop))

    # 最も不利な損切り金額を選択
    if candidates:
        if close_side == "1":
            # 売り返済：低い価格ほど不利
            reason, _price = min(candidates, key=lambda x: x[1])
        else:
            # 買い返済：高い価格ほど不利
            reason, _price = max(candidates, key=lambda x: x[1])
        return ("market", reason)

    # 条件#5 指値返済
    limit_price = targets.get("limit_price", 0.0)
    if limit_price <= 0:
        return (None, None)
    return ("limit", "5")