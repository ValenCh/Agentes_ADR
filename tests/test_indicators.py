import numpy as np
import pandas as pd
import pytest

from market.indicators import MIN_CANDLES, bollinger, compute_indicators, ema, latest_snapshot, rsi, sma


def test_sma_matches_manual_mean():
    s = pd.Series(np.arange(1.0, 31.0))
    assert sma(s, 5).iloc[-1] == pytest.approx(np.mean(s.iloc[-5:]))
    assert np.isnan(sma(s, 5).iloc[3])


def test_ema_matches_manual_recursion():
    s = pd.Series([10, 11, 12, 11, 13, 14, 13, 15.0])
    alpha = 2 / (5 + 1)
    manual = [s.iloc[0]]
    for x in s.iloc[1:]:
        manual.append(alpha * x + (1 - alpha) * manual[-1])
    assert ema(s, 5).tolist() == pytest.approx(manual)


def test_rsi_extremes_and_bounds(candles):
    up = pd.Series(np.arange(1.0, 61.0))
    down = pd.Series(np.arange(60.0, 0.0, -1.0))
    flat = pd.Series([5.0] * 60)
    assert rsi(up).iloc[-1] == pytest.approx(100.0)
    assert rsi(down).iloc[-1] == pytest.approx(0.0, abs=1e-6)
    assert rsi(flat).iloc[-1] == pytest.approx(50.0)
    r = rsi(candles["close"]).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_rsi_matches_manual_wilder_recursion():
    prices = pd.Series([44, 44.3, 44.1, 44.5, 43.9, 44.6, 45.1, 45.4, 45.8, 46.1, 45.9, 46.2,
                        46.5, 46.3, 46.8, 47.0, 46.6, 46.9, 47.3, 47.1], dtype=float)
    n = 14
    delta = prices.diff().to_numpy()
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    ag, al = gain[1], loss[1]
    for i in range(2, len(prices)):
        ag = (1 - 1 / n) * ag + (1 / n) * gain[i]
        al = (1 - 1 / n) * al + (1 / n) * loss[i]
    expected = 100 - 100 / (1 + ag / al)
    assert rsi(prices, n).iloc[-1] == pytest.approx(expected, rel=1e-9)


def test_bollinger_matches_numpy(candles):
    close = candles["close"]
    bb = bollinger(close, 20, 2.0)
    window = close.iloc[-20:].to_numpy()
    assert bb["sma20"].iloc[-1] == pytest.approx(window.mean())
    assert bb["bb_upper"].iloc[-1] == pytest.approx(window.mean() + 2 * window.std())
    assert bb["bb_lower"].iloc[-1] == pytest.approx(window.mean() - 2 * window.std())
    pb = (close.iloc[-1] - bb["bb_lower"].iloc[-1]) / (bb["bb_upper"].iloc[-1] - bb["bb_lower"].iloc[-1])
    assert bb["percent_b"].iloc[-1] == pytest.approx(pb)


def test_compute_indicators_adds_columns_and_needs_enough_candles(candles):
    out = compute_indicators(candles)
    for col in ("rsi14", "ema20", "ema50", "sma20", "bb_upper", "bb_lower", "percent_b"):
        assert col in out.columns
        assert not np.isnan(out[col].iloc[-1])
    with pytest.raises(ValueError, match="al menos"):
        compute_indicators(candles.head(MIN_CANDLES - 1))


def test_latest_snapshot_is_consistent_with_columns(candles):
    snap = latest_snapshot(candles)
    ind = compute_indicators(candles).iloc[-1]
    assert snap["price"] == pytest.approx(round(ind["close"], 2))
    assert snap["rsi14"] == pytest.approx(round(ind["rsi14"], 2))
    assert snap["bb_lower"] < snap["price"] < snap["bb_upper"] or snap["percent_b"] <= 0 or snap["percent_b"] >= 1
