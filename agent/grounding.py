"""Control de alucinaciones: verificar lo que el agente cita contra lo que dijeron las herramientas.

El agente debe adjuntar `cited_values` (las cifras en las que basó su decisión).
Este módulo compara cada cifra con el valor real que devolvió una herramienta
en ESTE ciclo. Si el modelo cita un precio o un RSI inventado, o cita algo que
nunca consultó, la decisión se rechaza y el agente recibe el motivo.

Es código determinista: no usa IA para vigilar a la IA.
"""
from __future__ import annotations

from agent.schemas import CITABLE_KEYS

# (tolerancia relativa, tolerancia absoluta). El valor se acepta si
# |citado - real| <= max(abs, rel * |real|). Permite redondeos razonables.
TOLERANCE: dict[str, tuple[float, float]] = {
    "price": (0.005, 0.5),
    "ema20": (0.005, 0.5),
    "ema50": (0.005, 0.5),
    "sma20": (0.005, 0.5),
    "bb_upper": (0.005, 0.5),
    "bb_lower": (0.005, 0.5),
    "cash_usd": (0.005, 0.5),
    "equity_usd": (0.005, 0.5),
    "rsi14": (0.0, 1.0),
    "percent_b": (0.0, 0.05),
    "btc": (0.02, 1e-6),
    "max_buy_btc": (0.02, 1e-6),
    "max_sell_btc": (0.02, 1e-6),
    "unrealized_pnl_pct": (0.0, 0.3),
}


def check_grounding(cited: dict[str, float], facts: dict[str, float]) -> list[str]:
    """Devuelve la lista de problemas (vacía si todo está respaldado por herramientas)."""
    problems: list[str] = []
    for key, value in cited.items():
        if key not in CITABLE_KEYS:
            continue  # claves desconocidas se ignoran (no se pueden verificar)
        if key not in facts:
            problems.append(
                f"Citaste {key}={value} pero no lo obtuviste de ninguna herramienta en este ciclo. "
                "Consulta la herramienta correspondiente antes de usar esa cifra."
            )
            continue
        real = facts[key]
        rel, abs_tol = TOLERANCE[key]
        if abs(value - real) > max(abs_tol, rel * abs(real)):
            problems.append(
                f"{key}: citaste {value} pero la herramienta devolvió {real}. "
                "No inventes ni recuerdes cifras: usa exactamente las de las herramientas."
            )
    return problems
