"""Proveedores de datos de mercado (SOLO LECTURA de datos públicos).

Tres fuentes, todas con la misma interfaz mínima:
- ReplayProvider: reproduce un CSV vela por vela. Nunca muestra velas futuras
  (sin "look-ahead"), que es lo que hace confiable la simulación.
- BinancePublicProvider / fetch_klines: velas reales desde el endpoint público
  de datos de mercado de Binance. No usa claves ni firma nada.
- LiveProvider: pide velas reales y, si la red falla, cae a un ReplayProvider.

SEGURIDAD: este módulo solo puede hacer GET a una lista blanca de URLs de
datos públicos. No tiene ninguna función de trading, y el módulo de
ejecución simulada (simulation/) no importa nada de red.
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests

from market.indicators import MIN_CANDLES

log = logging.getLogger("market")

COLUMNS = ["time", "open", "high", "low", "close", "volume"]

# Lista blanca: solo endpoints públicos de datos de mercado.
ALLOWED_HOSTS = ("data-api.binance.vision", "api.binance.com")
ALLOWED_PATHS = ("/api/v3/klines",)
BASE_URLS = tuple(f"https://{h}" for h in ALLOWED_HOSTS)


class DataUnavailable(RuntimeError):
    """No se pudieron obtener datos de mercado."""


class SecurityError(RuntimeError):
    """Se intentó usar una URL fuera de la lista blanca de datos públicos."""


def assert_public_data_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.path not in ALLOWED_PATHS:
        raise SecurityError(
            f"URL no permitida: {url}. Solo se permiten datos públicos de mercado "
            f"en {ALLOWED_HOSTS} con rutas {ALLOWED_PATHS}."
        )


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------
def load_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise DataUnavailable(f"Al CSV {path} le faltan columnas: {missing}")
    df = df[COLUMNS].copy()
    df["time"] = pd.to_datetime(df["time"], utc=True)
    for col in COLUMNS[1:]:
        df[col] = pd.to_numeric(df[col])
    return df.sort_values("time").reset_index(drop=True)


class ReplayProvider:
    """Reproduce velas históricas una por una. `cursor` = última vela visible."""

    source_name = "csv-replay"
    using_fallback = False

    def __init__(self, candles: pd.DataFrame, start_index: int | None = None):
        if len(candles) < MIN_CANDLES + 1:
            raise DataUnavailable(f"El dataset es muy corto ({len(candles)} velas).")
        self._df = candles.reset_index(drop=True)
        start = MIN_CANDLES - 1 if start_index is None else start_index
        if not (MIN_CANDLES - 1 <= start < len(self._df)):
            raise DataUnavailable(
                f"start_index={start} inválido: debe estar entre {MIN_CANDLES - 1} y {len(self._df) - 1}."
            )
        self.cursor = int(start)

    @property
    def total(self) -> int:
        return len(self._df)

    def get_candles(self, n: int = 200) -> pd.DataFrame:
        lo = max(0, self.cursor - n + 1)
        return self._df.iloc[lo : self.cursor + 1].reset_index(drop=True)

    @property
    def latest_price(self) -> float:
        return float(self._df["close"].iloc[self.cursor])

    @property
    def current_time(self) -> pd.Timestamp:
        return self._df["time"].iloc[self.cursor]

    def can_advance(self, k: int = 1) -> bool:
        return self.cursor + k < len(self._df)

    def advance(self, k: int = 1) -> bool:
        if not self.can_advance(k):
            return False
        self.cursor += k
        return True


# --------------------------------------------------------------------------
# Binance (datos públicos)
# --------------------------------------------------------------------------
def fetch_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    limit: int = 200,
    start_ms: int | None = None,
    end_ms: int | None = None,
    http_get=requests.get,
    timeout: float = 10.0,
) -> pd.DataFrame:
    """Descarga velas públicas. Prueba cada URL base hasta que una responda."""
    params: dict = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_ms is not None:
        params["startTime"] = int(start_ms)
    if end_ms is not None:
        params["endTime"] = int(end_ms)
    last_error: Exception | None = None
    for base in BASE_URLS:
        url = f"{base}/api/v3/klines"
        assert_public_data_url(url)
        try:
            resp = http_get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            rows = resp.json()
            if not rows:
                raise DataUnavailable("La API devolvió una lista vacía.")
            df = pd.DataFrame(
                [[r[0], r[1], r[2], r[3], r[4], r[5]] for r in rows],
                columns=COLUMNS,
            )
            df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
            for col in COLUMNS[1:]:
                df[col] = pd.to_numeric(df[col])
            return df
        except Exception as exc:  # noqa: BLE001 - probamos la siguiente URL
            last_error = exc
            log.warning("Fallo al pedir velas a %s: %s", base, exc)
    raise DataUnavailable(f"No se pudieron descargar velas: {last_error}")


class LiveProvider:
    """Velas reales recientes. Si la API falla, usa un proveedor de respaldo."""

    def __init__(self, symbol, interval, window, fallback: ReplayProvider, fetcher=fetch_klines):
        self.symbol, self.interval, self.window = symbol, interval, window
        self._fallback = fallback
        self._fetcher = fetcher
        self._df: pd.DataFrame | None = None
        self.using_fallback = False
        self.source_name = "binance-public"
        self.refresh()

    def refresh(self) -> None:
        try:
            self._df = self._fetcher(self.symbol, self.interval, self.window)
            self.using_fallback = False
            self.source_name = "binance-public"
        except Exception as exc:  # noqa: BLE001
            log.warning("API no disponible (%s). Usando datos locales de respaldo.", exc)
            self._df = self._fallback.get_candles(self.window)
            self.using_fallback = True
            self.source_name = "csv-fallback"

    def get_candles(self, n: int = 200) -> pd.DataFrame:
        return self._df.tail(n).reset_index(drop=True)

    @property
    def latest_price(self) -> float:
        return float(self._df["close"].iloc[-1])

    @property
    def current_time(self) -> pd.Timestamp:
        return self._df["time"].iloc[-1]

    def can_advance(self, k: int = 1) -> bool:
        return True

    def advance(self, k: int = 1) -> bool:
        self.refresh()
        return True
