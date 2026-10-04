"""Indicadores técnicos, calculados con pandas puro (sin librerías de TA).

Cada indicador aporta EVIDENCIA al agente; ninguno decide por sí solo:
- RSI(14): momento. Mide si las subidas recientes fueron más fuertes que las
  bajadas (0-100). Valores extremos sugieren "sobrecompra/sobreventa", pero en
  tendencias fuertes pueden persistir.
- EMA(20) y EMA(50): tendencia. Promedios móviles exponenciales (dan más peso
  a lo reciente). La relación entre ambas y con el precio describe la tendencia.
- Bandas de Bollinger(20, 2): volatilidad. SMA(20) +/- 2 desviaciones estándar.
  %B indica dónde está el precio dentro de la banda (0 = banda inferior,
  1 = banda superior).
"""
from __future__ import annotations

import pandas as pd

MIN_CANDLES = 60  # mínimo de velas para que EMA50/RSI sean confiables


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI con suavizado de Wilder."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    out = 100 - 100 / (1 + rs)
    out = out.mask((avg_loss == 0) & (avg_gain > 0), 100.0)  # solo subidas
    out = out.mask((avg_loss == 0) & (avg_gain == 0), 50.0)  # precio plano
    return out


def bollinger(series: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    mid = sma(series, period)
    std = series.rolling(period).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = upper - lower
    percent_b = (series - lower) / width.where(width != 0)
    return pd.DataFrame(
        {
            "sma20": mid,
            "bb_upper": upper,
            "bb_lower": lower,
            "percent_b": percent_b,
            "bandwidth_pct": width / mid * 100,
        }
    )


def compute_indicators(candles: pd.DataFrame) -> pd.DataFrame:
    """Devuelve una copia de `candles` con todas las columnas de indicadores."""
    if len(candles) < MIN_CANDLES:
        raise ValueError(
            f"Se necesitan al menos {MIN_CANDLES} velas para calcular indicadores "
            f"(hay {len(candles)})."
        )
    out = candles.copy()
    close = out["close"]
    out["rsi14"] = rsi(close, 14)
    out["ema20"] = ema(close, 20)
    out["ema50"] = ema(close, 50)
    out = pd.concat([out, bollinger(close, 20, 2.0)], axis=1)
    return out


def latest_snapshot(candles: pd.DataFrame) -> dict:
    """Indicadores de la última vela como diccionario simple (para el agente)."""
    df = compute_indicators(candles)
    last = df.iloc[-1]
    price = float(last["close"])
    ema20, ema50 = float(last["ema20"]), float(last["ema50"])
    lookback = min(24, len(df) - 1)
    ref = float(df["close"].iloc[-1 - lookback])
    return {
        "candle_time": pd.Timestamp(last["time"]).strftime("%Y-%m-%d %H:%M UTC"),
        "price": round(price, 2),
        "rsi14": round(float(last["rsi14"]), 2),
        "ema20": round(ema20, 2),
        "ema50": round(ema50, 2),
        "sma20": round(float(last["sma20"]), 2),
        "bb_upper": round(float(last["bb_upper"]), 2),
        "bb_lower": round(float(last["bb_lower"]), 2),
        "percent_b": round(float(last["percent_b"]), 3),
        "bandwidth_pct": round(float(last["bandwidth_pct"]), 2),
        "price_vs_ema20_pct": round((price / ema20 - 1) * 100, 2),
        "ema20_vs_ema50_pct": round((ema20 / ema50 - 1) * 100, 2),
        "change_last_24_candles_pct": round((price / ref - 1) * 100, 2),
    }
