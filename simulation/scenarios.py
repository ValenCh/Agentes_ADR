"""Escenarios de demostración reproducibles.

Cada escenario fija un punto del dataset histórico (data/scenarios.json,
generado por scripts/make_sample_data.py) y una cartera inicial. Lo que queda
controlado es la SITUACIÓN DE MERCADO; la decisión la toma el agente, así que
con un LLM real puede variar levemente entre ejecuciones (temperatura baja).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from config import Settings
from market.data_provider import DataUnavailable
from simulation.portfolio import Portfolio


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    description: str
    watch: str            # qué observar en la exposición
    start_index: int
    steps: int
    stride: int           # velas que avanza entre ciclo y ciclo
    position_btc: float   # BTC con los que arranca la cartera
    entry_factor: float   # precio de entrada = precio inicial * entry_factor


META = {
    "dip": dict(
        title="Caída fuerte: ¿oportunidad o cuchillo cayendo?",
        description="El precio viene de una corrección brusca: RSI extremadamente bajo y precio en la banda inferior, "
                    "pero con EMA20 por debajo de EMA50. La cartera está 100% en efectivo.",
        watch="Cómo pesa la señal de sobreventa contra la tendencia bajista. Cuánto compra y con qué confianza.",
        steps=3, stride=2, position_btc=0.0, entry_factor=1.0),
    "rally": dict(
        title="Rally con posición en ganancia",
        description="Fuerte subida: RSI muy alto y precio cerca de la banda superior. La cartera ya tiene 0,06 BTC "
                    "comprados un 8% más abajo.",
        watch="Si decide tomar ganancias, vender parcial o mantener. Cómo cita su ganancia no realizada.",
        steps=3, stride=3, position_btc=0.06, entry_factor=0.92),
    "sideways": dict(
        title="Mercado lateral sin señal clara",
        description="RSI neutro, precio dentro de las bandas y medias entrelazadas. Cartera 100% en efectivo.",
        watch="Que sepa no operar: HOLD justificado, sin sobre-operar.",
        steps=3, stride=3, position_btc=0.0, entry_factor=1.0),
    "crash": dict(
        title="Desplome: la seguridad en capas",
        description="Comienza tranquilo y luego el precio se derrumba. La cartera tiene 0,06 BTC comprados al precio "
                    "inicial, así que el stop-loss automático puede activarse.",
        watch="Qué decide el agente mientras cae y cómo el stop-loss (código) actúa aunque el agente no lo haga.",
        steps=6, stride=3, position_btc=0.06, entry_factor=1.0),
}


def load_scenarios(settings: Settings) -> dict[str, Scenario]:
    path = settings.resolve(settings.scenarios_path)
    if not path.exists():
        raise DataUnavailable(
            f"No existe {path}. Genera los datos de ejemplo con: python scripts/make_sample_data.py"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))["scenarios"]
    out = {}
    for name, meta in META.items():
        if name in raw:
            out[name] = Scenario(name=name, start_index=int(raw[name]["index"]), **meta)
    return out


def build_portfolio(sc: Scenario, price: float, initial_cash: float) -> Portfolio:
    if sc.position_btc <= 0:
        return Portfolio.new(initial_cash)
    entry = price * sc.entry_factor
    cash = initial_cash - sc.position_btc * entry
    return Portfolio.with_position(cash=cash, btc=sc.position_btc, entry_price=entry, price_now=price)
