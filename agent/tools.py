"""Herramientas del agente (function calling).

Cada método de `ToolBox` es una HERRAMIENTA que el LLM puede pedir. El modelo
no ve precios ni saldos hasta que llama a la herramienta correspondiente: así
todos los datos vienen del sistema y no de la "memoria" del modelo.

Herramientas de lectura: get_market_data, calculate_indicators, get_portfolio,
calculate_risk, get_trade_history.
Herramienta de acción: execute_virtual_order (valida, verifica, aplica riesgo
y ejecuta en el broker simulado).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from agent.grounding import check_grounding
from agent.schemas import CITABLE_KEYS, Action, TradeDecision, format_validation_errors
from config import Settings
from market.indicators import latest_snapshot
from simulation.broker import Fill, SimulatedBroker
from simulation.portfolio import Portfolio
from simulation.risk import RiskManager, RiskVerdict

log = logging.getLogger("agent")

DATA_TOOLS = ("get_market_data", "calculate_indicators")


@dataclass
class ToolContext:
    """Estado del entorno al que las herramientas tienen acceso en un ciclo."""

    settings: Settings
    provider: Any
    portfolio: Portfolio
    risk: RiskManager
    broker: SimulatedBroker
    db: Any = None
    run_id: str = ""
    step: int = 0
    # --- se completan durante el ciclo ---
    facts: dict[str, float] = field(default_factory=dict)   # cifras reales vistas por el agente
    consulted: set[str] = field(default_factory=set)
    tool_trace: list[dict] = field(default_factory=list)
    attempts: int = 0
    finished: bool = False
    final_status: str | None = None   # EXECUTED | HOLD | REJECTED | INVALID | UNGROUNDED | NOT_CONSULTED
    last_errors: list[str] = field(default_factory=list)
    decision: TradeDecision | None = None
    verdict: RiskVerdict | None = None
    fill: Fill | None = None

    @property
    def price(self) -> float:
        return self.provider.latest_price


TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_market_data",
            "description": "Precio actual de BTCUSDT, variación reciente y las últimas velas OHLCV. "
                           "Es la única fuente válida del precio.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_indicators",
            "description": "Indicadores técnicos calculados por el sistema: RSI14, EMA20, EMA50, SMA20, "
                           "Bandas de Bollinger (superior/inferior, %B, ancho) y distancias relativas.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_portfolio",
            "description": "Estado de la cartera virtual: saldo en USD, BTC en posesión, precio medio de entrada, "
                           "ganancia/pérdida realizada y no realizada, valor total.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_risk",
            "description": "Límites de riesgo vigentes y márgenes disponibles: máximo comprable (max_buy_btc), "
                           "máximo vendible (max_sell_btc), stop-loss, si las compras están bloqueadas.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_trade_history",
            "description": "Decisiones anteriores del agente y cómo evolucionó el precio desde entonces "
                           "(para observar el resultado de lo decidido antes).",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "Cuántas decisiones traer (1-20, por defecto 5)."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_virtual_order",
            "description": "ACCIÓN FINAL del ciclo. Registra tu decisión (BUY, SELL o HOLD). El sistema la valida, "
                           "verifica que las cifras citadas coincidan con las herramientas, aplica el control de "
                           "riesgo y, si corresponde, ejecuta la orden SIMULADA. Con HOLD, amount = 0.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
                    "asset": {"type": "string", "description": "Par, p. ej. BTCUSDT"},
                    "amount": {"type": "number", "description": "Cantidad en BTC (no en USD). 0 si HOLD."},
                    "confidence": {"type": "number", "description": "Confianza entre 0 y 1."},
                    "reason": {"type": "string", "description": "Razonamiento en español, 1-4 frases, citando cifras de las herramientas."},
                    "cited_values": {
                        "type": "object",
                        "description": "Cifras clave EXACTAS devueltas por las herramientas en las que basas la decisión "
                                       "(obligatorio para BUY/SELL).",
                        "properties": {k: {"type": "number"} for k in CITABLE_KEYS},
                    },
                },
                "required": ["action", "asset", "amount", "confidence", "reason"],
            },
        },
    },
]


class ToolBox:
    def __init__(self, ctx: ToolContext):
        self.ctx = ctx
        self._registry = {
            "get_market_data": self.get_market_data,
            "calculate_indicators": self.calculate_indicators,
            "get_portfolio": self.get_portfolio,
            "calculate_risk": self.calculate_risk,
            "get_trade_history": self.get_trade_history,
            "execute_virtual_order": self.execute_virtual_order,
        }

    @staticmethod
    def schemas() -> list[dict]:
        return TOOL_SCHEMAS

    def call(self, name: str, arguments: dict | None) -> dict:
        log.info("Agente solicitó herramienta: %s", name)
        arguments = arguments or {}
        fn = self._registry.get(name)
        if "_invalid_json" in arguments:
            result = {"error": "Los argumentos no son un JSON válido. Vuelve a llamar con un objeto JSON correcto."}
        elif fn is None:
            result = {"error": f"Herramienta desconocida: {name}. Disponibles: {', '.join(self._registry)}"}
        else:
            try:
                result = fn(**arguments)
            except Exception as exc:  # noqa: BLE001 - el agente debe ver el error, no el sistema caerse
                log.warning("Error en herramienta %s: %s", name, exc)
                result = {"error": f"{type(exc).__name__}: {exc}"}
        self.ctx.tool_trace.append({"tool": name, "args": arguments, "result": result})
        return result

    # ------------------------------------------------------------------ lectura
    def get_market_data(self, **_ignored) -> dict:
        ctx = self.ctx
        candles = ctx.provider.get_candles(ctx.settings.candles_window)
        last = candles.iloc[-1]
        price = float(last["close"])
        ref_idx = max(0, len(candles) - 25)
        ref = float(candles["close"].iloc[ref_idx])
        last24 = candles.tail(24)
        recent = [
            {"time": r.time.strftime("%Y-%m-%d %H:%M"), "open": round(r.open, 2), "high": round(r.high, 2),
             "low": round(r.low, 2), "close": round(r.close, 2), "volume": round(r.volume, 3)}
            for r in candles.tail(6).itertuples()
        ]
        ctx.facts["price"] = round(price, 2)
        ctx.consulted.add("get_market_data")
        result = {
            "symbol": ctx.settings.symbol,
            "interval": ctx.settings.interval,
            "data_source": ctx.provider.source_name,
            "candle_time": last["time"].strftime("%Y-%m-%d %H:%M UTC"),
            "price": round(price, 2),
            "high_last_24_candles": round(float(last24["high"].max()), 2),
            "low_last_24_candles": round(float(last24["low"].min()), 2),
            "change_last_24_candles_pct": round((price / ref - 1) * 100, 2),
            "last_6_candles": recent,
        }
        log.info("  Precio %s = %s (fuente: %s)", ctx.settings.symbol, f"{price:,.2f}", ctx.provider.source_name)
        return result

    def calculate_indicators(self, **_ignored) -> dict:
        ctx = self.ctx
        snap = latest_snapshot(ctx.provider.get_candles(ctx.settings.candles_window))
        for key in ("price", "rsi14", "ema20", "ema50", "sma20", "bb_upper", "bb_lower", "percent_b"):
            ctx.facts[key] = snap[key]
        ctx.consulted.add("calculate_indicators")
        log.info("  RSI14 = %s | EMA20 = %s | EMA50 = %s | %%B = %s",
                 snap["rsi14"], f"{snap['ema20']:,.2f}", f"{snap['ema50']:,.2f}", snap["percent_b"])
        return snap

    def get_portfolio(self, **_ignored) -> dict:
        ctx = self.ctx
        d = ctx.portfolio.to_dict(ctx.price)
        ctx.facts.update(
            {"cash_usd": d["cash_usd"], "btc": d["btc"], "equity_usd": d["equity_usd"],
             "unrealized_pnl_pct": d["unrealized_pnl_pct"]}
        )
        ctx.consulted.add("get_portfolio")
        log.info("  Cartera: %s USD + %s BTC = %s USD", f"{d['cash_usd']:,.2f}", d["btc"], f"{d['equity_usd']:,.2f}")
        return d

    def calculate_risk(self, **_ignored) -> dict:
        ctx = self.ctx
        snap = ctx.risk.snapshot(ctx.portfolio, ctx.price)
        ctx.facts.update({"max_buy_btc": snap["max_buy_btc"], "max_sell_btc": snap["max_sell_btc"],
                          "equity_usd": snap["equity_usd"]})
        ctx.consulted.add("calculate_risk")
        log.info("  Riesgo: max_buy_btc = %s | max_sell_btc = %s | compras bloqueadas = %s",
                 snap["max_buy_btc"], snap["max_sell_btc"], snap["buys_blocked"])
        return snap

    def get_trade_history(self, limit: int = 5, **_ignored) -> dict:
        ctx = self.ctx
        try:
            limit = max(1, min(int(limit), 20))
        except (TypeError, ValueError):
            limit = 5
        entries: list[dict] = []
        if ctx.db is not None and ctx.run_id:
            rows = ctx.db.list_decisions(ctx.run_id, include_skipped=False, limit=limit, newest_first=True)
            for r in rows:
                then = r.get("price")
                change = round((ctx.price / then - 1) * 100, 2) if then else None
                entries.append({
                    "step": r["step"], "candle_time": r["candle_time"], "source": r["source"],
                    "action": r["action"], "amount_btc": r["amount"], "price_then": then,
                    "executed": bool(r["executed"]), "risk_status": r["risk_status"],
                    "realized_pnl_usd": r["realized_pnl"],
                    "price_change_since_then_pct": change,
                    "reason": (r["reason"] or "")[:160],
                })
        ctx.consulted.add("get_trade_history")
        log.info("  Historial: %d decisiones previas", len(entries))
        return {
            "previous_decisions_newest_first": entries,
            "realized_pnl_usd": round(ctx.portfolio.realized_pnl, 2),
            "total_return_pct": round(ctx.portfolio.total_return_pct(ctx.price), 2),
            "note": "Sin decisiones previas." if not entries else "price_change_since_then_pct = cómo se movió el precio desde esa decisión.",
        }

    # ------------------------------------------------------------------- acción
    def execute_virtual_order(self, **args) -> dict:
        ctx = self.ctx
        ctx.attempts += 1
        attempts_left = max(ctx.settings.max_order_attempts - ctx.attempts, 0)

        def reject(status: str, errors: list[str], hint: str) -> dict:
            ctx.final_status = status
            ctx.last_errors = errors
            if attempts_left == 0:
                ctx.finished = True
            log.info("  Decisión RECHAZADA (%s): %s", status, "; ".join(errors))
            return {"status": status, "errors": errors, "attempts_left": attempts_left, "hint": hint}

        # 1) Validación estructural (Pydantic)
        try:
            decision = TradeDecision.model_validate(args)
        except ValidationError as exc:
            return reject("INVALID", format_validation_errors(exc),
                          "Corrige el formato y vuelve a llamar a execute_virtual_order.")
        if decision.asset != ctx.settings.symbol:
            return reject("INVALID", [f"asset debe ser {ctx.settings.symbol}, recibido {decision.asset}."],
                          "Usa el par correcto.")

        action = decision.action.value
        log.info("Decisión del agente: %s %s BTC (confianza %.2f)", action, f"{decision.amount:.8f}", decision.confidence)
        log.info("  Motivo: %s", decision.reason)

        # 2) Para operar hay que haber consultado mercado/indicadores y cartera
        if decision.action != Action.HOLD:
            missing = []
            if not (ctx.consulted & set(DATA_TOOLS)):
                missing.append("get_market_data o calculate_indicators")
            if "get_portfolio" not in ctx.consulted:
                missing.append("get_portfolio")
            if missing:
                return reject("NOT_CONSULTED", [f"Antes de operar debes consultar: {', '.join(missing)}."],
                              "Llama a esas herramientas y luego vuelve a decidir.")

        # 3) Control de alucinaciones: lo citado debe coincidir con las herramientas
        problems = check_grounding(decision.cited_values, ctx.facts)
        if problems:
            return reject("UNGROUNDED", problems, "Rehace la decisión usando SOLO cifras devueltas por las herramientas.")

        # 4) Validación de riesgo (código determinista)
        price = ctx.price
        verdict = ctx.risk.validate(action, decision.amount, ctx.portfolio, price)
        log.info("Validación de riesgo: %s%s", verdict.status, f" ({'; '.join(verdict.notes)})" if verdict.notes else "")
        ctx.decision, ctx.verdict = decision, verdict
        if not verdict.approved:
            ctx.final_status = "REJECTED"
            if attempts_left == 0:
                ctx.finished = True
            return {"status": "REJECTED_BY_RISK", "risk": verdict.to_dict(), "attempts_left": attempts_left,
                    "hint": "Ajusta la orden a los límites (mira calculate_risk) o decide HOLD."}

        # 5) Ejecución simulada
        if action == "HOLD":
            ctx.final_status, ctx.finished = "HOLD", True
            return {"status": "HOLD_RECORDED", "risk": verdict.to_dict()}
        fill = ctx.broker.execute(ctx.portfolio, action, verdict.amount, price)
        ctx.fill = fill
        if not fill.executed:  # no debería ocurrir: el riesgo ya validó
            ctx.final_status = "REJECTED"
            ctx.verdict = RiskVerdict("REJECTED", 0.0, [fill.message])
            ctx.finished = attempts_left == 0
            return {"status": "REJECTED_BY_BROKER", "message": fill.message, "attempts_left": attempts_left}
        ctx.final_status, ctx.finished = "EXECUTED", True
        log.info("Orden simulada ejecutada: %s", fill.message)
        return {"status": "EXECUTED", "risk": verdict.to_dict(), "fill": fill.to_dict(),
                "portfolio_after": ctx.portfolio.to_dict(price)}
