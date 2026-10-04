"""Genera el dataset SINTÉTICO de ejemplo y los escenarios de demostración.

IMPORTANTE: estos precios NO son reales. Se generan con una semilla fija a
partir de tramos de mercado con distinto comportamiento (tendencia alcista,
corrección, rally, lateral, caída) para que la demo sea reproducible sin
depender de lo que esté haciendo el mercado ese día.

Uso:
    python scripts/make_sample_data.py
Escribe data/sample_btcusdt_1h.csv y data/scenarios.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from market.indicators import compute_indicators  # noqa: E402

SEED = 42
START_PRICE = 60_000.0
START_TIME = "2025-01-01 00:00:00"

# (nombre, velas, deriva por vela, volatilidad por vela)
SEGMENTS = [
    ("warmup", 120, 0.0000, 0.0035),
    ("uptrend", 260, 0.0004, 0.0035),
    ("pullback", 34, -0.0030, 0.0040),
    ("recovery", 90, 0.0010, 0.0035),
    ("rally", 70, 0.0016, 0.0040),
    ("topping", 40, -0.0004, 0.0035),
    ("sideways", 220, 0.0000, 0.0018),
    ("crash", 70, -0.0036, 0.0060),
    ("rebound", 200, 0.0006, 0.0045),
]


def generate() -> tuple[pd.DataFrame, dict[str, tuple[int, int]]]:
    rng = np.random.default_rng(SEED)
    closes, bounds, price = [], {}, START_PRICE
    for name, n, drift, vol in SEGMENTS:
        start = len(closes)
        for _ in range(n):
            price *= 1 + rng.normal(drift, vol)
            closes.append(price)
        bounds[name] = (start, len(closes) - 1)
    close = np.array(closes)
    open_ = np.concatenate([[START_PRICE], close[:-1]])
    wick = np.abs(rng.normal(0, 0.0015, len(close)))
    high = np.maximum(open_, close) * (1 + wick)
    low = np.minimum(open_, close) * (1 - wick)
    volume = rng.lognormal(mean=3.0, sigma=0.4, size=len(close))
    times = pd.date_range(START_TIME, periods=len(close), freq="h", tz="UTC")
    df = pd.DataFrame(
        {
            "time": times,
            "open": open_.round(2),
            "high": high.round(2),
            "low": low.round(2),
            "close": close.round(2),
            "volume": volume.round(3),
        }
    )
    return df, bounds


def pick_scenarios(df: pd.DataFrame, bounds: dict[str, tuple[int, int]]) -> dict:
    ind = compute_indicators(df)
    rsi = ind["rsi14"]

    def argmin_in(seg):
        a, b = bounds[seg]
        return int(rsi.iloc[a : b + 1].idxmin())

    def argmax_in(seg):
        a, b = bounds[seg]
        return int(rsi.iloc[a : b + 1].idxmax())

    # Lateral: un punto neutro (RSI cerca de 50 y precio en el centro de las bandas)
    a, b = bounds["sideways"]
    q = (b - a) // 4
    mid = ind.iloc[a + q : b - q + 1]
    neutral = mid[(mid["percent_b"] - 0.5).abs() <= 0.2]
    pool = neutral if len(neutral) else mid
    sideways_idx = int((pool["rsi14"] - 50).abs().idxmin())

    # Caída: un punto tranquilo (RSI neutro) desde el que el precio cae >= 4,5 %
    # en las siguientes 12 velas. Se busca en los datos, no se fija a mano.
    a_c, b_c = bounds["crash"]
    close = ind["close"]
    crash_idx = a_c + 16
    for i in range(a_c, b_c - 12):
        if 35 <= rsi.iloc[i] <= 60 and close.iloc[i + 12] / close.iloc[i] - 1 <= -0.045:
            crash_idx = i
            break

    return {
        "dip": {"index": argmin_in("pullback")},
        "rally": {"index": argmax_in("rally")},
        "sideways": {"index": sideways_idx},
        "crash": {"index": crash_idx},
    }


def main() -> None:
    df, bounds = generate()
    scenarios = pick_scenarios(df, bounds)
    out_dir = ROOT / "data"
    out_dir.mkdir(exist_ok=True)
    df.to_csv(out_dir / "sample_btcusdt_1h.csv", index=False)
    (out_dir / "scenarios.json").write_text(
        json.dumps({"segments": bounds, "scenarios": scenarios}, indent=2), encoding="utf-8"
    )
    ind = compute_indicators(df)
    print(f"{len(df)} velas sintéticas escritas en data/sample_btcusdt_1h.csv")
    for name, info in scenarios.items():
        i = info["index"]
        r = ind.iloc[i]
        print(
            f"  escenario {name:9s} indice={i:5d} precio={r['close']:>10,.2f} "
            f"RSI={r['rsi14']:5.1f} EMA20={r['ema20']:>10,.2f} EMA50={r['ema50']:>10,.2f} %B={r['percent_b']:5.2f}"
        )


if __name__ == "__main__":
    main()
