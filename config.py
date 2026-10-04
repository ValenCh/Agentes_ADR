"""Configuración central del proyecto.

Todo lo que se puede ajustar (capital, límites de riesgo, modelo de lenguaje)
vive acá. `Settings()` usa solo valores por defecto (útil en tests);
`load_settings()` lee además el archivo `.env` y las variables de entorno.

SEGURIDAD: este proyecto NO define ni lee ninguna clave de exchange.
La única clave que existe es LLM_API_KEY (para el modelo de lenguaje).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    # --- Mercado / simulación -------------------------------------------
    symbol: str = "BTCUSDT"
    interval: str = "1h"
    initial_cash: float = 10_000.0     # USD virtuales
    fee_rate: float = 0.001            # comisión simulada 0,1 %
    candles_window: int = 200          # velas que ven las herramientas

    # --- Gestión de riesgo (las aplica el código, no el LLM) ------------
    max_trade_pct: float = 0.25        # máx. 25 % del capital total por operación
    max_btc_position: float = 0.10     # máx. BTC que se pueden tener a la vez
    stop_loss_pct: float = 0.05        # venta automática si pierde 5 % vs. entrada
    max_loss_pct: float = 0.20         # con -20 % del capital inicial: no más compras
    min_order_usd: float = 10.0        # orden mínima

    # --- Agente / LLM ----------------------------------------------------
    llm_provider: str = "api"          # "api" (LLM real) | "mock" (sin IA, solo pruebas)
    llm_api_key: str = ""
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    llm_model: str = "gemini-3.8-flash"
    llm_temperature: float | None = 0.2
    max_agent_steps: int = 6           # máx. llamadas al LLM por ciclo
    max_order_attempts: int = 2        # intentos de orden por ciclo (si se rechaza)

    # --- Datos y almacenamiento -----------------------------------------
    data_path: str = "data/sample_btcusdt_1h.csv"          # dataset para backtest / respaldo en vivo
    sample_data_path: str = "data/sample_btcusdt_1h.csv"   # dataset de los escenarios de demo (fijo)
    scenarios_path: str = "data/scenarios.json"
    db_path: str = "trading_agent.db"

    def resolve(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else ROOT / p


def _get(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip()


def load_settings() -> Settings:
    """Lee `.env` + variables de entorno y devuelve la configuración."""
    load_dotenv(ROOT / ".env")
    d = Settings()
    temp_raw = os.getenv("LLM_TEMPERATURE")
    if temp_raw is None:
        temperature = d.llm_temperature
    elif temp_raw.strip() == "":
        temperature = None  # no enviar temperatura (algunos modelos no la aceptan)
    else:
        temperature = float(temp_raw)
    return Settings(
        symbol=_get("SYMBOL", d.symbol),
        interval=_get("INTERVAL", d.interval),
        initial_cash=float(_get("INITIAL_CASH", str(d.initial_cash))),
        fee_rate=float(_get("FEE_RATE", str(d.fee_rate))),
        max_trade_pct=float(_get("MAX_TRADE_PCT", str(d.max_trade_pct))),
        max_btc_position=float(_get("MAX_BTC_POSITION", str(d.max_btc_position))),
        stop_loss_pct=float(_get("STOP_LOSS_PCT", str(d.stop_loss_pct))),
        max_loss_pct=float(_get("MAX_LOSS_PCT", str(d.max_loss_pct))),
        min_order_usd=float(_get("MIN_ORDER_USD", str(d.min_order_usd))),
        llm_provider=_get("LLM_PROVIDER", d.llm_provider).lower(),
        llm_api_key=_get("LLM_API_KEY", ""),
        llm_base_url=_get("LLM_BASE_URL", d.llm_base_url),
        llm_model=_get("LLM_MODEL", d.llm_model),
        llm_temperature=temperature,
        max_agent_steps=int(_get("MAX_AGENT_STEPS", str(d.max_agent_steps))),
        max_order_attempts=int(_get("MAX_ORDER_ATTEMPTS", str(d.max_order_attempts))),
        data_path=_get("DATA_PATH", d.data_path),
        scenarios_path=_get("SCENARIOS_PATH", d.scenarios_path),
        db_path=_get("DB_PATH", d.db_path),
    )
